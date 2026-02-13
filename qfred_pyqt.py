"""
Q-fred - Smart Snippet Manager for Windows (PyQt6 Version)
시스템 전역에서 단축어를 감지하고 치환하는 프로그램
"""

import json
import os
import sys
import threading
import time

# PyInstaller frozen exe: Qt 플러그인 경로 설정
if getattr(sys, 'frozen', False):
    base_path = sys._MEIPASS
    os.environ['QT_PLUGIN_PATH'] = os.path.join(base_path, 'PyQt6', 'Qt6', 'plugins')
import pyperclip
import yt_dlp
import uuid
import ctypes
import ctypes.wintypes
import winreg
import urllib.request
import subprocess
import tempfile
from datetime import datetime
from pynput import keyboard as pynput_keyboard
from pynput.keyboard import Key, Controller

# 앱 버전
APP_VERSION = "1.0.24"
APP_NAME = "Q-fred"
GITHUB_REPO = "dumock/Qfred"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

# 콘솔 창 숨기기
def hide_console():
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE = 0
    except:
        pass

hide_console()

# --- Windows SendInput 직접 호출 (pynput 우회) ---
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
VK_BACK = 0x08
VK_CONTROL = 0x11
VK_SHIFT = 0x10
VK_INSERT = 0x2D
VK_V = 0x56

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ('wVk', ctypes.c_ushort),
        ('wScan', ctypes.c_ushort),
        ('dwFlags', ctypes.c_ulong),
        ('time', ctypes.c_ulong),
        ('dwExtraInfo', ctypes.POINTER(ctypes.c_ulong)),
    ]

class INPUT(ctypes.Structure):
    class _INPUT(ctypes.Union):
        _fields_ = [('ki', KEYBDINPUT), ('padding', ctypes.c_byte * 32)]
    _anonymous_ = ('_input',)
    _fields_ = [('type', ctypes.c_ulong), ('_input', _INPUT)]

def _send_key(vk, flags=0):
    """키 이벤트 하나를 SendInput으로 전송"""
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.ki.wVk = vk
    inp.ki.dwFlags = flags
    arr = (INPUT * 1)(inp)
    return ctypes.windll.user32.SendInput(1, arr, ctypes.sizeof(INPUT))

def _make_input(vk, flags=0):
    """INPUT 구조체 생성"""
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.ki.wVk = vk
    inp.ki.dwFlags = flags
    return inp

def send_backspaces(count):
    """백스페이스를 count번 전송 (한 번에 down+up 원자적)"""
    for _ in range(count):
        arr = (INPUT * 2)(_make_input(VK_BACK), _make_input(VK_BACK, KEYEVENTF_KEYUP))
        ctypes.windll.user32.SendInput(2, arr, ctypes.sizeof(INPUT))
        time.sleep(0.02)

KEYEVENTF_UNICODE = 0x0004

def send_paste():
    """Ctrl+V로 붙여넣기"""
    arr = (INPUT * 4)(
        _make_input(VK_CONTROL),
        _make_input(VK_V),
        _make_input(VK_V, KEYEVENTF_KEYUP),
        _make_input(VK_CONTROL, KEYEVENTF_KEYUP),
    )
    ctypes.windll.user32.SendInput(4, arr, ctypes.sizeof(INPUT))

KEYEVENTF_EXTENDEDKEY = 0x0001

def send_paste_shift_insert():
    """Shift+Insert로 붙여넣기 (콘솔용, Insert에 EXTENDEDKEY 플래그 포함)"""
    arr = (INPUT * 4)(
        _make_input(VK_SHIFT),
        _make_input(VK_INSERT, KEYEVENTF_EXTENDEDKEY),
        _make_input(VK_INSERT, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP),
        _make_input(VK_SHIFT, KEYEVENTF_KEYUP),
    )
    ctypes.windll.user32.SendInput(4, arr, ctypes.sizeof(INPUT))

def is_console_window():
    """포그라운드 윈도우가 콘솔/터미널인지 감지 (클래스명 + 프로세스명)"""
    hwnd = ctypes.windll.user32.GetForegroundWindow()

    # 1차: 윈도우 클래스명 체크
    class_name = ctypes.create_unicode_buffer(256)
    ctypes.windll.user32.GetClassNameW(hwnd, class_name, 256)
    name = class_name.value.lower()
    _debug_console_info(name, "")  # 디버그
    if ('console' in name or 'terminal' in name
            or 'cascadia' in name or 'mintty' in name
            or 'cmd' in name or 'powershell' in name):
        return True

    # 2차: 프로세스명 체크 (Windows Terminal 등 WinUI3 기반)
    try:
        pid = ctypes.wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if handle:
            exe_buf = ctypes.create_unicode_buffer(260)
            size = ctypes.wintypes.DWORD(260)
            ctypes.windll.kernel32.QueryFullProcessImageNameW(
                handle, 0, exe_buf, ctypes.byref(size))
            ctypes.windll.kernel32.CloseHandle(handle)
            proc = os.path.basename(exe_buf.value).lower()
            _debug_console_info(name, proc)  # 디버그
            if proc in ('windowsterminal.exe', 'cmd.exe', 'powershell.exe',
                        'pwsh.exe', 'conhost.exe', 'bash.exe', 'wsl.exe',
                        'mintty.exe', 'alacritty.exe', 'wezterm-gui.exe',
                        'hyper.exe', 'code.exe', 'antigravity.exe'):
                return True
    except:
        pass

    return False

def _debug_console_info(class_name, proc_name):
    """콘솔 감지 디버그 로그"""
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '_debug.txt'), 'a', encoding='utf-8') as f:
            f.write(f"CONSOLE_DETECT: class='{class_name}', proc='{proc_name}'\n")
    except:
        pass


# IMM32 for IME control (트리거 입력 필드 한/영 전환)
try:
    ctypes.windll.imm32.ImmAssociateContext.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    ctypes.windll.imm32.ImmAssociateContext.restype = ctypes.c_void_p
except:
    pass

def send_unicode_string(text):
    """SendInput + KEYEVENTF_UNICODE로 문자열 원자적 전송 (클립보드 불필요, 한번에 출력)"""
    events = []
    for char in text:
        if char == '\n':
            events.append(_make_input(0x0D))
            events.append(_make_input(0x0D, KEYEVENTF_KEYUP))
        else:
            down = INPUT()
            down.type = INPUT_KEYBOARD
            down.ki.wVk = 0
            down.ki.wScan = ord(char)
            down.ki.dwFlags = KEYEVENTF_UNICODE
            events.append(down)

            up = INPUT()
            up.type = INPUT_KEYBOARD
            up.ki.wVk = 0
            up.ki.wScan = ord(char)
            up.ki.dwFlags = KEYEVENTF_UNICODE | KEYEVENTF_KEYUP
            events.append(up)

    if events:
        arr = (INPUT * len(events))(*events)
        ctypes.windll.user32.SendInput(len(events), arr, ctypes.sizeof(INPUT))

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QTextEdit, QPushButton, QListWidget, QListWidgetItem,
    QFrame, QScrollArea, QSystemTrayIcon, QMenu, QSplitter, QMessageBox,
    QSizePolicy, QStackedWidget, QSpacerItem, QDialog, QFileDialog, QCheckBox,
    QComboBox, QProgressBar, QGridLayout, QSlider
)
from PyQt6.QtCore import Qt, QSize, pyqtSignal, QObject, QTimer, QEvent, QThread, QPoint
from PyQt6.QtGui import (
    QIcon, QPixmap, QFont, QColor, QPalette, QAction, QFontDatabase, QCursor,
    QImage, QPainter, QPen, QBrush
)

# 설정 파일 경로
# PyInstaller exe로 실행 시 exe 파일 위치, 스크립트 실행 시 스크립트 위치 사용
if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(sys.executable)
    RESOURCE_DIR = sys._MEIPASS
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
    RESOURCE_DIR = APP_DIR

# 기본 저장 폴더: %APPDATA%\Qfred
DEFAULT_STORAGE_FOLDER = os.path.join(os.environ.get('APPDATA', APP_DIR), 'Qfred')
APP_SETTINGS_FILE = os.path.join(APP_DIR, "app_settings.json")


class AppSettings:
    """앱 설정 관리 클래스"""

    DEFAULT_DOWNLOAD_FOLDER = os.path.join(os.path.expanduser('~'), 'Downloads')

    def __init__(self):
        self._settings = {
            'start_with_windows': False,
            'start_minimized': False,
            'storage_folder': DEFAULT_STORAGE_FOLDER,
            'download_folder': self.DEFAULT_DOWNLOAD_FOLDER,
            'download_groups': [
                {"name": "General", "folder": ""},
                {"name": "YouTube", "folder": "YouTube"},
                {"name": "Music", "folder": "Music"},
            ],
            'default_format': 'video',
        }
        self.load()

    def load(self):
        """설정 파일 로드"""
        if os.path.exists(APP_SETTINGS_FILE):
            try:
                with open(APP_SETTINGS_FILE, 'r', encoding='utf-8') as f:
                    saved = json.load(f)
                    self._settings.update(saved)
            except:
                pass

    def save(self):
        """설정 파일 저장"""
        with open(APP_SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(self._settings, f, ensure_ascii=False, indent=2)

    @property
    def start_with_windows(self) -> bool:
        return self._settings.get('start_with_windows', False)

    @start_with_windows.setter
    def start_with_windows(self, value: bool):
        self._settings['start_with_windows'] = value
        self.save()
        self._update_startup_registry(value)

    @property
    def start_minimized(self) -> bool:
        return self._settings.get('start_minimized', False)

    @start_minimized.setter
    def start_minimized(self, value: bool):
        self._settings['start_minimized'] = value
        self.save()

    @property
    def storage_folder(self) -> str:
        return self._settings.get('storage_folder', DEFAULT_STORAGE_FOLDER)

    @storage_folder.setter
    def storage_folder(self, value: str):
        self._settings['storage_folder'] = value
        self.save()

    @property
    def snippets_file(self) -> str:
        folder = self.storage_folder
        os.makedirs(folder, exist_ok=True)
        return os.path.join(folder, "snippets.json")

    @property
    def download_folder(self) -> str:
        return self._settings.get('download_folder', self.DEFAULT_DOWNLOAD_FOLDER)

    @download_folder.setter
    def download_folder(self, value: str):
        self._settings['download_folder'] = value
        self.save()

    @property
    def download_groups(self) -> list:
        return self._settings.get('download_groups', [{"name": "General", "folder": ""}])

    @download_groups.setter
    def download_groups(self, value: list):
        self._settings['download_groups'] = value
        self.save()

    @property
    def default_format(self) -> str:
        return self._settings.get('default_format', 'video')

    @default_format.setter
    def default_format(self, value: str):
        self._settings['default_format'] = value
        self.save()

    def get_download_path(self, group_name: str = "") -> str:
        """그룹에 맞는 다운로드 경로 반환"""
        base = self.download_folder
        for g in self.download_groups:
            if g["name"] == group_name and g["folder"]:
                path = os.path.join(base, g["folder"])
                os.makedirs(path, exist_ok=True)
                return path
        os.makedirs(base, exist_ok=True)
        return base

    def _update_startup_registry(self, enable: bool):
        """Windows 시작 프로그램 레지스트리 등록/해제"""
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
            if enable:
                # exe 경로 가져오기
                if getattr(sys, 'frozen', False):
                    exe_path = sys.executable
                else:
                    exe_path = f'"{sys.executable}" "{os.path.abspath(__file__)}"'
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, exe_path)
                print(f"[AppSettings] 시작 프로그램 등록: {exe_path}")
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                    print("[AppSettings] 시작 프로그램 해제")
                except FileNotFoundError:
                    pass
            winreg.CloseKey(key)
        except Exception as e:
            print(f"[AppSettings] 레지스트리 오류: {e}")

    def is_registered_startup(self) -> bool:
        """시작 프로그램에 등록되어 있는지 확인"""
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ)
            try:
                winreg.QueryValueEx(key, APP_NAME)
                winreg.CloseKey(key)
                return True
            except FileNotFoundError:
                winreg.CloseKey(key)
                return False
        except:
            return False


def check_for_updates() -> tuple[bool, str, str]:
    """GitHub Releases에서 최신 버전 확인
    Returns: (업데이트 있음 여부, 최신 버전, exe 다운로드 URL)
    """
    try:
        req = urllib.request.Request(
            GITHUB_API_URL,
            headers={'User-Agent': 'Q-fred Update Checker', 'Accept': 'application/vnd.github.v3+json'}
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
            tag = data.get('tag_name', '')
            latest_version = tag.lstrip('v')

            if not latest_version:
                return False, "", ""

            # 버전 비교 (숫자 튜플로 비교)
            def parse_version(v):
                try:
                    return tuple(int(x) for x in v.split('.'))
                except:
                    return (0,)

            if parse_version(latest_version) > parse_version(APP_VERSION):
                # assets에서 .exe 파일 찾기
                download_url = ""
                for asset in data.get('assets', []):
                    if asset['name'].lower().endswith('.exe'):
                        download_url = asset['browser_download_url']
                        break
                return True, latest_version, download_url

        return False, APP_VERSION, ""
    except Exception as e:
        print(f"[UpdateChecker] 오류: {e}")
        return False, "", ""


def download_update(download_url: str, progress_callback=None) -> str:
    """새 버전 exe 다운로드
    Returns: 다운로드된 파일 경로 (실패 시 빈 문자열)
    """
    try:
        app_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
        update_path = os.path.join(app_dir, "Qfred_update.exe")

        req = urllib.request.Request(download_url, headers={'User-Agent': 'Q-fred Update Checker'})
        with urllib.request.urlopen(req, timeout=120) as response:
            total_size = int(response.headers.get('Content-Length', 0))
            downloaded = 0
            block_size = 8192

            with open(update_path, 'wb') as f:
                while True:
                    chunk = response.read(block_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback and total_size > 0:
                        progress_callback(int(downloaded * 100 / total_size))

        return update_path
    except Exception as e:
        print(f"[UpdateDownload] 오류: {e}")
        return ""


def apply_update(update_path: str):
    """batch 스크립트로 exe 교체 후 재시작"""
    if getattr(sys, 'frozen', False):
        current_exe = sys.executable
    else:
        # 스크립트 모드에서는 교체 불필요
        print("[Update] 스크립트 모드에서는 자동 교체를 지원하지 않습니다.")
        return

    app_dir = os.path.dirname(current_exe)
    bat_path = os.path.join(app_dir, "_update.bat")
    exe_name = os.path.basename(current_exe)
    update_name = os.path.basename(update_path)

    bat_content = f'''@echo off
chcp 65001 >nul
echo Q-fred 업데이트 중...
timeout /t 2 /nobreak >nul
del /f "{exe_name}"
move /Y "{update_name}" "{exe_name}"
start "" "{exe_name}"
del "%~f0"
'''
    with open(bat_path, 'w', encoding='utf-8') as f:
        f.write(bat_content)

    subprocess.Popen(
        ['cmd', '/c', bat_path],
        cwd=app_dir,
        creationflags=subprocess.CREATE_NO_WINDOW
    )
    sys.exit(0)


# 한글 -> QWERTY 매핑
KOREAN_TO_QWERTY = {
    'ㄱ': 'r', 'ㄲ': 'R', 'ㄴ': 's', 'ㄷ': 'e', 'ㄸ': 'E', 'ㄹ': 'f',
    'ㅁ': 'a', 'ㅂ': 'q', 'ㅃ': 'Q', 'ㅅ': 't', 'ㅆ': 'T', 'ㅇ': 'd',
    'ㅈ': 'w', 'ㅉ': 'W', 'ㅊ': 'c', 'ㅋ': 'z', 'ㅌ': 'x', 'ㅍ': 'v', 'ㅎ': 'g',
    'ㅏ': 'k', 'ㅐ': 'o', 'ㅑ': 'i', 'ㅒ': 'O', 'ㅓ': 'j', 'ㅔ': 'p',
    'ㅕ': 'u', 'ㅖ': 'P', 'ㅗ': 'h', 'ㅘ': 'hk', 'ㅙ': 'ho', 'ㅚ': 'hl',
    'ㅛ': 'y', 'ㅜ': 'n', 'ㅝ': 'nj', 'ㅞ': 'np', 'ㅟ': 'nl', 'ㅠ': 'b',
    'ㅡ': 'm', 'ㅢ': 'ml', 'ㅣ': 'l',
    'ㄳ': 'rt', 'ㄵ': 'sw', 'ㄶ': 'sg', 'ㄺ': 'fr', 'ㄻ': 'fa',
    'ㄼ': 'fq', 'ㄽ': 'ft', 'ㄾ': 'fx', 'ㄿ': 'fv', 'ㅀ': 'fg', 'ㅄ': 'qt'
}

# 스캔코드 -> QWERTY 키 매핑 (keyboard 라이브러리용)
SCANCODE_TO_QWERTY = {
    2: '1', 3: '2', 4: '3', 5: '4', 6: '5', 7: '6', 8: '7', 9: '8', 10: '9', 11: '0',
    12: '-', 13: '=',
    16: 'q', 17: 'w', 18: 'e', 19: 'r', 20: 't', 21: 'y', 22: 'u', 23: 'i', 24: 'o', 25: 'p',
    26: '[', 27: ']',
    30: 'a', 31: 's', 32: 'd', 33: 'f', 34: 'g', 35: 'h', 36: 'j', 37: 'k', 38: 'l',
    39: ';', 40: "'",
    44: 'z', 45: 'x', 46: 'c', 47: 'v', 48: 'b', 49: 'n', 50: 'm',
    51: ',', 52: '.', 53: '/',
    41: '`', 43: '\\',
}

# Virtual Key Code → (normal, shifted) 매핑 (Shift 인식, 범용 트리거용)
VK_TO_CHAR = {
    # 숫자 키 (상단)
    0x30: ('0', ')'), 0x31: ('1', '!'), 0x32: ('2', '@'), 0x33: ('3', '#'),
    0x34: ('4', '$'), 0x35: ('5', '%'), 0x36: ('6', '^'), 0x37: ('7', '&'),
    0x38: ('8', '*'), 0x39: ('9', '('),
    # 알파벳 키 (A-Z는 0x41-0x5A)
    0x41: ('a', 'A'), 0x42: ('b', 'B'), 0x43: ('c', 'C'), 0x44: ('d', 'D'),
    0x45: ('e', 'E'), 0x46: ('f', 'F'), 0x47: ('g', 'G'), 0x48: ('h', 'H'),
    0x49: ('i', 'I'), 0x4A: ('j', 'J'), 0x4B: ('k', 'K'), 0x4C: ('l', 'L'),
    0x4D: ('m', 'M'), 0x4E: ('n', 'N'), 0x4F: ('o', 'O'), 0x50: ('p', 'P'),
    0x51: ('q', 'Q'), 0x52: ('r', 'R'), 0x53: ('s', 'S'), 0x54: ('t', 'T'),
    0x55: ('u', 'U'), 0x56: ('v', 'V'), 0x57: ('w', 'W'), 0x58: ('x', 'X'),
    0x59: ('y', 'Y'), 0x5A: ('z', 'Z'),
    # 기호 키
    0xBD: ('-', '_'), 0xBB: ('=', '+'), 0xDB: ('[', '{'), 0xDD: (']', '}'),
    0xDC: ('\\', '|'), 0xBA: (';', ':'), 0xDE: ("'", '"'), 0xBC: (',', '<'),
    0xBE: ('.', '>'), 0xBF: ('/', '?'), 0xC0: ('`', '~'),
}

# Unicode 자모 매핑
UNICODE_JAMO = {
    '\u1100': 'r', '\u1101': 'R', '\u1102': 's', '\u1103': 'e', '\u1104': 'E',
    '\u1105': 'f', '\u1106': 'a', '\u1107': 'q', '\u1108': 'Q', '\u1109': 't',
    '\u110A': 'T', '\u110B': 'd', '\u110C': 'w', '\u110D': 'W', '\u110E': 'c',
    '\u110F': 'z', '\u1110': 'x', '\u1111': 'v', '\u1112': 'g',
    '\u1161': 'k', '\u1162': 'o', '\u1163': 'i', '\u1164': 'O', '\u1165': 'j',
    '\u1166': 'p', '\u1167': 'u', '\u1168': 'P', '\u1169': 'h', '\u116A': 'hk',
    '\u116B': 'ho', '\u116C': 'hl', '\u116D': 'y', '\u116E': 'n', '\u116F': 'nj',
    '\u1170': 'np', '\u1171': 'nl', '\u1172': 'b', '\u1173': 'm', '\u1174': 'ml',
    '\u1175': 'l',
    '\u11A8': 'r', '\u11A9': 'R', '\u11AA': 'rt', '\u11AB': 's', '\u11AC': 'sw',
    '\u11AD': 'sg', '\u11AE': 'e', '\u11AF': 'f', '\u11B0': 'fr', '\u11B1': 'fa',
    '\u11B2': 'fq', '\u11B3': 'fs', '\u11B4': 'fx', '\u11B5': 'fv', '\u11B6': 'fg',
    '\u11B7': 'a', '\u11B8': 'q', '\u11B9': 'qt', '\u11BA': 't', '\u11BB': 'T',
    '\u11BC': 'd', '\u11BD': 'w', '\u11BE': 'c', '\u11BF': 'z', '\u11C0': 'x',
    '\u11C1': 'v', '\u11C2': 'g'
}

# 겹받침 쌍 (한글 IME가 자동으로 합치는 자음 조합)
GYEOP_BATCHIM_PAIRS = {
    ('ㄱ', 'ㅅ'), ('ㄴ', 'ㅈ'), ('ㄴ', 'ㅎ'),
    ('ㄹ', 'ㄱ'), ('ㄹ', 'ㅁ'), ('ㄹ', 'ㅂ'), ('ㄹ', 'ㅅ'),
    ('ㄹ', 'ㅌ'), ('ㄹ', 'ㅍ'), ('ㄹ', 'ㅎ'),
    ('ㅂ', 'ㅅ'),
}

# QWERTY -> 한글 자모
QWERTY_TO_KOREAN = {
    'r': 'ㄱ', 'R': 'ㄲ', 's': 'ㄴ', 'e': 'ㄷ', 'E': 'ㄸ',
    'f': 'ㄹ', 'a': 'ㅁ', 'q': 'ㅂ', 'Q': 'ㅃ', 't': 'ㅅ',
    'T': 'ㅆ', 'd': 'ㅇ', 'w': 'ㅈ', 'W': 'ㅉ', 'c': 'ㅊ',
    'z': 'ㅋ', 'x': 'ㅌ', 'v': 'ㅍ', 'g': 'ㅎ',
    'k': 'ㅏ', 'o': 'ㅐ', 'i': 'ㅑ', 'O': 'ㅒ', 'j': 'ㅓ',
    'p': 'ㅔ', 'u': 'ㅕ', 'P': 'ㅖ', 'h': 'ㅗ', 'y': 'ㅛ',
    'n': 'ㅜ', 'b': 'ㅠ', 'm': 'ㅡ', 'l': 'ㅣ'
}


def convert_to_qwerty(text: str) -> str:
    """한글(완성형/자모)을 QWERTY 키 입력으로 변환"""
    import unicodedata
    result = ''
    for char in text:
        if '\uAC00' <= char <= '\uD7A3':
            decomposed = unicodedata.normalize('NFD', char)
            for jamo in decomposed:
                if jamo in UNICODE_JAMO:
                    result += UNICODE_JAMO[jamo]
                elif jamo in KOREAN_TO_QWERTY:
                    result += KOREAN_TO_QWERTY[jamo]
                else:
                    result += jamo
        elif char in KOREAN_TO_QWERTY:
            result += KOREAN_TO_QWERTY[char]
        else:
            result += char
    return result


def convert_to_korean(qwerty: str) -> str:
    """QWERTY 키 입력을 한글 자모로 변환"""
    result = ''
    for char in qwerty:
        if char in QWERTY_TO_KOREAN:
            result += QWERTY_TO_KOREAN[char]
        else:
            result += char
    return result


def calc_visual_len(qwerty_trigger: str) -> int:
    """QWERTY 트리거의 화면 표시 글자수 계산 (한글 겹받침 고려)
    예: 'rt'(ㄱㅅ) → IME가 ㄳ으로 합침 → 1글자, 'dx'(ㅇㅌ) → 합칠 수 없음 → 2글자
    """
    korean = convert_to_korean(qwerty_trigger)
    count = 0
    i = 0
    while i < len(korean):
        if i + 1 < len(korean) and (korean[i], korean[i + 1]) in GYEOP_BATCHIM_PAIRS:
            count += 1
            i += 2
        else:
            count += 1
            i += 1
    return count


class TriggerLineEdit(QLineEdit):
    """영문 모드에서 IME를 우회하여 영문/특수문자를 직접 입력하는 트리거 필드"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.english_mode = False

    def set_english_mode(self, english: bool):
        self.english_mode = english
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, not english)

    def keyPressEvent(self, event):
        if self.english_mode:
            # Ctrl/Alt 조합은 기본 동작 유지 (Ctrl+A, Ctrl+C 등)
            if not (event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)):
                vk = event.nativeVirtualKey()
                if vk and vk in VK_TO_CHAR:
                    normal, shifted = VK_TO_CHAR[vk]
                    char = shifted if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else normal
                    self.insert(char)
                    return
        super().keyPressEvent(event)

    def inputMethodEvent(self, event):
        if self.english_mode:
            event.ignore()
            return
        super().inputMethodEvent(event)


class SnippetManager:
    """스니펫 데이터 관리"""

    def __init__(self, snippets_file: str):
        self.snippets = []
        self.snippets_file = snippets_file
        self.load()

    def load(self):
        if os.path.exists(self.snippets_file):
            try:
                with open(self.snippets_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, dict) and "snippets" in data:
                        self.snippets = []
                        for s in data["snippets"]:
                            self.snippets.append({
                                "id": s.get("id", str(uuid.uuid4())),
                                "trigger": s.get("trigger", "").replace(" ", ""),
                                "content": s.get("content", ""),
                                "createdAt": s.get("createdAt", time.time())
                            })
                    else:
                        self.snippets = data
            except:
                self.snippets = self._get_defaults()
        else:
            self.snippets = self._get_defaults()
            self.save()

    def _get_defaults(self):
        return [
            {"id": str(uuid.uuid4()), "trigger": "ㄱㅅ", "content": "감사합니다", "createdAt": time.time()},
        ]

    def save(self):
        os.makedirs(os.path.dirname(self.snippets_file), exist_ok=True)
        with open(self.snippets_file, 'w', encoding='utf-8') as f:
            json.dump(self.snippets, f, ensure_ascii=False, indent=2)

    def add(self, trigger: str, content: str):
        snippet = {
            "id": str(uuid.uuid4()),
            "trigger": trigger,
            "content": content,
            "createdAt": time.time()
        }
        self.snippets.append(snippet)
        self.save()
        return snippet

    def update(self, id: str, trigger: str, content: str):
        for s in self.snippets:
            if s["id"] == id:
                s["trigger"] = trigger
                s["content"] = content
                break
        self.save()

    def delete(self, id: str):
        self.snippets = [s for s in self.snippets if s["id"] != id]
        self.save()

    def get_trigger_map(self):
        result = {}
        for s in self.snippets:
            qwerty_trigger = convert_to_qwerty(s["trigger"])
            result[qwerty_trigger] = s["content"]
            if qwerty_trigger != s["trigger"]:
                result[s["trigger"]] = s["content"]
        return result


class SnippetEngine(QObject):
    """전역 키보드 후킹 및 치환 엔진 (pynput 사용)"""

    def __init__(self, manager: SnippetManager):
        super().__init__()
        self.manager = manager
        self.buffer = ""
        self.running = False
        self.trigger_map = {}
        self.max_trigger_len = 0
        self.is_replacing = False
        self._last_replace_time = 0.0
        self.listener = None
        self.keyboard_controller = Controller()
        self.ctrl_pressed = False
        self.alt_pressed = False
        self.shift_pressed = False
        self.refresh_triggers()

    def refresh_triggers(self):
        self.trigger_map = self.manager.get_trigger_map()
        if self.trigger_map:
            self.max_trigger_len = max(len(t) for t in self.trigger_map.keys())
        else:
            self.max_trigger_len = 0

    def on_press(self, key):
        if self.is_replacing:
            return

        vk = getattr(key, 'vk', None)

        # Modifier 키 추적
        if key == Key.ctrl_l or key == Key.ctrl_r:
            self.ctrl_pressed = True
            return
        if key == Key.alt_l or key == Key.alt_r or key == Key.alt_gr:
            self.alt_pressed = True
            return
        if key in (Key.shift, Key.shift_l, Key.shift_r):
            self.shift_pressed = True
            return
        if key == Key.cmd:
            self.buffer = ""
            return

        # Modifier가 눌려있으면 버퍼 초기화
        if self.ctrl_pressed or self.alt_pressed:
            self.buffer = ""
            return

        # 종결키 처리: 버퍼 즉시 캡처 & 초기화 후 지연 체크
        if key == Key.space or key == Key.tab:
            buf_snapshot = self.buffer
            self.buffer = ""  # 즉시 초기화 → 중복 스페이스 이벤트 방지
            if buf_snapshot:
                def _delayed_check(snapshot=buf_snapshot):
                    time.sleep(0.05)  # IME 조합 완료 대기
                    if self.is_replacing:
                        return
                    self._check_triggers_snapshot(snapshot)
                threading.Thread(target=_delayed_check, daemon=True).start()
            return

        # Backspace 처리
        if key == Key.backspace:
            if self.buffer:
                self.buffer = self.buffer[:-1]
            return

        # 네비게이션 키 - 버퍼 초기화
        if key in [Key.esc, Key.enter, Key.left, Key.right, Key.up, Key.down, Key.home, Key.end, Key.delete]:
            self.buffer = ""
            return

        # 일반 문자 키 처리 (VK 코드 기반, Shift 인식)
        try:
            if vk and vk in VK_TO_CHAR:
                normal, shifted = VK_TO_CHAR[vk]
                char = shifted if self.shift_pressed else normal
                self.buffer += char
                if len(self.buffer) > self.max_trigger_len + 5:
                    self.buffer = self.buffer[-(self.max_trigger_len + 5):]
        except:
            pass

    def on_release(self, key):
        if self.is_replacing:
            return
        # Modifier 키 해제 추적
        if key == Key.ctrl_l or key == Key.ctrl_r:
            self.ctrl_pressed = False
        if key == Key.alt_l or key == Key.alt_r or key == Key.alt_gr:
            self.alt_pressed = False
        if key in (Key.shift, Key.shift_l, Key.shift_r):
            self.shift_pressed = False

    def _check_triggers_snapshot(self, snapshot: str) -> bool:
        """스냅샷 기반 트리거 체크 (self.buffer 건드리지 않음)"""
        now = time.monotonic()
        elapsed = now - self._last_replace_time
        if self.is_replacing:
            return False
        # 디바운스: 마지막 치환 후 300ms 이내 재발동 방지
        if elapsed < 0.3:
            return False
        # 최장 매칭: 여러 트리거가 매칭되면 가장 긴 것 우선
        best_trigger = None
        best_content = None
        best_len = 0
        for trigger, content in self.trigger_map.items():
            if snapshot.endswith(trigger) and len(trigger) > best_len:
                best_trigger = trigger
                best_content = content
                best_len = len(trigger)
        if best_trigger:
            self.is_replacing = True
            self.buffer = ""
            threading.Thread(target=self._replace, args=(best_trigger, best_content), daemon=True).start()
            return True
        return False

    def _replace(self, trigger: str, content: str):
        try:
            time.sleep(0.1)  # IME 조합 완료 대기

            # 리스너 일시 중지 (pynput이 Ctrl+V를 중복 처리하는 것 방지)
            if self.listener:
                self.listener.stop()
                self.listener = None
            time.sleep(0.05)

            # ctypes SendInput으로 백스페이스 (겹받침은 2키→1글자이므로 화면 글자수 기준)
            backspace_count = calc_visual_len(trigger) + 1
            send_backspaces(backspace_count)
            time.sleep(0.05)

            # 콘솔/GUI 감지 후 분기
            console = is_console_window()

            if console:
                # 콘솔/터미널: UNICODE 직접 입력 (Ctrl+V는 터미널+셸 양쪽에서 중복 처리됨)
                send_unicode_string(content)
                time.sleep(0.1)
            elif len(content) <= 50:
                # GUI 짧은 텍스트: UNICODE 직접 입력
                send_unicode_string(content)
                time.sleep(0.1)
            else:
                # GUI 긴 텍스트: 클립보드 + Ctrl+V
                try:
                    old_clipboard = pyperclip.paste()
                except:
                    old_clipboard = ""
                pyperclip.copy(content)
                time.sleep(0.05)
                send_paste()
                time.sleep(0.2)
                try:
                    pyperclip.copy(old_clipboard)
                except:
                    pass
        except Exception:
            pass
        finally:
            self.buffer = ""
            self.ctrl_pressed = False
            self.alt_pressed = False
            self.shift_pressed = False
            self._last_replace_time = time.monotonic()
            self.is_replacing = False
            # 리스너 재시작
            if self.running:
                self.listener = pynput_keyboard.Listener(
                    on_press=self.on_press,
                    on_release=self.on_release
                )
                self.listener.start()

    def start(self):
        if not self.running:
            self.running = True
            self.buffer = ""
            self.listener = pynput_keyboard.Listener(on_press=self.on_press, on_release=self.on_release)
            self.listener.start()

    def stop(self):
        if self.running:
            self.running = False
            self.buffer = ""
            if self.listener:
                self.listener.stop()
                self.listener = None


class SnippetCard(QFrame):
    """스니펫 카드 위젯"""
    clicked = pyqtSignal(dict)
    copyClicked = pyqtSignal(dict)
    deleteClicked = pyqtSignal(dict)

    def __init__(self, snippet: dict, is_selected: bool = False, parent=None):
        super().__init__(parent)
        self.snippet = snippet
        self.is_selected = is_selected
        self.setup_ui()

    def setup_ui(self):
        self.setFixedHeight(65)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.update_style()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        # 상단: 트리거 뱃지 + 버튼들
        top_layout = QHBoxLayout()
        top_layout.setSpacing(4)

        # 트리거 뱃지
        trigger_badge = QLabel(self.snippet["trigger"])
        trigger_badge.setStyleSheet("""
            background-color: #064e3b;
            color: #6ee7b7;
            border-radius: 4px;
            padding: 3px 10px;
            font-size: 12px;
            font-family: 'Malgun Gothic';
        """)
        trigger_badge.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        top_layout.addWidget(trigger_badge)

        top_layout.addStretch()

        # 복사 버튼 (Segoe MDL2 Assets 폰트 사용)
        self.copy_btn = QPushButton("\uE8C8")
        self.copy_btn.setFixedSize(24, 24)
        self.copy_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                border: none;
                border-radius: 4px;
                font-size: 14px;
                font-family: 'Segoe MDL2 Assets';
                color: #94a3b8;
            }
            QPushButton:hover {
                background-color: #334155;
                color: #ffffff;
            }
        """)
        self.copy_btn.clicked.connect(lambda: self.copyClicked.emit(self.snippet))
        self.copy_btn.hide()
        top_layout.addWidget(self.copy_btn)

        # 삭제 버튼
        self.delete_btn = QPushButton("\uE74D")
        self.delete_btn.setFixedSize(24, 24)
        self.delete_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                border: none;
                border-radius: 4px;
                font-size: 14px;
                font-family: 'Segoe MDL2 Assets';
                color: #94a3b8;
            }
            QPushButton:hover {
                background-color: #334155;
                color: #ffffff;
            }
        """)
        self.delete_btn.clicked.connect(lambda: self.deleteClicked.emit(self.snippet))
        self.delete_btn.hide()
        top_layout.addWidget(self.delete_btn)

        layout.addLayout(top_layout)

        # 하단: 내용 미리보기 (카드 너비에 맞게 자동 말줄임)
        preview_text = self.snippet["content"].replace('\n', ' ')
        preview_label = QLabel(preview_text)
        preview_label.setStyleSheet("color: #94a3b8; font-size: 12px;")
        preview_label.setMaximumWidth(280)
        preview_label.setTextFormat(Qt.TextFormat.PlainText)
        from PyQt6.QtCore import Qt as QtCore_Qt
        preview_label.setWordWrap(False)
        # QFontMetrics로 너비에 맞게 말줄임
        metrics = preview_label.fontMetrics()
        elided = metrics.elidedText(preview_text, QtCore_Qt.TextElideMode.ElideRight, 270)
        preview_label.setText(elided)
        layout.addWidget(preview_label)

    def update_style(self):
        if self.is_selected:
            self.setStyleSheet("""
                QFrame {
                    background-color: #1e293b;
                    border: 1px solid #334155;
                    border-radius: 8px;
                }
            """)
        else:
            self.setStyleSheet("""
                QFrame {
                    background-color: transparent;
                    border: none;
                    border-radius: 8px;
                }
                QFrame:hover {
                    background-color: #1e293b;
                }
            """)

    def enterEvent(self, event):
        self.copy_btn.show()
        self.delete_btn.show()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.copy_btn.hide()
        self.delete_btn.hide()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        self.clicked.emit(self.snippet)
        super().mousePressEvent(event)


class QfredApp(QMainWindow):
    """메인 GUI 애플리케이션"""

    def __init__(self, manager: SnippetManager, engine: SnippetEngine, app_settings=None):
        super().__init__()
        self.manager = manager
        self.engine = engine
        self.app_settings = app_settings or AppSettings()
        self.selected_id = None
        self.current_tab = "snippets"
        self.setWindowTitle("Q-fred - Smart Snippet Manager")
        self.setMinimumSize(900, 550)
        self.resize(950, 600)

        # 아이콘 설정
        logo_path = os.path.join(RESOURCE_DIR, "q_logo_hd.ico")
        if os.path.exists(logo_path):
            self.setWindowIcon(QIcon(logo_path))

        self.setup_ui()
        self.setup_tray()
        self.load_snippets_list()

    def setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        central.setStyleSheet("background-color: #0f172a;")

        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ========== 사이드바 ==========
        sidebar = QFrame()
        sidebar.setFixedWidth(320)
        sidebar.setStyleSheet("""
            QFrame {
                background-color: #0f172a;
                border-right: 1px solid #1e293b;
            }
        """)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(16, 20, 16, 16)
        sidebar_layout.setSpacing(14)

        # 타이틀 + 설정 버튼
        title_frame = QFrame()
        title_frame.setStyleSheet("QFrame { background: transparent; border: none; }")
        title_layout = QHBoxLayout(title_frame)
        title_layout.setContentsMargins(0, 0, 0, 0)

        title = QLabel("Snippets")
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #ffffff;")
        title_layout.addWidget(title)

        title_layout.addStretch()

        # 설정 버튼
        self.settings_btn = QPushButton("⚙")
        self.settings_btn.setFixedSize(32, 32)
        self.settings_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.settings_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                border: none;
                color: #94a3b8;
                font-size: 18px;
                border-radius: 6px;
            }
            QPushButton:hover {
                background-color: #334155;
                color: #ffffff;
            }
        """)
        self.settings_btn.clicked.connect(self.open_settings)
        title_layout.addWidget(self.settings_btn)

        sidebar_layout.addWidget(title_frame)

        # 탭 버튼 (Snippets / Test)
        tab_frame = QFrame()
        tab_frame.setStyleSheet("""
            QFrame {
                background-color: #1e293b;
                border-radius: 8px;
                border: none;
            }
        """)
        tab_layout = QHBoxLayout(tab_frame)
        tab_layout.setContentsMargins(4, 4, 4, 4)
        tab_layout.setSpacing(4)

        self.snippets_tab_btn = QPushButton("⚡ Snippets")
        self.snippets_tab_btn.setFixedHeight(32)
        self.snippets_tab_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.snippets_tab_btn.clicked.connect(lambda: self.switch_tab("snippets"))
        tab_layout.addWidget(self.snippets_tab_btn)

        self.test_tab_btn = QPushButton("⌨ Test")
        self.test_tab_btn.setFixedHeight(32)
        self.test_tab_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.test_tab_btn.clicked.connect(lambda: self.switch_tab("test"))
        tab_layout.addWidget(self.test_tab_btn)

        sidebar_layout.addWidget(tab_frame)
        self.update_tab_styles()

        # 검색창
        search_frame = QFrame()
        search_frame.setStyleSheet("""
            QFrame {
                background-color: #1e293b;
                border: 1px solid #334155;
                border-radius: 8px;
            }
        """)
        search_layout = QHBoxLayout(search_frame)
        search_layout.setContentsMargins(12, 0, 12, 0)

        search_icon = QLabel("🔍")
        search_icon.setStyleSheet("font-size: 14px; border: none; background: transparent;")
        search_layout.addWidget(search_icon)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search snippets...")
        self.search_input.setStyleSheet("""
            QLineEdit {
                background-color: transparent;
                border: none;
                color: #ffffff;
                font-size: 13px;
                padding: 8px 0;
            }
        """)
        self.search_input.textChanged.connect(self.on_search)
        search_layout.addWidget(self.search_input)

        sidebar_layout.addWidget(search_frame)

        # SAVED SNIPPETS 라벨 + 카운트
        label_frame = QHBoxLayout()
        saved_label = QLabel("SAVED SNIPPETS")
        saved_label.setStyleSheet("font-size: 10px; font-weight: bold; color: #64748b;")
        label_frame.addWidget(saved_label)

        label_frame.addStretch()

        self.count_badge = QLabel("0")
        self.count_badge.setFixedSize(22, 22)
        self.count_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.count_badge.setStyleSheet("""
            background-color: #1e293b;
            color: #64748b;
            border-radius: 11px;
            font-size: 10px;
            font-weight: bold;
        """)
        label_frame.addWidget(self.count_badge)

        sidebar_layout.addLayout(label_frame)

        # 스니펫 리스트 (스크롤)
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setStyleSheet("""
            QScrollArea {
                background-color: transparent;
                border: none;
            }
            QScrollBar:vertical {
                background-color: transparent;
                width: 6px;
                margin: 0px;
            }
            QScrollBar::handle:vertical {
                background-color: #334155;
                border-radius: 3px;
                min-height: 20px;
            }
            QScrollBar::handle:vertical:hover {
                background-color: #475569;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: transparent;
            }
        """)

        self.snippet_list_widget = QWidget()
        self.snippet_list_layout = QVBoxLayout(self.snippet_list_widget)
        self.snippet_list_layout.setContentsMargins(0, 0, 0, 0)
        self.snippet_list_layout.setSpacing(4)
        self.snippet_list_layout.addStretch()

        scroll_area.setWidget(self.snippet_list_widget)
        sidebar_layout.addWidget(scroll_area, 1)

        # + New 버튼
        new_btn = QPushButton("+ New")
        new_btn.setFixedHeight(40)
        new_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        new_btn.setStyleSheet("""
            QPushButton {
                background-color: #334155;
                border: none;
                border-radius: 8px;
                color: #ffffff;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #475569;
            }
        """)
        new_btn.clicked.connect(self.on_new)
        sidebar_layout.addWidget(new_btn)

        main_layout.addWidget(sidebar)

        # ========== 메인 콘텐츠 (스택) ==========
        self.content_stack = QStackedWidget()
        self.content_stack.setStyleSheet("background-color: #0f172a;")

        # 페이지 1: 스니펫 편집
        self.edit_page = self.create_edit_page()
        self.content_stack.addWidget(self.edit_page)

        # 페이지 2: 테스트 (Playground)
        self.test_page = self.create_test_page()
        self.content_stack.addWidget(self.test_page)

        main_layout.addWidget(self.content_stack, 1)

    def create_edit_page(self):
        """스니펫 편집 페이지"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(32, 20, 32, 32)
        layout.setSpacing(16)

        # 헤더
        self.header_label = QLabel("Create Snippet")
        self.header_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #ffffff;")
        layout.addWidget(self.header_label)

        # 트리거 입력
        trigger_label = QLabel("Trigger Keyword (단축어)")
        trigger_label.setStyleSheet("font-size: 12px; color: #94a3b8;")
        layout.addWidget(trigger_label)

        # 트리거 입력 컨테이너
        trigger_container = QFrame()
        trigger_container.setStyleSheet("""
            QFrame {
                background-color: #1e293b;
                border: 1px solid #334155;
                border-radius: 8px;
            }
        """)
        trigger_layout = QHBoxLayout(trigger_container)
        trigger_layout.setContentsMargins(0, 0, 12, 0)

        self.trigger_input = QLineEdit()
        self.trigger_input.setPlaceholderText("예: ㄱㅅ, addr, :sig")
        self.trigger_input.setStyleSheet("""
            QLineEdit {
                background-color: transparent;
                border: none;
                color: #ffffff;
                font-size: 15px;
                font-family: 'Consolas', 'Malgun Gothic';
                padding: 12px;
            }
        """)
        trigger_layout.addWidget(self.trigger_input)

        trigger_hint = QLabel("Type this + Space")
        trigger_hint.setFixedHeight(24)
        trigger_hint.setStyleSheet("""
            background-color: #334155;
            color: #94a3b8;
            border-radius: 4px;
            padding: 0px 8px;
            font-size: 11px;
        """)
        trigger_layout.addWidget(trigger_hint)

        layout.addWidget(trigger_container)

        # 트리거 도움말
        help_label = QLabel("한글 자모, 영문, 특수문자 모두 사용 가능")
        help_label.setStyleSheet("color: #64748b; font-size: 11px;")
        layout.addWidget(help_label)

        # 내용 입력
        content_label = QLabel("Replacement Text (변환될 내용)")
        content_label.setStyleSheet("font-size: 12px; color: #94a3b8; margin-top: 8px;")
        layout.addWidget(content_label)

        # 내용 입력 컨테이너
        content_container = QFrame()
        content_container.setStyleSheet("""
            QFrame {
                background-color: #1e293b;
                border: 1px solid #334155;
                border-radius: 8px;
            }
        """)
        content_inner_layout = QVBoxLayout(content_container)
        content_inner_layout.setContentsMargins(0, 0, 0, 0)
        content_inner_layout.setSpacing(0)

        # 힌트 라벨
        hint_bar = QHBoxLayout()
        hint_bar.setContentsMargins(12, 8, 12, 0)
        hint_bar.addStretch()
        content_hint = QLabel("...to get this")
        content_hint.setFixedHeight(24)
        content_hint.setStyleSheet("""
            background-color: #334155;
            color: #94a3b8;
            border-radius: 4px;
            padding: 0px 8px;
            font-size: 11px;
        """)
        hint_bar.addWidget(content_hint)
        content_inner_layout.addLayout(hint_bar)

        self.content_input = QTextEdit()
        self.content_input.setAcceptRichText(False)
        self.content_input.setPlaceholderText("e.g. 감사합니다")
        self.content_input.setStyleSheet("""
            QTextEdit {
                background-color: transparent;
                border: none;
                color: #ffffff;
                font-size: 14px;
                padding: 12px;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 8px;
                margin: 4px 2px 4px 0px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background: rgba(255, 255, 255, 0.2);
                border-radius: 4px;
                min-height: 30px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(255, 255, 255, 0.35);
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: transparent;
            }
        """)
        self.content_input.setMinimumHeight(180)
        content_inner_layout.addWidget(self.content_input)

        layout.addWidget(content_container)

        # 도움말
        output_help = QLabel("The expanded text output.")
        output_help.setStyleSheet("color: #64748b; font-size: 11px;")
        layout.addWidget(output_help)

        layout.addStretch()

        # 버튼 영역
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setFixedSize(90, 40)
        self.cancel_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                border: none;
                border-radius: 6px;
                color: #94a3b8;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #1e293b;
            }
        """)
        self.cancel_btn.clicked.connect(self.on_new)
        self.cancel_btn.hide()
        btn_layout.addWidget(self.cancel_btn)

        self.save_btn = QPushButton("💾  Save")
        self.save_btn.setFixedSize(120, 40)
        self.save_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.save_btn.setStyleSheet("""
            QPushButton {
                background-color: #4a946c;
                border: none;
                border-radius: 6px;
                color: #ffffff;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #5db684;
            }
        """)
        self.save_btn.clicked.connect(self.on_save)
        btn_layout.addWidget(self.save_btn)

        layout.addLayout(btn_layout)

        return page

    def create_test_page(self):
        """테스트 (Playground) 페이지"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(32, 20, 32, 32)
        layout.setSpacing(16)

        # 헤더
        header_layout = QHBoxLayout()
        header_left = QVBoxLayout()

        title = QLabel("Playground")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #ffffff;")
        header_left.addWidget(title)

        subtitle = QLabel("Try typing your triggers here")
        subtitle.setStyleSheet("font-size: 12px; color: #94a3b8;")
        header_left.addWidget(subtitle)

        header_layout.addLayout(header_left)
        header_layout.addStretch()

        clear_btn = QPushButton("🧹 Clear")
        clear_btn.setFixedSize(80, 32)
        clear_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        clear_btn.setStyleSheet("""
            QPushButton {
                background-color: #334155;
                border: none;
                border-radius: 6px;
                color: #cbd5e1;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #475569;
            }
        """)
        clear_btn.clicked.connect(self.on_playground_clear)
        header_layout.addWidget(clear_btn)

        layout.addLayout(header_layout)

        # 테스트 입력 영역
        self.playground_input = QTextEdit()
        self.playground_input.setAcceptRichText(False)
        self.playground_input.setPlaceholderText("Type here to test your snippets... (e.g. type 'ㄱㅅ' + Space)")
        self.playground_input.setStyleSheet("""
            QTextEdit {
                background-color: #1e293b;
                border: 1px solid #334155;
                border-radius: 8px;
                color: #ffffff;
                font-size: 14px;
                padding: 16px;
            }
            QTextEdit:focus {
                border: 1px solid #34d399;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 8px;
                margin: 8px 2px 8px 0px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background: rgba(255, 255, 255, 0.2);
                border-radius: 4px;
                min-height: 30px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(255, 255, 255, 0.35);
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: transparent;
            }
        """)
        layout.addWidget(self.playground_input, 1)

        # 상태 바
        status_bar = QFrame()
        status_bar.setFixedHeight(40)
        status_bar.setStyleSheet("""
            QFrame {
                background-color: #1e293b;
                border: 1px solid #334155;
                border-radius: 6px;
            }
        """)
        status_layout = QHBoxLayout(status_bar)
        status_layout.setContentsMargins(12, 0, 12, 0)

        self.status_dot = QLabel("●")
        self.status_dot.setStyleSheet("color: #64748b; font-size: 10px;")
        status_layout.addWidget(self.status_dot)

        self.status_label = QLabel("Waiting for trigger...")
        self.status_label.setStyleSheet("color: #94a3b8; font-size: 12px;")
        status_layout.addWidget(self.status_label)

        status_layout.addStretch()

        self.char_count = QLabel("0 chars")
        self.char_count.setStyleSheet("color: #64748b; font-size: 11px; font-family: 'Consolas';")
        status_layout.addWidget(self.char_count)

        layout.addWidget(status_bar)

        # 텍스트 변경 시 글자 수 업데이트
        self.playground_input.textChanged.connect(self.update_char_count)

        return page

    def update_tab_styles(self):
        if self.current_tab == "snippets":
            self.snippets_tab_btn.setStyleSheet("""
                QPushButton {
                    background-color: #334155;
                    border: none;
                    border-radius: 6px;
                    color: #ffffff;
                    font-size: 12px;
                    font-weight: bold;
                }
            """)
            self.test_tab_btn.setStyleSheet("""
                QPushButton {
                    background-color: transparent;
                    border: none;
                    border-radius: 6px;
                    color: #94a3b8;
                    font-size: 12px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #334155;
                }
            """)
        else:
            self.snippets_tab_btn.setStyleSheet("""
                QPushButton {
                    background-color: transparent;
                    border: none;
                    border-radius: 6px;
                    color: #94a3b8;
                    font-size: 12px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #334155;
                }
            """)
            self.test_tab_btn.setStyleSheet("""
                QPushButton {
                    background-color: #4a946c;
                    border: none;
                    border-radius: 6px;
                    color: #ffffff;
                    font-size: 12px;
                    font-weight: bold;
                }
            """)

    def switch_tab(self, tab: str):
        self.current_tab = tab
        self.update_tab_styles()
        if tab == "snippets":
            self.content_stack.setCurrentIndex(0)
        else:
            self.content_stack.setCurrentIndex(1)

    def update_char_count(self):
        count = len(self.playground_input.toPlainText())
        self.char_count.setText(f"{count} chars")

    def on_playground_clear(self):
        self.playground_input.clear()
        self.status_dot.setStyleSheet("color: #64748b; font-size: 10px;")
        self.status_label.setText("Waiting for trigger...")

    def setup_tray(self):
        """시스템 트레이 설정"""
        self.tray_icon = QSystemTrayIcon(self)

        logo_path = os.path.join(RESOURCE_DIR, "q_logo_hd.ico")
        if os.path.exists(logo_path):
            self.tray_icon.setIcon(QIcon(logo_path))

        tray_menu = QMenu()

        show_action = QAction("열기", self)
        show_action.triggered.connect(self.show_window)
        tray_menu.addAction(show_action)

        tray_menu.addSeparator()

        quit_action = QAction("종료", self)
        quit_action.triggered.connect(self.quit_app)
        tray_menu.addAction(quit_action)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self.on_tray_activated)
        self.tray_icon.setToolTip("Q-fred - 단축어 관리자")
        self.tray_icon.show()

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show_window()

    def show_window(self):
        """창 표시"""
        self.show()
        self.raise_()
        self.activateWindow()

    def hide_window(self):
        """창 숨김"""
        self.hide()

    def closeEvent(self, event):
        """창 닫기 버튼 - 트레이로 최소화"""
        event.ignore()
        self.hide_window()

    def quit_app(self):
        """앱 종료"""
        self.engine.stop()
        self.tray_icon.hide()
        QApplication.quit()

    def show_update_notification(self, latest_ver: str, download_url: str):
        """업데이트 알림 표시 (메인 스레드에서 호출)"""
        QTimer.singleShot(0, lambda: self._show_update_dialog(latest_ver, download_url))

    def _show_update_dialog(self, latest_ver: str, download_url: str):
        """업데이트 다이얼로그"""
        if not download_url:
            return

        reply = QMessageBox.question(
            self, 'Q-fred 업데이트',
            f"새 버전 {latest_ver}이(가) 있습니다.\n현재 버전: {APP_VERSION}\n\n업데이트 하시겠습니까?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # 프로그레스 다이얼로그
        progress = QMessageBox(self)
        progress.setWindowTitle("업데이트")
        progress.setText("다운로드 중... 0%")
        progress.setStandardButtons(QMessageBox.StandardButton.NoButton)
        progress.show()
        QApplication.processEvents()

        def on_progress(percent):
            progress.setText(f"다운로드 중... {percent}%")
            QApplication.processEvents()

        # 다운로드 (메인 스레드에서 실행 - UI 업데이트 위해)
        update_path = download_update(download_url, progress_callback=on_progress)
        progress.close()

        if update_path:
            QMessageBox.information(self, '업데이트', '다운로드 완료! 앱을 재시작합니다.')
            self.engine.stop()
            self.tray_icon.hide()
            apply_update(update_path)
        else:
            QMessageBox.warning(self, '업데이트 실패', '다운로드에 실패했습니다.\n나중에 다시 시도해주세요.')

    def load_snippets_list(self, filter_text=""):
        """스니펫 리스트 로드"""
        # 기존 위젯 제거
        while self.snippet_list_layout.count() > 1:
            item = self.snippet_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        filtered = []
        for snippet in self.manager.snippets:
            if filter_text:
                if filter_text.lower() not in snippet["trigger"].lower() and \
                   filter_text.lower() not in snippet["content"].lower():
                    continue
            filtered.append(snippet)

        # 카운트 업데이트
        self.count_badge.setText(str(len(filtered)))

        # 카드 추가
        for snippet in filtered:
            card = SnippetCard(snippet, snippet["id"] == self.selected_id)
            card.clicked.connect(self.on_select)
            card.copyClicked.connect(self.on_copy_snippet)
            card.deleteClicked.connect(self.on_delete_snippet)
            self.snippet_list_layout.insertWidget(self.snippet_list_layout.count() - 1, card)

    def on_search(self, text):
        self.load_snippets_list(text)

    def on_select(self, snippet):
        """스니펫 선택"""
        self.selected_id = snippet["id"]
        self.header_label.setText("Edit Snippet")

        self.trigger_input.setText(snippet["trigger"])
        self.content_input.setText(snippet["content"])

        self.save_btn.setText("💾  Update")
        self.cancel_btn.show()

        # Snippets 탭으로 전환
        if self.current_tab != "snippets":
            self.switch_tab("snippets")

        self.load_snippets_list()

    def on_new(self):
        """새 스니펫"""
        self.selected_id = None
        self.header_label.setText("Create Snippet")
        self.trigger_input.clear()
        self.content_input.clear()
        self.save_btn.setText("💾  Save")
        self.cancel_btn.hide()
        self.load_snippets_list()

    def on_save(self):
        """저장"""
        trigger_input = self.trigger_input.text().strip()
        content = self.content_input.toPlainText().strip()

        if not trigger_input or not content:
            return

        # 자동 감지: 한글 포함 → 한글 트리거, 그 외 → 그대로 저장
        has_korean = any('\uAC00' <= c <= '\uD7A3' or '\u3131' <= c <= '\u3163' for c in trigger_input)
        if has_korean:
            qwerty_converted = convert_to_qwerty(trigger_input)
            trigger = convert_to_korean(qwerty_converted)
        else:
            trigger = trigger_input

        if self.selected_id:
            self.manager.update(self.selected_id, trigger, content)
        else:
            snippet = self.manager.add(trigger, content)
            self.selected_id = snippet["id"]

        self.engine.refresh_triggers()
        self.load_snippets_list()
        self.header_label.setText("Edit Snippet")
        self.save_btn.setText("💾  Update")
        self.cancel_btn.show()

    def on_copy_snippet(self, snippet):
        """스니펫 복사"""
        new_trigger = snippet["trigger"] + "_copy"
        existing = [s["trigger"] for s in self.manager.snippets]
        counter = 1
        while new_trigger in existing:
            new_trigger = f"{snippet['trigger']}_copy{counter}"
            counter += 1

        self.manager.add(new_trigger, snippet["content"])
        self.engine.refresh_triggers()
        self.load_snippets_list()

    def on_delete_snippet(self, snippet):
        """스니펫 삭제"""
        reply = QMessageBox.question(
            self, '삭제 확인',
            f"정말로 이 스니펫을 삭제하시겠습니까?\n\n트리거: {snippet['trigger']}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.manager.delete(snippet["id"])
            self.engine.refresh_triggers()
            if self.selected_id == snippet["id"]:
                self.on_new()
            else:
                self.load_snippets_list()

    def open_settings(self):
        """설정 창 열기"""
        dialog = SettingsDialog(self.app_settings, self)
        dialog.exec()


class SettingsDialog(QDialog):
    """설정 다이얼로그 (앱 설정 + 로컬 저장 설정)"""

    def __init__(self, app_settings, parent=None):
        super().__init__(parent)
        self.app_settings = app_settings
        self.setWindowTitle("스니펫 설정")
        self.setFixedSize(500, 520)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowMaximizeButtonHint)
        self.setSizeGripEnabled(False)
        self.setStyleSheet("""
            QDialog { background-color: #0f172a; }
            QLabel { color: #e2e8f0; }
            QLineEdit {
                background-color: #1e293b;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 8px 12px;
                color: #ffffff;
                font-size: 13px;
                min-height: 20px;
            }
            QLineEdit:focus { border-color: #4a946c; }
            QPushButton {
                background-color: #334155;
                border: none;
                border-radius: 6px;
                padding: 10px 20px;
                color: #ffffff;
                font-size: 13px;
                min-height: 20px;
            }
            QPushButton:hover { background-color: #475569; }
            QCheckBox { color: #e2e8f0; font-size: 13px; min-height: 24px; }
            QCheckBox::indicator {
                width: 18px; height: 18px;
                border-radius: 4px;
                border: 1px solid #475569;
                background-color: #1e293b;
            }
            QCheckBox::indicator:checked {
                background-color: #4a946c;
                border-color: #4a946c;
            }
        """)
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 24)
        layout.setSpacing(8)

        # ===== 일반 설정 섹션 =====
        general_title = QLabel("일반 설정")
        general_title.setStyleSheet("font-size: 16px; font-weight: bold; color: #ffffff;")
        layout.addWidget(general_title)
        layout.addSpacing(4)

        layout.addSpacing(4)

        # 시작 시 자동 실행
        self.startup_check = QCheckBox("Windows 시작 시 자동 실행")
        self.startup_check.setChecked(self.app_settings.is_registered_startup())
        layout.addWidget(self.startup_check)
        layout.addSpacing(4)

        # 시작 시 창 숨김
        self.minimized_check = QCheckBox("시작 시 트레이로 실행 (창 숨김)")
        self.minimized_check.setChecked(self.app_settings.start_minimized)
        layout.addWidget(self.minimized_check)
        layout.addSpacing(16)

        # 구분선
        line1 = QFrame()
        line1.setFrameShape(QFrame.Shape.HLine)
        line1.setStyleSheet("background-color: #334155;")
        line1.setFixedHeight(1)
        layout.addWidget(line1)
        layout.addSpacing(16)

        # ===== 로컬 저장 설정 섹션 =====
        storage_title = QLabel("로컬 저장 설정")
        storage_title.setStyleSheet("font-size: 16px; font-weight: bold; color: #ffffff;")
        layout.addWidget(storage_title)
        layout.addSpacing(8)

        storage_desc = QLabel("스니펫 데이터가 저장되는 폴더를 지정합니다.")
        storage_desc.setStyleSheet("font-size: 12px; color: #94a3b8;")
        layout.addWidget(storage_desc)
        layout.addSpacing(4)

        # 저장 폴더 경로
        folder_label = QLabel("저장 폴더")
        folder_label.setStyleSheet("font-size: 12px; color: #94a3b8;")
        layout.addWidget(folder_label)
        layout.addSpacing(4)

        folder_frame = QFrame()
        folder_frame.setStyleSheet("QFrame { background: transparent; }")
        folder_layout = QHBoxLayout(folder_frame)
        folder_layout.setContentsMargins(0, 0, 0, 0)
        folder_layout.setSpacing(8)

        self.folder_input = QLineEdit()
        self.folder_input.setPlaceholderText("스니펫 저장 폴더 경로...")
        self.folder_input.setText(self.app_settings.storage_folder)
        self.folder_input.setFixedHeight(38)
        self.folder_input.setReadOnly(True)
        folder_layout.addWidget(self.folder_input)

        browse_btn = QPushButton("찾아보기")
        browse_btn.setFixedSize(100, 38)
        browse_btn.clicked.connect(self.browse_folder)
        folder_layout.addWidget(browse_btn)
        layout.addWidget(folder_frame)

        # 기본값 복원 버튼
        reset_btn = QPushButton("기본 폴더로 복원")
        reset_btn.setFixedSize(140, 32)
        reset_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                border: 1px solid #475569;
                border-radius: 6px;
                color: #94a3b8;
                font-size: 12px;
                min-height: 16px;
                padding: 4px 12px;
            }
            QPushButton:hover {
                background-color: #1e293b;
                color: #ffffff;
            }
        """)
        reset_btn.clicked.connect(self.reset_folder)
        layout.addWidget(reset_btn)
        layout.addSpacing(4)

        # 현재 저장 파일 경로 표시
        self.file_path_label = QLabel("")
        self.file_path_label.setStyleSheet("color: #64748b; font-size: 11px;")
        self.file_path_label.setWordWrap(True)
        self.update_file_path_label()
        layout.addWidget(self.file_path_label)

        layout.addStretch()

        # ===== 버튼 영역 =====
        btn_frame = QFrame()
        btn_frame.setStyleSheet("QFrame { background: transparent; }")
        btn_layout = QHBoxLayout(btn_frame)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(12)

        btn_layout.addStretch()

        save_btn = QPushButton("저장")
        save_btn.setFixedSize(100, 40)
        save_btn.setStyleSheet("""
            QPushButton { background-color: #4a946c; font-weight: bold; min-height: 20px; }
            QPushButton:hover { background-color: #5db684; }
        """)
        save_btn.clicked.connect(self.save_settings)
        btn_layout.addWidget(save_btn)

        cancel_btn = QPushButton("취소")
        cancel_btn.setFixedSize(100, 40)
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        layout.addWidget(btn_frame)

    def browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "스니펫 저장 폴더 선택", self.folder_input.text())
        if folder:
            self.folder_input.setText(folder)
            self.update_file_path_label()

    def reset_folder(self):
        self.folder_input.setText(DEFAULT_STORAGE_FOLDER)
        self.update_file_path_label()

    def update_file_path_label(self):
        folder = self.folder_input.text()
        self.file_path_label.setText(f"저장 파일: {os.path.join(folder, 'snippets.json')}")

    def save_settings(self):
        self.app_settings.start_with_windows = self.startup_check.isChecked()
        self.app_settings.start_minimized = self.minimized_check.isChecked()
        self.app_settings.storage_folder = self.folder_input.text()
        self.accept()


class NavButton(QFrame):
    """네비게이션 바 버튼"""
    clicked = pyqtSignal()

    def __init__(self, icon_text, label_text, parent=None):
        super().__init__(parent)
        self._active = False
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setFixedSize(64, 52)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 4)
        layout.setSpacing(1)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.icon_lbl = QLabel(icon_text)
        self.icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.icon_lbl)

        self.text_lbl = QLabel(label_text)
        self.text_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.text_lbl)

        self._update_style()

    @property
    def active(self):
        return self._active

    @active.setter
    def active(self, value):
        self._active = value
        self._update_style()

    def _update_style(self):
        if self._active:
            self.setStyleSheet("QFrame { background-color: #1e293b; border: none; border-left: 3px solid #4a946c; }")
            color = "#e2e8f0"
        else:
            self.setStyleSheet("QFrame { background-color: transparent; border: none; border-left: 3px solid transparent; }")
            color = "#64748b"
        self.icon_lbl.setStyleSheet(f"color: {color}; font-size: 18px; background: transparent; border: none;")
        self.text_lbl.setStyleSheet(f"color: {color}; font-size: 9px; background: transparent; border: none;")

    def enterEvent(self, event):
        if not self._active:
            self.setStyleSheet("QFrame { background-color: #1e293b; border: none; border-left: 3px solid transparent; }")
            self.icon_lbl.setStyleSheet("color: #94a3b8; font-size: 18px; background: transparent; border: none;")
            self.text_lbl.setStyleSheet("color: #94a3b8; font-size: 9px; background: transparent; border: none;")
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._update_style()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        self.clicked.emit()
        super().mousePressEvent(event)


class DownloadWorker(QThread):
    """yt-dlp 다운로드 워커 스레드"""
    progress = pyqtSignal(dict)   # {'percent': float, 'speed': str, 'eta': str}
    finished = pyqtSignal(dict)   # {'success': bool, 'title': str, 'path': str, 'error': str}
    info_ready = pyqtSignal(dict) # {'title': str, 'duration': str, 'thumbnail': str}

    def __init__(self, url, output_path, audio_only=False):
        super().__init__()
        self.url = url
        self.output_path = output_path
        self.audio_only = audio_only
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    @staticmethod
    def _has_ffmpeg():
        import shutil
        return shutil.which('ffmpeg') is not None

    def run(self):
        try:
            has_ffmpeg = self._has_ffmpeg()
            ydl_opts = {
                'outtmpl': os.path.join(self.output_path, '%(title)s.%(ext)s'),
                'quiet': True,
                'no_warnings': True,
                'progress_hooks': [self._progress_hook],
                'noplaylist': True,
            }

            if self.audio_only:
                if has_ffmpeg:
                    ydl_opts['format'] = 'bestaudio/best'
                    ydl_opts['postprocessors'] = [{
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'mp3',
                        'preferredquality': '192',
                    }]
                else:
                    ydl_opts['format'] = 'bestaudio/best'
            else:
                if has_ffmpeg:
                    ydl_opts['format'] = 'bestvideo+bestaudio/best'
                    ydl_opts['merge_output_format'] = 'mp4'
                else:
                    # ffmpeg 없으면 머지 불필요한 단일 포맷
                    ydl_opts['format'] = 'best'

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # 먼저 정보 추출
                info = ydl.extract_info(self.url, download=False)
                title = info.get('title', 'Unknown')
                duration = info.get('duration')
                dur_str = f"{int(duration) // 60}:{int(duration) % 60:02d}" if duration else ""
                self.info_ready.emit({
                    'title': title,
                    'duration': dur_str,
                    'thumbnail': info.get('thumbnail', ''),
                })

                if self._cancelled:
                    self.finished.emit({'success': False, 'title': title, 'path': '', 'error': 'Cancelled'})
                    return

                # 다운로드 실행
                ydl.download([self.url])

            if not self._cancelled:
                self.finished.emit({'success': True, 'title': title, 'path': self.output_path, 'error': ''})
        except Exception as e:
            self.finished.emit({'success': False, 'title': '', 'path': '', 'error': str(e)})

    def _progress_hook(self, d):
        if self._cancelled:
            raise yt_dlp.utils.DownloadCancelled()
        if d['status'] == 'downloading':
            percent = 0.0
            if d.get('total_bytes'):
                percent = d.get('downloaded_bytes', 0) / d['total_bytes'] * 100
            elif d.get('total_bytes_estimate'):
                percent = d.get('downloaded_bytes', 0) / d['total_bytes_estimate'] * 100
            speed = d.get('_speed_str', '').strip()
            eta = d.get('_eta_str', '').strip()
            self.progress.emit({'percent': percent, 'speed': speed, 'eta': eta})
        elif d['status'] == 'finished':
            self.progress.emit({'percent': 100.0, 'speed': '', 'eta': ''})


class DouyinDownloadWorker(QThread):
    """도우인/틱톡 다운로드 워커 (맥미니 Douyin Worker API 경유)"""
    progress = pyqtSignal(dict)
    finished = pyqtSignal(dict)
    info_ready = pyqtSignal(dict)

    WORKER_API = "https://douyin.tubiq.net"

    def __init__(self, url, output_path):
        super().__init__()
        self.url = url
        self.output_path = output_path
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            # 1) 도우인 워커에서 영상 정보 가져오기
            import urllib.request
            import urllib.parse
            api_url = f"{self.WORKER_API}/api/hybrid/video_data?url={urllib.parse.quote(self.url, safe='')}&minimal=false"
            req = urllib.request.Request(api_url, headers={'User-Agent': 'Q-fred Downloader'})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode('utf-8'))

            if data.get('code') != 200 or not data.get('data'):
                self.finished.emit({'success': False, 'title': '', 'path': '', 'error': '도우인 영상 정보를 가져올 수 없습니다'})
                return

            vdata = data['data']
            title = vdata.get('desc', '') or 'douyin_video'
            # 파일명에 쓸 수 없는 문자 제거
            safe_title = "".join(c for c in title if c not in r'\/:*?"<>|').strip()[:80] or 'douyin_video'
            duration = vdata.get('duration', 0)
            if isinstance(duration, (int, float)) and duration > 0:
                dur_sec = int(duration / 1000) if duration > 1000 else int(duration)
                dur_str = f"{dur_sec // 60}:{dur_sec % 60:02d}"
            else:
                dur_str = ""

            # 썸네일
            thumb = ""
            video_info = vdata.get('video', {})
            cover = video_info.get('cover', {})
            if isinstance(cover, dict) and cover.get('url_list'):
                thumb = cover['url_list'][0]

            self.info_ready.emit({'title': safe_title, 'duration': dur_str, 'thumbnail': thumb})

            if self._cancelled:
                self.finished.emit({'success': False, 'title': safe_title, 'path': '', 'error': 'Cancelled'})
                return

            # 2) 다운로드 URL 추출
            download_url = None
            play_addr = video_info.get('play_addr', {})
            if isinstance(play_addr, dict) and play_addr.get('url_list'):
                download_url = play_addr['url_list'][0]

            if not download_url:
                # download_addr 시도
                dl_addr = video_info.get('download_addr', {})
                if isinstance(dl_addr, dict) and dl_addr.get('url_list'):
                    download_url = dl_addr['url_list'][0]

            if not download_url:
                self.finished.emit({'success': False, 'title': safe_title, 'path': '', 'error': '다운로드 URL을 찾을 수 없습니다'})
                return

            # 3) 영상 파일 다운로드
            os.makedirs(self.output_path, exist_ok=True)
            file_path = os.path.join(self.output_path, f"{safe_title}.mp4")

            req2 = urllib.request.Request(download_url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Referer': 'https://www.douyin.com/',
            })
            with urllib.request.urlopen(req2, timeout=120) as resp2:
                total = int(resp2.headers.get('Content-Length', 0))
                downloaded = 0
                block = 8192
                with open(file_path, 'wb') as f:
                    while True:
                        if self._cancelled:
                            self.finished.emit({'success': False, 'title': safe_title, 'path': '', 'error': 'Cancelled'})
                            return
                        chunk = resp2.read(block)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total > 0:
                            pct = downloaded / total * 100
                            speed = ""
                            self.progress.emit({'percent': pct, 'speed': speed, 'eta': ''})

            self.progress.emit({'percent': 100.0, 'speed': '', 'eta': ''})
            self.finished.emit({'success': True, 'title': safe_title, 'path': self.output_path, 'error': ''})

        except Exception as e:
            self.finished.emit({'success': False, 'title': '', 'path': '', 'error': str(e)})


class DownloadItemCard(QFrame):
    """다운로드 큐 아이템 카드"""
    cancelClicked = pyqtSignal(str)  # item_id

    def __init__(self, item_id, url, output_path="", parent=None):
        super().__init__(parent)
        self.item_id = item_id
        self.url = url
        self.output_path = output_path
        self.setMinimumHeight(72)
        self.setMaximumHeight(100)
        self.setStyleSheet("""
            QFrame {
                background-color: #1e293b;
                border: 1px solid #334155;
                border-radius: 8px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)

        # 상단: 제목 + 취소 버튼
        top = QHBoxLayout()
        self.title_label = QLabel("정보 가져오는 중...")
        self.title_label.setStyleSheet("color: #e2e8f0; font-size: 12px; font-weight: bold; border: none; background: transparent;")
        self.title_label.setMaximumWidth(400)
        top.addWidget(self.title_label)
        top.addStretch()

        self.status_label = QLabel("대기")
        self.status_label.setStyleSheet("color: #94a3b8; font-size: 11px; border: none; background: transparent;")
        top.addWidget(self.status_label)

        self.cancel_btn = QPushButton("\u2715")
        self.cancel_btn.setFixedSize(20, 20)
        self.cancel_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.cancel_btn.setStyleSheet("""
            QPushButton { background: transparent; border: none; color: #64748b; font-size: 12px; }
            QPushButton:hover { color: #ef4444; }
        """)
        self.cancel_btn.clicked.connect(lambda: self.cancelClicked.emit(self.item_id))
        top.addWidget(self.cancel_btn)
        layout.addLayout(top)

        # 하단: 프로그레스바 + 속도
        bottom = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                background-color: #334155;
                border: none;
                border-radius: 3px;
            }
            QProgressBar::chunk {
                background-color: #4a946c;
                border-radius: 3px;
            }
        """)
        bottom.addWidget(self.progress_bar)

        self.speed_label = QLabel("")
        self.speed_label.setFixedWidth(100)
        self.speed_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.speed_label.setStyleSheet("color: #64748b; font-size: 10px; border: none; background: transparent;")
        bottom.addWidget(self.speed_label)
        layout.addLayout(bottom)

    def set_title(self, title):
        display = title if len(title) <= 50 else title[:47] + "..."
        self.title_label.setText(display)
        self.title_label.setToolTip(title)

    def set_progress(self, percent, speed="", eta=""):
        self.progress_bar.setValue(int(percent))
        self.status_label.setText("다운로드 중")
        self.status_label.setStyleSheet("color: #4a946c; font-size: 11px; border: none; background: transparent;")
        info = speed
        if eta:
            info += f" | {eta}"
        self.speed_label.setText(info)

    def set_finished(self, success, error=""):
        self.cancel_btn.hide()
        if success:
            self.progress_bar.setValue(100)
            self.status_label.setText("완료")
            self.status_label.setStyleSheet("color: #34d399; font-size: 11px; border: none; background: transparent;")
            self.speed_label.setText("")
            self.progress_bar.setStyleSheet("""
                QProgressBar { background-color: #334155; border: none; border-radius: 3px; }
                QProgressBar::chunk { background-color: #34d399; border-radius: 3px; }
            """)
            # 폴더 열기 버튼
            open_btn = QPushButton("\U0001f4c2 폴더 열기")
            open_btn.setFixedHeight(24)
            open_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            open_btn.setStyleSheet("""
                QPushButton {
                    background-color: #334155; border: none; border-radius: 4px;
                    color: #94a3b8; font-size: 11px; padding: 0 10px;
                }
                QPushButton:hover { background-color: #475569; color: #ffffff; }
            """)
            open_btn.clicked.connect(lambda: self._open_folder())
            self.layout().addWidget(open_btn)
        else:
            self.status_label.setText("실패")
            self.status_label.setStyleSheet("color: #ef4444; font-size: 11px; border: none; background: transparent;")
            self.speed_label.setText(error[:30] if error else "")
            self.progress_bar.setStyleSheet("""
                QProgressBar { background-color: #334155; border: none; border-radius: 3px; }
                QProgressBar::chunk { background-color: #ef4444; border-radius: 3px; }
            """)

    def _open_folder(self):
        path = self.output_path
        if path and os.path.isdir(path):
            os.startfile(path)

    def set_cancelled(self):
        self.cancel_btn.hide()
        self.status_label.setText("취소됨")
        self.status_label.setStyleSheet("color: #f59e0b; font-size: 11px; border: none; background: transparent;")
        self.speed_label.setText("")


class DownloaderSettingsDialog(QDialog):
    """다운로더 전용 설정 다이얼로그"""

    def __init__(self, app_settings, parent=None):
        super().__init__(parent)
        self.app_settings = app_settings
        self.setWindowTitle("다운로더 설정")
        self.setFixedSize(500, 480)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowMaximizeButtonHint)
        self.setSizeGripEnabled(False)
        self.setStyleSheet("""
            QDialog { background-color: #0f172a; }
            QLabel { color: #e2e8f0; }
            QLineEdit {
                background-color: #1e293b; border: 1px solid #334155;
                border-radius: 6px; padding: 8px 12px; color: #ffffff;
                font-size: 13px; min-height: 20px;
            }
            QLineEdit:focus { border-color: #4a946c; }
            QPushButton {
                background-color: #334155; border: none; border-radius: 6px;
                padding: 10px 20px; color: #ffffff; font-size: 13px; min-height: 20px;
            }
            QPushButton:hover { background-color: #475569; }
        """)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 24)
        layout.setSpacing(8)

        title = QLabel("다운로더 설정")
        title.setStyleSheet("font-size: 16px; font-weight: bold; color: #ffffff;")
        layout.addWidget(title)
        layout.addSpacing(8)

        # 다운로드 폴더
        fl = QLabel("다운로드 저장 폴더")
        fl.setStyleSheet("font-size: 12px; color: #94a3b8;")
        layout.addWidget(fl)
        layout.addSpacing(4)

        ff = QFrame()
        ff.setStyleSheet("QFrame { background: transparent; }")
        ffl = QHBoxLayout(ff)
        ffl.setContentsMargins(0, 0, 0, 0)
        ffl.setSpacing(8)

        self.dl_folder_input = QLineEdit()
        self.dl_folder_input.setText(self.app_settings.download_folder)
        self.dl_folder_input.setFixedHeight(38)
        self.dl_folder_input.setReadOnly(True)
        ffl.addWidget(self.dl_folder_input)

        browse_btn = QPushButton("찾아보기")
        browse_btn.setFixedSize(100, 38)
        browse_btn.clicked.connect(self._browse_folder)
        ffl.addWidget(browse_btn)
        layout.addWidget(ff)
        layout.addSpacing(16)

        # 구분선
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("background-color: #334155;")
        line.setFixedHeight(1)
        layout.addWidget(line)
        layout.addSpacing(16)

        # 그룹 관리
        gl = QLabel("다운로드 그룹")
        gl.setStyleSheet("font-size: 12px; color: #94a3b8;")
        layout.addWidget(gl)
        gd = QLabel("그룹별 하위 폴더에 다운로드가 저장됩니다")
        gd.setStyleSheet("font-size: 11px; color: #64748b;")
        layout.addWidget(gd)
        layout.addSpacing(4)

        self.group_list = QListWidget()
        self.group_list.setFixedHeight(100)
        self.group_list.setStyleSheet("""
            QListWidget {
                background-color: #1e293b; border: 1px solid #334155;
                border-radius: 6px; color: #ffffff; font-size: 12px; padding: 4px;
            }
            QListWidget::item { padding: 2px 4px; }
            QListWidget::item:selected { background-color: #334155; }
            QScrollBar:vertical {
                background: transparent;
                width: 10px;
                border: none;
                margin: 0px;
                padding: 2px;
            }
            QScrollBar::handle:vertical {
                background: #475569;
                border: 2px solid #1e293b;
                border-radius: 5px;
                min-height: 20px;
            }
            QScrollBar::handle:vertical:hover {
                background: #64748b;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px; border: none;
            }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: transparent;
            }
        """)
        for g in self.app_settings.download_groups:
            name = g['name']
            folder = g['folder'] or '(루트)'
            self.group_list.addItem(f"{name}  →  {folder}")
        layout.addWidget(self.group_list)

        # 그룹 추가/삭제
        gbl = QHBoxLayout()
        gbl.setSpacing(8)

        self.grp_name_input = QLineEdit()
        self.grp_name_input.setPlaceholderText("그룹 이름")
        self.grp_name_input.setFixedHeight(32)
        self.grp_name_input.setStyleSheet("min-height: 16px; font-size: 12px;")
        gbl.addWidget(self.grp_name_input)

        self.grp_folder_input = QLineEdit()
        self.grp_folder_input.setPlaceholderText("폴더명")
        self.grp_folder_input.setFixedHeight(32)
        self.grp_folder_input.setFixedWidth(100)
        self.grp_folder_input.setStyleSheet("min-height: 16px; font-size: 12px;")
        gbl.addWidget(self.grp_folder_input)

        add_btn = QPushButton("+")
        add_btn.setFixedSize(32, 32)
        add_btn.setStyleSheet("min-height: 16px; font-size: 14px; font-weight: bold;")
        add_btn.clicked.connect(self._add_group)
        gbl.addWidget(add_btn)

        del_btn = QPushButton("-")
        del_btn.setFixedSize(32, 32)
        del_btn.setStyleSheet("min-height: 16px; font-size: 14px; font-weight: bold;")
        del_btn.clicked.connect(self._del_group)
        gbl.addWidget(del_btn)

        layout.addLayout(gbl)
        layout.addStretch()

        # 버튼 영역
        btn_frame = QFrame()
        btn_frame.setStyleSheet("QFrame { background: transparent; }")
        btn_layout = QHBoxLayout(btn_frame)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(12)
        btn_layout.addStretch()

        save_btn = QPushButton("저장")
        save_btn.setFixedSize(100, 40)
        save_btn.setStyleSheet("""
            QPushButton { background-color: #4a946c; font-weight: bold; min-height: 20px; }
            QPushButton:hover { background-color: #5db684; }
        """)
        save_btn.clicked.connect(self._save)
        btn_layout.addWidget(save_btn)

        cancel_btn = QPushButton("취소")
        cancel_btn.setFixedSize(100, 40)
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        layout.addWidget(btn_frame)

    def _browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "다운로드 저장 폴더 선택", self.dl_folder_input.text())
        if folder:
            self.dl_folder_input.setText(folder)

    def _add_group(self):
        name = self.grp_name_input.text().strip()
        folder = self.grp_folder_input.text().strip() or name
        if not name:
            return
        display_folder = folder or '(루트)'
        self.group_list.addItem(f"{name}  →  {display_folder}")
        self.grp_name_input.clear()
        self.grp_folder_input.clear()

    def _del_group(self):
        row = self.group_list.currentRow()
        if row >= 0:
            self.group_list.takeItem(row)

    def _save(self):
        self.app_settings.download_folder = self.dl_folder_input.text()
        groups = []
        for i in range(self.group_list.count()):
            text = self.group_list.item(i).text()
            parts = text.split("\u2192")
            name = parts[0].strip()
            folder = parts[1].strip() if len(parts) > 1 else ""
            if folder == "(\ub8e8\ud2b8)":
                folder = ""
            groups.append({"name": name, "folder": folder})
        self.app_settings.download_groups = groups
        self.accept()


class DownloaderPage(QWidget):
    """다운로더 페이지"""

    COMBO_STYLE = """
        QComboBox {
            background-color: #1e293b;
            border: 1px solid #334155;
            border-radius: 6px;
            color: #ffffff;
            font-size: 12px;
            padding: 4px 8px;
            min-height: 28px;
        }
        QComboBox:hover { border-color: #4a946c; }
        QComboBox::drop-down {
            border: none;
            width: 20px;
        }
        QComboBox::down-arrow {
            image: none;
            border: none;
        }
        QComboBox QAbstractItemView {
            background-color: #1e293b;
            border: 1px solid #334155;
            color: #ffffff;
            selection-background-color: #334155;
        }
    """

    def __init__(self, app_settings=None, parent=None):
        super().__init__(parent)
        self.app_settings = app_settings
        self.setStyleSheet("background-color: #0f172a;")
        self.workers = {}  # item_id -> DownloadWorker
        self.cards = {}    # item_id -> DownloadItemCard
        self.queue_count = 0
        self.empty_widget = None
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        # 헤더 + 설정 버튼
        header_layout = QHBoxLayout()
        header = QLabel("Downloader")
        header.setStyleSheet("font-size: 20px; font-weight: bold; color: #ffffff;")
        header_layout.addWidget(header)
        header_layout.addStretch()

        settings_btn = QPushButton("\u2699")
        settings_btn.setFixedSize(32, 32)
        settings_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        settings_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent; border: none;
                color: #94a3b8; font-size: 18px; border-radius: 6px;
            }
            QPushButton:hover { background-color: #334155; color: #ffffff; }
        """)
        settings_btn.clicked.connect(self._open_settings)
        header_layout.addWidget(settings_btn)
        layout.addLayout(header_layout)

        subtitle = QLabel("YouTube, Instagram, TikTok 등 URL을 입력하면 미디어를 다운로드합니다")
        subtitle.setStyleSheet("font-size: 12px; color: #94a3b8;")
        layout.addWidget(subtitle)

        # 옵션 바: 형식 + 그룹
        opt_layout = QHBoxLayout()
        opt_layout.setSpacing(8)

        fmt_label = QLabel("형식")
        fmt_label.setStyleSheet("color: #94a3b8; font-size: 11px;")
        opt_layout.addWidget(fmt_label)

        self.format_combo = QComboBox()
        self.format_combo.addItems(["영상 (MP4)", "오디오 (MP3)"])
        self.format_combo.setFixedWidth(130)
        self.format_combo.setStyleSheet(self.COMBO_STYLE)
        opt_layout.addWidget(self.format_combo)

        opt_layout.addSpacing(12)

        grp_label = QLabel("그룹")
        grp_label.setStyleSheet("color: #94a3b8; font-size: 11px;")
        opt_layout.addWidget(grp_label)

        self.group_combo = QComboBox()
        self.group_combo.setFixedWidth(150)
        self.group_combo.setStyleSheet(self.COMBO_STYLE)
        self._refresh_groups()
        opt_layout.addWidget(self.group_combo)

        opt_layout.addStretch()
        layout.addLayout(opt_layout)

        # URL 입력 바
        url_frame = QFrame()
        url_frame.setStyleSheet("""
            QFrame {
                background-color: #1e293b;
                border: 1px solid #334155;
                border-radius: 8px;
            }
        """)
        url_layout = QHBoxLayout(url_frame)
        url_layout.setContentsMargins(12, 4, 4, 4)
        url_layout.setSpacing(8)

        link_icon = QLabel("\U0001f517")
        link_icon.setStyleSheet("font-size: 16px; background: transparent; border: none;")
        url_layout.addWidget(link_icon)

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("URL을 입력하세요...")
        self.url_input.setStyleSheet("""
            QLineEdit {
                background-color: transparent;
                border: none;
                color: #ffffff;
                font-size: 14px;
                padding: 8px 0;
            }
        """)
        self.url_input.returnPressed.connect(self.on_download)
        url_layout.addWidget(self.url_input)

        dl_btn = QPushButton("\u2b07  Download")
        dl_btn.setFixedHeight(36)
        dl_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        dl_btn.setStyleSheet("""
            QPushButton {
                background-color: #4a946c;
                border: none;
                border-radius: 6px;
                color: #ffffff;
                font-size: 13px;
                font-weight: bold;
                padding: 0 20px;
            }
            QPushButton:hover {
                background-color: #5db684;
            }
        """)
        dl_btn.clicked.connect(self.on_download)
        url_layout.addWidget(dl_btn)

        layout.addWidget(url_frame)

        # 다운로드 큐 헤더
        q_header = QHBoxLayout()
        q_label = QLabel("DOWNLOAD QUEUE")
        q_label.setStyleSheet("font-size: 10px; font-weight: bold; color: #64748b;")
        q_header.addWidget(q_label)
        q_header.addStretch()
        self.q_count = QLabel("0")
        self.q_count.setFixedSize(22, 22)
        self.q_count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.q_count.setStyleSheet("""
            background-color: #1e293b; color: #64748b;
            border-radius: 11px; font-size: 10px; font-weight: bold;
        """)
        q_header.addWidget(self.q_count)
        layout.addLayout(q_header)

        # 다운로드 큐 리스트 (스크롤)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("""
            QScrollArea { background-color: transparent; border: none; }
            QScrollBar:vertical {
                background-color: transparent; width: 6px; margin: 0px;
            }
            QScrollBar::handle:vertical {
                background-color: #334155; border-radius: 3px; min-height: 20px;
            }
            QScrollBar::handle:vertical:hover { background-color: #475569; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
        """)

        queue_w = QWidget()
        self.queue_layout = QVBoxLayout(queue_w)
        self.queue_layout.setContentsMargins(0, 0, 0, 0)
        self.queue_layout.setSpacing(8)

        # 빈 상태 표시
        self.empty_widget = QFrame()
        self.empty_widget.setStyleSheet("""
            QFrame {
                background-color: #1e293b;
                border: 1px dashed #334155;
                border-radius: 12px;
            }
        """)
        el = QVBoxLayout(self.empty_widget)
        el.setContentsMargins(40, 60, 40, 60)
        el.setAlignment(Qt.AlignmentFlag.AlignCenter)

        ei = QLabel("\u2b07")
        ei.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ei.setStyleSheet("font-size: 36px; color: #334155; background: transparent; border: none;")
        el.addWidget(ei)

        et = QLabel("다운로드 대기열이 비어있습니다")
        et.setAlignment(Qt.AlignmentFlag.AlignCenter)
        et.setStyleSheet("font-size: 13px; color: #475569; background: transparent; border: none;")
        el.addWidget(et)

        eh = QLabel("위 입력창에 URL을 붙여넣고 다운로드 버튼을 누르세요")
        eh.setAlignment(Qt.AlignmentFlag.AlignCenter)
        eh.setStyleSheet("font-size: 11px; color: #334155; background: transparent; border: none;")
        el.addWidget(eh)

        self.queue_layout.addWidget(self.empty_widget)
        self.queue_layout.addStretch()

        scroll.setWidget(queue_w)
        layout.addWidget(scroll, 1)

        # 하단 상태 바
        status = QFrame()
        status.setFixedHeight(36)
        status.setStyleSheet("""
            QFrame {
                background-color: #1e293b;
                border: 1px solid #334155;
                border-radius: 6px;
            }
        """)
        sl = QHBoxLayout(status)
        sl.setContentsMargins(12, 0, 12, 0)

        self.status_dot = QLabel("\u25cf")
        self.status_dot.setStyleSheet("color: #64748b; font-size: 8px; border: none; background: transparent;")
        sl.addWidget(self.status_dot)

        self.status_text = QLabel("대기 중")
        self.status_text.setStyleSheet("color: #94a3b8; font-size: 11px; border: none; background: transparent;")
        sl.addWidget(self.status_text)

        sl.addStretch()

        dl_folder = self.app_settings.download_folder if self.app_settings else "~/Downloads"
        self.path_label = QLabel(f"저장: {dl_folder}")
        self.path_label.setStyleSheet("color: #64748b; font-size: 11px; border: none; background: transparent;")
        sl.addWidget(self.path_label)

        layout.addWidget(status)

    def _refresh_groups(self):
        self.group_combo.clear()
        if self.app_settings:
            for g in self.app_settings.download_groups:
                self.group_combo.addItem(g["name"])
        else:
            self.group_combo.addItem("General")

    @staticmethod
    def extract_douyin_url(text):
        """도우인/틱톡 공유 텍스트에서 실제 URL을 추출"""
        import re
        # 도우인/틱톡 URL 패턴 매칭
        patterns = [
            r'https?://v\.douyin\.com/[^\s]+',
            r'https?://www\.douyin\.com/[^\s]+',
            r'https?://douyin\.com/[^\s]+',
            r'https?://vt\.tiktok\.com/[^\s]+',
            r'https?://www\.tiktok\.com/[^\s]+',
            r'https?://tiktok\.com/[^\s]+',
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                url = match.group(0).rstrip('/')
                # 끝에 붙은 중국어/특수문자 제거
                url = re.sub(r'[^\x00-\x7F]+$', '', url).rstrip('/')
                return url
        return None

    def on_download(self):
        raw_text = self.url_input.text().strip()
        if not raw_text:
            return

        self.url_input.clear()

        # 도우인/틱톡 공유 텍스트에서 URL 추출
        extracted = self.extract_douyin_url(raw_text)
        if extracted:
            url = extracted
        else:
            url = raw_text

        # 빈 상태 위젯 숨기기
        if self.empty_widget and self.empty_widget.isVisible():
            self.empty_widget.hide()

        # 형식 & 그룹
        audio_only = self.format_combo.currentIndex() == 1
        group_name = self.group_combo.currentText()

        # 저장 경로
        if self.app_settings:
            output_path = self.app_settings.get_download_path(group_name)
        else:
            output_path = os.path.join(os.path.expanduser('~'), 'Downloads')

        # 아이템 ID
        item_id = str(uuid.uuid4())[:8]

        # 카드 생성
        card = DownloadItemCard(item_id, url, output_path=output_path)
        card.cancelClicked.connect(self.on_cancel)
        self.queue_layout.insertWidget(self.queue_layout.count() - 1, card)
        self.cards[item_id] = card

        # 카운트 업데이트
        self.queue_count += 1
        self.q_count.setText(str(self.queue_count))

        # 상태 바 업데이트
        self.status_dot.setStyleSheet("color: #4a946c; font-size: 8px; border: none; background: transparent;")
        self.status_text.setText("다운로드 중...")
        self.path_label.setText(f"저장: {output_path}")

        # 워커 시작 (도우인/틱톡이면 DouyinDownloadWorker, 나머지는 yt-dlp)
        is_douyin = any(k in url.lower() for k in ['douyin.com', 'v.douyin.com', 'tiktok.com', 'vt.tiktok.com'])
        if is_douyin:
            worker = DouyinDownloadWorker(url, output_path)
        else:
            worker = DownloadWorker(url, output_path, audio_only)
        worker.info_ready.connect(lambda info, c=card: c.set_title(info['title']))
        worker.progress.connect(lambda p, c=card: c.set_progress(p['percent'], p.get('speed', ''), p.get('eta', '')))
        worker.finished.connect(lambda r, iid=item_id: self._on_finished(iid, r))
        self.workers[item_id] = worker
        worker.start()

    def on_cancel(self, item_id):
        worker = self.workers.get(item_id)
        if worker:
            worker.cancel()
        card = self.cards.get(item_id)
        if card:
            card.set_cancelled()

    def _on_finished(self, item_id, result):
        card = self.cards.get(item_id)
        if card:
            card.set_finished(result['success'], result.get('error', ''))

        # 워커 정리
        worker = self.workers.pop(item_id, None)
        if worker:
            worker.deleteLater()

        # 활성 다운로드가 없으면 상태 복원
        active = any(w.isRunning() for w in self.workers.values())
        if not active:
            self.status_dot.setStyleSheet("color: #64748b; font-size: 8px; border: none; background: transparent;")
            self.status_text.setText("대기 중")

    def _open_settings(self):
        dialog = DownloaderSettingsDialog(self.app_settings, self)
        if dialog.exec():
            self._refresh_groups()
            self.path_label.setText(f"저장: {self.app_settings.download_folder}")


class ColorPickerPage(QWidget):
    """컬러 픽커 페이지 - 스포이드로 화면 색상 추출"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._picked_color = QColor("#6c5ce7")
        self._history: list[str] = []
        self._is_picking = False
        self._init_ui()

    @staticmethod
    def _svg_to_pixmap(svg_str: str, size: int) -> 'QPixmap':
        from PyQt6.QtSvg import QSvgRenderer
        from PyQt6.QtGui import QPixmap, QPainter
        renderer = QSvgRenderer(svg_str.strip().encode())
        pix = QPixmap(size, size)
        pix.fill(QColor(0, 0, 0, 0))
        p = QPainter(pix)
        renderer.render(p)
        p.end()
        return pix

    _EYEDROPPER_SVG = """<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M21.17 2.83a2.83 2.83 0 0 0-4 0l-2.12 2.12-1.42-1.42-1.41 1.42 1.41 1.41-7.78 7.78a2 2 0 0 0-.59 1.42V18h2.44a2 2 0 0 0 1.42-.59l7.78-7.78 1.41 1.41 1.42-1.41-1.42-1.42 2.12-2.12a2.83 2.83 0 0 0 0-4Z"
            stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" fill="none"/>
      <path d="M2 22l3-3" stroke="{color}" stroke-width="1.8" stroke-linecap="round"/>
    </svg>"""

    _PALETTE_DATA = {
        'tailwind': [
            ('#ef4444','Red'),('#f97316','Orange'),('#f59e0b','Amber'),('#eab308','Yellow'),
            ('#84cc16','Lime'),('#22c55e','Green'),('#10b981','Emerald'),('#14b8a6','Teal'),
            ('#06b6d4','Cyan'),('#0ea5e9','Sky'),('#3b82f6','Blue'),('#6366f1','Indigo'),
            ('#8b5cf6','Violet'),('#a855f7','Purple'),('#d946ef','Fuchsia'),('#ec4899','Pink'),
            ('#f43f5e','Rose'),('#64748b','Slate'),('#6b7280','Gray'),('#78716c','Stone'),
        ],
        'material': [
            ('#F44336','Red'),('#E91E63','Pink'),('#9C27B0','Purple'),('#673AB7','D.Purple'),
            ('#3F51B5','Indigo'),('#2196F3','Blue'),('#03A9F4','L.Blue'),('#00BCD4','Cyan'),
            ('#009688','Teal'),('#4CAF50','Green'),('#8BC34A','L.Green'),('#CDDC39','Lime'),
            ('#FFEB3B','Yellow'),('#FFC107','Amber'),('#FF9800','Orange'),('#FF5722','D.Orange'),
            ('#795548','Brown'),('#9E9E9E','Grey'),('#607D8B','B.Grey'),('#000000','Black'),
        ],
        'pastel': [
            ('#FFB3BA','Rose'),('#FFDFBA','Peach'),('#FFFFBA','Cream'),('#BAFFC9','Mint'),
            ('#BAE1FF','Sky'),('#E8D5B7','Sand'),('#C9E4DE','Sage'),('#FADDE1','Blush'),
            ('#FFF5BA','Butter'),('#C3B1E1','Lavender'),('#F9C9D6','Pink'),('#B5EAD7','Seafoam'),
            ('#C7CEEA','Periwinkle'),('#FFDAC1','Apricot'),('#D4A5A5','Mauve'),('#FFE5B4','Mango'),
            ('#D5E8D4','Pistachio'),('#F2D7D5','Coral'),('#DAEAF6','Ice'),('#E0BBE4','Orchid'),
        ],
    }

    def _init_ui(self):
        self.setStyleSheet("background-color: #0f172a;")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            "QScrollArea{background:transparent;border:none;}"
            "QScrollBar:vertical{background:#0f172a;width:6px;}"
            "QScrollBar::handle:vertical{background:#334155;border-radius:3px;min-height:30px;}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}"
        )
        content = QWidget()
        content.setStyleSheet("background:transparent;")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(16)
        scroll.setWidget(content)
        outer.addWidget(scroll)

        header = QHBoxLayout()
        header.setSpacing(12)
        icon_label = QLabel()
        icon_label.setPixmap(self._svg_to_pixmap(self._EYEDROPPER_SVG.replace("{color}", "#a78bfa"), 28))
        icon_label.setFixedSize(28, 28)
        icon_label.setStyleSheet("background: transparent; border: none;")
        header.addWidget(icon_label)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title = QLabel("Color Picker")
        title.setStyleSheet("color: #f8fafc; font-size: 20px; font-weight: bold; background: transparent; border: none;")
        title_col.addWidget(title)
        desc = QLabel("화면 아무 곳이나 클릭해서 색상 코드를 추출합니다")
        desc.setStyleSheet("color: #64748b; font-size: 11px; background: transparent; border: none;")
        title_col.addWidget(desc)
        header.addLayout(title_col, 1)
        layout.addLayout(header)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background: #1e293b; border: none; max-height: 1px;")
        layout.addWidget(sep)

        preview_row = QHBoxLayout()
        preview_row.setSpacing(20)
        self.color_preview = QFrame()
        self.color_preview.setFixedSize(140, 140)
        self._set_preview_color(self._picked_color.name())
        preview_row.addWidget(self.color_preview)

        codes_layout = QVBoxLayout()
        codes_layout.setSpacing(8)
        self._hex_row = self._make_code_row("HEX", self._picked_color.name().upper())
        self._rgb_row = self._make_code_row("RGB", f"{self._picked_color.red()}, {self._picked_color.green()}, {self._picked_color.blue()}")
        h_v = self._picked_color.hslHue()
        s_v = self._picked_color.hslSaturation()
        l_v = self._picked_color.lightness()
        self._hsl_row = self._make_code_row("HSL", f"{max(h_v, 0)}°, {round(s_v / 255 * 100)}%, {round(l_v / 255 * 100)}%")
        codes_layout.addLayout(self._hex_row["layout"])
        codes_layout.addLayout(self._rgb_row["layout"])
        codes_layout.addLayout(self._hsl_row["layout"])
        codes_layout.addStretch()
        preview_row.addLayout(codes_layout, 1)
        layout.addLayout(preview_row)

        pick_btn = QPushButton()
        pick_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        pick_btn.setFixedHeight(52)
        btn_layout = QHBoxLayout(pick_btn)
        btn_layout.setContentsMargins(20, 0, 20, 0)
        btn_layout.setSpacing(10)
        btn_icon = QLabel()
        btn_icon.setPixmap(self._svg_to_pixmap(self._EYEDROPPER_SVG.replace("{color}", "#ffffff"), 22))
        btn_icon.setFixedSize(22, 22)
        btn_icon.setStyleSheet("background: transparent; border: none;")
        btn_layout.addWidget(btn_icon)
        btn_text = QLabel("스포이드로 색상 추출")
        btn_text.setStyleSheet("color: #ffffff; font-size: 14px; font-weight: bold; background: transparent; border: none;")
        btn_layout.addWidget(btn_text)
        btn_layout.addStretch()
        shortcut_label = QLabel("단축키: F8")
        shortcut_label.setStyleSheet("color: rgba(255,255,255,0.5); font-size: 11px; background: transparent; border: none;")
        btn_layout.addWidget(shortcut_label)
        pick_btn.setStyleSheet("""
            QPushButton { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #6c5ce7, stop:1 #a78bfa); border: none; border-radius: 12px; }
            QPushButton:hover { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #7c6cf7, stop:1 #b79bff); }
            QPushButton:pressed { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #5a4bd6, stop:1 #9679e8); }
        """)
        pick_btn.clicked.connect(self._start_pick)
        layout.addWidget(pick_btn)

        from PyQt6.QtGui import QShortcut, QKeySequence
        self._shortcut = QShortcut(QKeySequence("F8"), self)
        self._shortcut.activated.connect(self._start_pick)

        hist_header = QHBoxLayout()
        hist_label = QLabel("최근 추출 색상")
        hist_label.setStyleSheet("color: #94a3b8; font-size: 12px; font-weight: bold; background: transparent; border: none;")
        hist_header.addWidget(hist_label)
        hist_header.addStretch()
        clear_btn = QPushButton("전체 삭제")
        clear_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        clear_btn.setStyleSheet("color: #475569; font-size: 10px; background: transparent; border: none;")
        clear_btn.clicked.connect(self._clear_history)
        hist_header.addWidget(clear_btn)
        layout.addLayout(hist_header)

        self._history_container = QWidget()
        self._history_container.setStyleSheet("background: transparent; border: none;")
        self.history_layout = QHBoxLayout(self._history_container)
        self.history_layout.setContentsMargins(0, 0, 0, 0)
        self.history_layout.setSpacing(6)
        self.history_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self._empty_hint = QLabel("스포이드로 색상을 추출하면 여기에 표시됩니다")
        self._empty_hint.setStyleSheet("color: #1e293b; font-size: 11px; background: transparent; border: none;")
        self.history_layout.addWidget(self._empty_hint)
        layout.addWidget(self._history_container)

        # ═══════════════════════════════════════
        # ★ Color Harmony ★
        # ═══════════════════════════════════════
        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet("background:#1e293b;border:none;max-height:1px;")
        layout.addWidget(sep2)
        h_lbl = QLabel("Color Harmony")
        h_lbl.setStyleSheet("color:#94a3b8;font-size:12px;font-weight:bold;background:transparent;border:none;")
        layout.addWidget(h_lbl)

        h_btns = QHBoxLayout(); h_btns.setSpacing(4)
        self._harmony_mode = 'complementary'
        self._harmony_btns = {}
        for mode, lbl in [('complementary','Complementary'),('analogous','Analogous'),('triadic','Triadic'),('split','Split-Comp'),('tetradic','Square')]:
            b = QPushButton(lbl); b.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            b.setFixedHeight(26); b.setCheckable(True); b.setChecked(mode == 'complementary')
            b.clicked.connect(lambda _, m=mode: self._set_harmony(m))
            self._harmony_btns[mode] = b; h_btns.addWidget(b)
        h_btns.addStretch()
        self._style_toggle_btns(self._harmony_btns, self._harmony_mode)
        layout.addLayout(h_btns)

        self._harmony_container = QWidget()
        self._harmony_container.setStyleSheet("background:transparent;border:none;")
        self._harmony_flow = QHBoxLayout(self._harmony_container)
        self._harmony_flow.setContentsMargins(0, 4, 0, 4)
        self._harmony_flow.setSpacing(8)
        layout.addWidget(self._harmony_container)
        self._refresh_harmony()

        # ═══════════════════════════════════════
        # ★ Gradient Generator ★
        # ═══════════════════════════════════════
        sep3 = QFrame(); sep3.setFrameShape(QFrame.Shape.HLine)
        sep3.setStyleSheet("background:#1e293b;border:none;max-height:1px;")
        layout.addWidget(sep3)
        g_lbl = QLabel("Gradient Generator")
        g_lbl.setStyleSheet("color:#94a3b8;font-size:12px;font-weight:bold;background:transparent;border:none;")
        layout.addWidget(g_lbl)

        self._grad_color2 = QColor("#0ea5e9")
        self._gradient_bar = QFrame()
        self._gradient_bar.setFixedHeight(48)
        layout.addWidget(self._gradient_bar)
        self._refresh_gradient()

        g_row = QHBoxLayout(); g_row.setSpacing(8)
        self._grad_css = QLabel()
        self._grad_css.setStyleSheet("color:#94a3b8;font-size:10px;font-family:Consolas;background:#1e293b;border:1px solid #334155;border-radius:6px;padding:6px 10px;")
        self._grad_css.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        g_row.addWidget(self._grad_css, 1)
        g_copy = QPushButton("Copy CSS")
        g_copy.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        g_copy.setFixedHeight(28)
        g_copy.setStyleSheet("QPushButton{background:#1e293b;color:#94a3b8;font-size:10px;border:1px solid #334155;border-radius:6px;padding:0 12px;}QPushButton:hover{background:#334155;}")
        g_copy.clicked.connect(lambda: self._copy_value(self._grad_css.text()))
        g_row.addWidget(g_copy)
        layout.addLayout(g_row)
        self._refresh_gradient()

        g_end = QHBoxLayout(); g_end.setSpacing(8)
        el = QLabel("End Color:")
        el.setStyleSheet("color:#64748b;font-size:10px;background:transparent;border:none;")
        g_end.addWidget(el)
        self._grad_end_swatch = QPushButton()
        self._grad_end_swatch.setFixedSize(28, 28)
        self._grad_end_swatch.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._grad_end_swatch.setStyleSheet(f"QPushButton{{background:{self._grad_color2.name()};border:2px solid #334155;border-radius:6px;}}QPushButton:hover{{border-color:#a78bfa;}}")
        self._grad_end_swatch.clicked.connect(self._pick_grad_end)
        g_end.addWidget(self._grad_end_swatch)
        self._grad_hex_input = QLineEdit(self._grad_color2.name().upper())
        self._grad_hex_input.setFixedWidth(90)
        self._grad_hex_input.setStyleSheet("color:#f1f5f9;font-size:11px;font-family:Consolas;background:#1e293b;border:1px solid #334155;border-radius:6px;padding:4px 8px;")
        self._grad_hex_input.returnPressed.connect(self._apply_grad_hex)
        g_end.addWidget(self._grad_hex_input)
        g_end.addStretch()
        layout.addLayout(g_end)

        # ═══════════════════════════════════════
        # ★ Preset Palettes ★
        # ═══════════════════════════════════════
        sep4 = QFrame(); sep4.setFrameShape(QFrame.Shape.HLine)
        sep4.setStyleSheet("background:#1e293b;border:none;max-height:1px;")
        layout.addWidget(sep4)
        p_lbl = QLabel("Palettes")
        p_lbl.setStyleSheet("color:#94a3b8;font-size:12px;font-weight:bold;background:transparent;border:none;")
        layout.addWidget(p_lbl)

        p_tabs = QHBoxLayout(); p_tabs.setSpacing(4)
        self._palette_mode = 'tailwind'
        self._palette_btns = {}
        for mode, lbl in [('tailwind','Tailwind'),('material','Material'),('pastel','Pastel')]:
            b = QPushButton(lbl); b.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            b.setFixedHeight(26); b.setCheckable(True); b.setChecked(mode == 'tailwind')
            b.clicked.connect(lambda _, m=mode: self._set_palette(m))
            self._palette_btns[mode] = b; p_tabs.addWidget(b)
        p_tabs.addStretch()
        self._style_toggle_btns(self._palette_btns, self._palette_mode)
        layout.addLayout(p_tabs)

        self._palette_container = QWidget()
        self._palette_container.setStyleSheet("background:transparent;border:none;")
        layout.addWidget(self._palette_container)
        self._refresh_palette()

        # ═══════════════════════════════════════
        # ★ Image → Palette ★
        # ═══════════════════════════════════════
        sep5 = QFrame(); sep5.setFrameShape(QFrame.Shape.HLine)
        sep5.setStyleSheet("background:#1e293b;border:none;max-height:1px;")
        layout.addWidget(sep5)
        i_lbl = QLabel("Image Palette")
        i_lbl.setStyleSheet("color:#94a3b8;font-size:12px;font-weight:bold;background:transparent;border:none;")
        layout.addWidget(i_lbl)

        self._img_drop = QPushButton("Click to select image or drag & drop")
        self._img_drop.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._img_drop.setFixedHeight(56)
        self._img_drop.setStyleSheet("QPushButton{background:#1e293b;color:#475569;font-size:12px;border:2px dashed #334155;border-radius:12px;}QPushButton:hover{border-color:#6c5ce7;color:#94a3b8;}")
        self._img_drop.clicked.connect(self._select_image_for_palette)
        layout.addWidget(self._img_drop)

        self._img_palette_container = QWidget()
        self._img_palette_container.setStyleSheet("background:transparent;border:none;")
        self._img_palette_flow = QHBoxLayout(self._img_palette_container)
        self._img_palette_flow.setContentsMargins(0, 4, 0, 4)
        self._img_palette_flow.setSpacing(8)
        self._img_palette_container.setVisible(False)
        layout.addWidget(self._img_palette_container)

        layout.addSpacing(20)

    def _set_preview_color(self, hex_color: str):
        self.color_preview.setStyleSheet(f"QFrame {{ background-color: {hex_color}; border-radius: 70px; border: 3px solid #334155; }}")

    def _make_code_row(self, label_text, value_text):
        row = QHBoxLayout()
        row.setSpacing(8)
        label = QLabel(label_text)
        label.setFixedWidth(34)
        label.setStyleSheet("color: #64748b; font-size: 10px; font-weight: bold; background: transparent; border: none;")
        row.addWidget(label)
        value = QLabel(value_text)
        value.setStyleSheet("color: #f1f5f9; font-size: 13px; font-family: Consolas, monospace; background: #1e293b; border: 1px solid #334155; border-radius: 6px; padding: 5px 10px;")
        value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(value, 1)
        copy_btn = QPushButton("\U0001f4cb")
        copy_btn.setToolTip(f"{label_text} 값 복사")
        copy_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        copy_btn.setFixedSize(32, 28)
        copy_btn.setStyleSheet("QPushButton { background: #1e293b; color: #94a3b8; font-size: 12px; border: 1px solid #334155; border-radius: 6px; } QPushButton:hover { background: #334155; }")
        copy_btn.clicked.connect(lambda: self._copy_value(value.text()))
        row.addWidget(copy_btn)
        return {"layout": row, "value": value}

    def _copy_value(self, text):
        try:
            pyperclip.copy(text)
        except Exception:
            pass

    def _update_display(self, color: QColor):
        self._picked_color = color
        hex_val = color.name().upper()
        rgb_val = f"{color.red()}, {color.green()}, {color.blue()}"
        h, s, l = color.hslHue(), color.hslSaturation(), color.lightness()
        hsl_val = f"{max(h, 0)}\u00b0, {round(s / 255 * 100)}%, {round(l / 255 * 100)}%"
        self._set_preview_color(hex_val)
        self._hex_row["value"].setText(hex_val)
        self._rgb_row["value"].setText(rgb_val)
        self._hsl_row["value"].setText(hsl_val)
        # 연동: 조화/그라디언트 갱신
        if hasattr(self, '_harmony_flow'):
            self._refresh_harmony()
        if hasattr(self, '_gradient_bar'):
            self._refresh_gradient()
        if not self._history or self._history[0] != hex_val:
            self._history.insert(0, hex_val)
            if len(self._history) > 20:
                self._history.pop()
            self._rebuild_history()
        try:
            pyperclip.copy(hex_val)
        except Exception:
            pass

    def _rebuild_history(self):
        while self.history_layout.count():
            item = self.history_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for hex_color in self._history:
            swatch = QPushButton()
            swatch.setFixedSize(34, 34)
            swatch.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            swatch.setToolTip(hex_color)
            swatch.setStyleSheet(f"QPushButton {{ background-color: {hex_color}; border: 2px solid #1e293b; border-radius: 8px; }} QPushButton:hover {{ border-color: #a78bfa; }}")
            swatch.clicked.connect(lambda checked, c=hex_color: self._update_display(QColor(c)))
            self.history_layout.addWidget(swatch)

    def _clear_history(self):
        self._history.clear()
        while self.history_layout.count():
            item = self.history_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._empty_hint = QLabel("스포이드로 색상을 추출하면 여기에 표시됩니다")
        self._empty_hint.setStyleSheet("color: #1e293b; font-size: 11px; background: transparent; border: none;")
        self.history_layout.addWidget(self._empty_hint)

    # ══════════════════════════════════════════════
    # Toggle button styling (shared by harmony & palette tabs)
    # ══════════════════════════════════════════════
    @staticmethod
    def _style_toggle_btns(btns_dict, active_mode):
        for m, b in btns_dict.items():
            if m == active_mode:
                b.setStyleSheet("QPushButton{background:#6c5ce7;color:#fff;font-size:10px;font-weight:bold;border:none;border-radius:6px;padding:0 10px;}")
            else:
                b.setStyleSheet("QPushButton{background:#1e293b;color:#64748b;font-size:10px;border:1px solid #334155;border-radius:6px;padding:0 10px;}QPushButton:hover{background:#334155;color:#94a3b8;}")

    # ── Color Harmony ──
    def _set_harmony(self, mode):
        self._harmony_mode = mode
        for m, b in self._harmony_btns.items():
            b.setChecked(m == mode)
        self._style_toggle_btns(self._harmony_btns, mode)
        self._refresh_harmony()

    def _compute_harmony(self, mode):
        c = self._picked_color
        h = c.hslHueF(); s = c.hslSaturationF(); l = c.lightnessF()
        if h < 0: h = 0.0
        offsets = {
            'complementary': [0, 0.5],
            'analogous': [-1/12, 0, 1/12],
            'triadic': [0, 1/3, 2/3],
            'split': [0, 5/12, 7/12],
            'tetradic': [0, 1/4, 1/2, 3/4],
        }
        colors = []
        for off in offsets.get(mode, [0]):
            nh = (h + off) % 1.0
            colors.append(QColor.fromHslF(max(0, min(1, nh)), max(0, min(1, s)), max(0, min(1, l))))
        return colors

    def _refresh_harmony(self):
        while self._harmony_flow.count():
            item = self._harmony_flow.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        for c in self._compute_harmony(self._harmony_mode):
            w = QWidget(); w.setStyleSheet("background:transparent;border:none;")
            vl = QVBoxLayout(w); vl.setContentsMargins(0,0,0,0); vl.setSpacing(2)
            vl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            btn = QPushButton(); btn.setFixedSize(48, 48)
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            hx = c.name().upper(); btn.setToolTip(hx)
            btn.setStyleSheet(f"QPushButton{{background:{c.name()};border:2px solid #1e293b;border-radius:10px;}}QPushButton:hover{{border-color:#a78bfa;}}")
            btn.clicked.connect(lambda _, col=c: self._update_display(col))
            vl.addWidget(btn)
            lbl = QLabel(hx); lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("color:#64748b;font-size:8px;font-family:Consolas;background:transparent;border:none;")
            vl.addWidget(lbl)
            self._harmony_flow.addWidget(w)
        self._harmony_flow.addStretch()

    # ── Gradient ──
    def _refresh_gradient(self):
        c1, c2 = self._picked_color.name(), self._grad_color2.name()
        self._gradient_bar.setStyleSheet(f"QFrame{{border-radius:10px;background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 {c1},stop:1 {c2});}}")
        css = f"background: linear-gradient(90deg, {c1} 0%, {c2} 100%);"
        if hasattr(self, '_grad_css'):
            self._grad_css.setText(css)
        if hasattr(self, '_grad_end_swatch'):
            self._grad_end_swatch.setStyleSheet(f"QPushButton{{background:{c2};border:2px solid #334155;border-radius:6px;}}QPushButton:hover{{border-color:#a78bfa;}}")
            self._grad_hex_input.setText(c2.upper())

    def _pick_grad_end(self):
        from PyQt6.QtWidgets import QColorDialog
        c = QColorDialog.getColor(self._grad_color2, self, "End Color")
        if c.isValid():
            self._grad_color2 = c
            self._refresh_gradient()

    def _apply_grad_hex(self):
        t = self._grad_hex_input.text().strip()
        if not t.startswith('#'): t = '#' + t
        c = QColor(t)
        if c.isValid():
            self._grad_color2 = c
            self._refresh_gradient()

    # ── Preset Palettes ──
    def _set_palette(self, mode):
        self._palette_mode = mode
        for m, b in self._palette_btns.items():
            b.setChecked(m == mode)
        self._style_toggle_btns(self._palette_btns, mode)
        self._refresh_palette()

    def _refresh_palette(self):
        old = self._palette_container.layout()
        if old:
            while old.count():
                item = old.takeAt(0)
                if item.widget(): item.widget().deleteLater()
            QWidget().setLayout(old)
        grid = QGridLayout()
        grid.setContentsMargins(0, 4, 0, 4); grid.setSpacing(6)
        colors = self._PALETTE_DATA.get(self._palette_mode, [])
        cols = 10
        for i, (hex_c, name) in enumerate(colors):
            r, co = divmod(i, cols)
            btn = QPushButton(); btn.setFixedSize(34, 34)
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn.setToolTip(f"{name}\n{hex_c}")
            btn.setStyleSheet(f"QPushButton{{background:{hex_c};border:2px solid #0f172a;border-radius:8px;}}QPushButton:hover{{border-color:#a78bfa;}}")
            btn.clicked.connect(lambda _, c=hex_c: self._update_display(QColor(c)))
            grid.addWidget(btn, r, co)
        self._palette_container.setLayout(grid)

    # ── Image Palette ──
    def _select_image_for_palette(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select Image", "", "Images (*.png *.jpg *.jpeg *.bmp *.webp)")
        if path:
            self._extract_palette(path)

    def _extract_palette(self, path):
        from PyQt6.QtGui import QImage
        from collections import Counter
        img = QImage(path)
        if img.isNull(): return
        scaled = img.scaled(80, 80, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        counts = Counter()
        for y in range(scaled.height()):
            for x in range(scaled.width()):
                c = QColor(scaled.pixel(x, y))
                counts[((c.red()//24)*24, (c.green()//24)*24, (c.blue()//24)*24)] += 1
        top = []
        for (r, g, b), _ in counts.most_common(60):
            if len(top) >= 8: break
            if r + g + b < 30 or r + g + b > 720: continue
            if any(abs(r-tr)+abs(g-tg)+abs(b-tb) < 60 for tr, tg, tb in top): continue
            top.append((r, g, b))
        if not top:
            top = [(r, g, b) for (r, g, b), _ in counts.most_common(8)]
        while self._img_palette_flow.count():
            item = self._img_palette_flow.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        self._img_palette_container.setVisible(True)
        for r, g, b in top:
            hx = f"#{r:02x}{g:02x}{b:02x}"
            w = QWidget(); w.setStyleSheet("background:transparent;border:none;")
            vl = QVBoxLayout(w); vl.setContentsMargins(0,0,0,0); vl.setSpacing(2)
            btn = QPushButton(); btn.setFixedSize(48, 48)
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn.setToolTip(hx.upper())
            btn.setStyleSheet(f"QPushButton{{background:{hx};border:2px solid #1e293b;border-radius:10px;}}QPushButton:hover{{border-color:#a78bfa;}}")
            btn.clicked.connect(lambda _, c=hx: self._update_display(QColor(c)))
            vl.addWidget(btn)
            lbl = QLabel(hx.upper()); lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("color:#64748b;font-size:8px;font-family:Consolas;background:transparent;border:none;")
            vl.addWidget(lbl)
            self._img_palette_flow.addWidget(w)
        self._img_palette_flow.addStretch()

    def _start_pick(self):
        if self._is_picking:
            return
        self._is_picking = True
        self.window().hide()
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(400, self._capture_screen)

    def _capture_screen(self):
        from PyQt6.QtGui import QGuiApplication
        screen = QGuiApplication.primaryScreen()
        if not screen:
            self._is_picking = False
            self.window().show()
            return

        # 물리 해상도 스크린샷 (픽셀 데이터 읽기 전용 — 배경에 그리지 않음)
        screenshot = screen.grabWindow(0)
        dpr = screen.devicePixelRatio()

        self._overlay = _ScreenOverlay(screenshot, dpr, self._on_color_picked)
        self._overlay.showFullScreen()

    def _on_color_picked(self, color: QColor):
        self._is_picking = False
        self.window().show()
        self.window().activateWindow()
        if color and color.isValid():
            self._update_display(color)


class _ScreenOverlay(QWidget):
    """투명 오버레이 스포이드 — 실제 화면이 그대로 보이고 커서+돋보기만 표시"""

    def __init__(self, screenshot, dpr, callback, parent=None):
        super().__init__(parent)
        self._img = screenshot.toImage()   # 물리 해상도 이미지 (픽셀 데이터 전용)
        self._dpr = dpr
        self.callback = callback
        self._called_back = False
        self._mouse_pos = None
        # 돋보기 확대 프리셋 (n x n 그리드, 픽셀 크기) — 휠로 전환
        self._zoom_presets = [
            (7, 22),    # x22 매우 확대
            (9, 17),    # x17
            (11, 14),   # x14 기본
            (15, 10),   # x10
            (21, 7),    # x7 넓은 시야
        ]
        self._zoom_idx = 2      # 기본: 11x14
        self._zoom_n = self._zoom_presets[self._zoom_idx][0]
        self._zoom_px = self._zoom_presets[self._zoom_idx][1]
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        # ★ 핵심: 투명 배경 — 스크린샷을 그리지 않으므로 실제 화면이 보임
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setCursor(QCursor(Qt.CursorShape.BlankCursor))
        self.setMouseTracking(True)

    # ── 논리좌표 → 물리좌표 변환하여 스크린샷에서 색상 읽기 ──
    def _pixel_at(self, logical_x, logical_y):
        px = int(logical_x * self._dpr)
        py = int(logical_y * self._dpr)
        if 0 <= px < self._img.width() and 0 <= py < self._img.height():
            return QColor(self._img.pixel(px, py))
        return QColor(0, 0, 0)

    # ── 물리 좌표에서 직접 색상 읽기 (돋보기용) ──
    def _phys_pixel(self, phys_x, phys_y):
        if 0 <= phys_x < self._img.width() and 0 <= phys_y < self._img.height():
            return QColor(self._img.pixel(phys_x, phys_y))
        return QColor(15, 23, 42)

    def paintEvent(self, event):
        from PyQt6.QtGui import QPainter, QPen, QBrush, QFont
        p = QPainter(self)

        # ★ 배경: alpha=1 (거의 완전 투명, 마우스 이벤트 캡처용)
        #   실제 화면이 그대로 보임 — 스크린샷을 배경에 그리지 않음
        p.fillRect(self.rect(), QColor(0, 0, 0, 1))

        if not self._mouse_pos:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            bw, bh = 460, 60
            bx, by = (self.width() - bw) // 2, (self.height() - bh) // 2
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 180))
            p.drawRoundedRect(bx, by, bw, bh, 14, 14)
            p.setPen(QColor(255, 255, 255, 230))
            p.setFont(QFont("Segoe UI", 15, QFont.Weight.Bold))
            p.drawText(bx, by, bw, bh, Qt.AlignmentFlag.AlignCenter,
                       "Click to pick  |  ESC cancel")
            p.end()
            return

        mx, my = self._mouse_pos.x(), self._mouse_pos.y()
        cur = self._pixel_at(mx, my)
        hex_t = cur.name().upper()

        # ── 커서: 큰 색상 원 + 십자선 ──
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        R = 28
        # 그림자
        p.setPen(QPen(QColor(0, 0, 0, 160), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(mx - R - 2, my - R - 2, R * 2 + 4, R * 2 + 4)
        # 메인 원 (현재 색상으로 채움)
        p.setPen(QPen(QColor(255, 255, 255), 3))
        p.setBrush(QBrush(cur))
        p.drawEllipse(mx - R, my - R, R * 2, R * 2)
        # 십자선
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        cl = 10
        for c, w in [(QColor(0, 0, 0, 220), 3), (QColor(255, 255, 255), 1)]:
            p.setPen(QPen(c, w))
            p.drawLine(mx - cl, my, mx + cl, my)
            p.drawLine(mx, my - cl, mx, my + cl)

        # 가이드 점선
        p.setPen(QPen(QColor(255, 255, 255, 50), 1, Qt.PenStyle.DashLine))
        p.drawLine(mx, 0, mx, my - R - 3)
        p.drawLine(mx, my + R + 3, mx, self.height())
        p.drawLine(0, my, mx - R - 3, my)
        p.drawLine(mx + R + 3, my, self.width(), my)

        # ── 돋보기 (물리 픽셀 기준 확대) ──
        n = self._zoom_n
        half = n // 2
        ps = self._zoom_px
        mw = n * ps
        gap = R + 16
        tw, th = mw + 8, mw + 50
        lx, ly = mx + gap, my - th // 2
        if lx + tw > self.width() - 10:
            lx = mx - gap - tw
        ly = max(10, min(ly, self.height() - 10 - th))

        # 돋보기 배경 (반투명 패널)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 140))
        p.drawRoundedRect(lx + 3, ly + 3, tw, th, 12, 12)
        p.setBrush(QColor(15, 23, 42, 240))
        p.setPen(QPen(QColor(71, 85, 105), 2))
        p.drawRoundedRect(lx, ly, tw, th, 12, 12)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        # 확대 픽셀 (물리 좌표 기준 — 1:1 정확한 픽셀)
        phys_cx = int(mx * self._dpr)
        phys_cy = int(my * self._dpr)
        ox, oy = lx + 4, ly + 4
        for dy in range(n):
            for dx in range(n):
                c = self._phys_pixel(phys_cx - half + dx, phys_cy - half + dy)
                p.fillRect(ox + dx * ps, oy + dy * ps, ps, ps, c)

        # 그리드
        p.setPen(QPen(QColor(255, 255, 255, 18), 1))
        for i in range(n + 1):
            p.drawLine(ox + i * ps, oy, ox + i * ps, oy + n * ps)
            p.drawLine(ox, oy + i * ps, ox + n * ps, oy + i * ps)

        # 중앙 픽셀 강조 (이게 지금 찍을 색상)
        ccx, ccy = ox + half * ps, oy + half * ps
        p.setPen(QPen(QColor(0, 0, 0, 220), 3))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(ccx - 1, ccy - 1, ps + 2, ps + 2)
        p.setPen(QPen(QColor(255, 255, 255), 1))
        p.drawRect(ccx, ccy, ps, ps)

        # 하단 색상 정보
        bar_y = oy + n * ps + 6
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(71, 85, 105), 1))
        p.setBrush(QBrush(cur))
        p.drawRoundedRect(ox + 2, bar_y + 3, 32, 32, 6, 6)
        p.setPen(QColor(248, 250, 252))
        p.setFont(QFont("Consolas", 13, QFont.Weight.Bold))
        p.drawText(ox + 42, bar_y + 20, hex_t)
        p.setPen(QColor(100, 116, 139))
        p.setFont(QFont("Consolas", 9))
        p.drawText(ox + 42, bar_y + 34,
                   f"rgb({cur.red()}, {cur.green()}, {cur.blue()})")
        p.end()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta > 0 and self._zoom_idx > 0:
            self._zoom_idx -= 1
        elif delta < 0 and self._zoom_idx < len(self._zoom_presets) - 1:
            self._zoom_idx += 1
        self._zoom_n, self._zoom_px = self._zoom_presets[self._zoom_idx]
        self.update()

    def mouseMoveEvent(self, event):
        self._mouse_pos = event.pos()
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._mouse_pos:
            color = self._pixel_at(self._mouse_pos.x(), self._mouse_pos.y())
            self._called_back = True
            self.close()
            self.callback(color)
            return
        if event.button() == Qt.MouseButton.RightButton:
            self._called_back = True
            self.close()
            self.callback(QColor())

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._called_back = True
            self.close()
            self.callback(QColor())

    def closeEvent(self, event):
        if not self._called_back:
            self.callback(QColor())
        super().closeEvent(event)


# ═══════════════════════════════════════════════════════════════════
#  배경 제거 (Background Remover) – rembg + 수동 지우개
# ═══════════════════════════════════════════════════════════════════

class _BgRemoveWorker(QThread):
    """rembg CLI를 서브프로세스로 실행 (onnxruntime access violation 방지)"""
    finished = pyqtSignal(QImage)   # 결과 투명 이미지
    error = pyqtSignal(str)
    status = pyqtSignal(str)

    def __init__(self, image_path: str, parent=None):
        super().__init__(parent)
        self._path = image_path

    def run(self):
        try:
            import tempfile as _tf

            # 출력 임시 파일
            out_fd, out_path = _tf.mkstemp(suffix=".png")
            os.close(out_fd)

            self.status.emit("배경 제거 중...")

            # PyQt6 + onnxruntime DLL 충돌 방지: 별도 프로세스에서 rembg 실행
            helper = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_rembg_helper.py")
            result = subprocess.run(
                [sys.executable, helper, self._path, out_path],
                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=300,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
            )

            if result.returncode != 0:
                err_msg = result.stderr.strip() or f"rembg 실패 (code {result.returncode})"
                try:
                    os.remove(out_path)
                except:
                    pass
                self.error.emit(err_msg)
                return

            # 결과 PNG → QImage
            qimg = QImage(out_path)
            try:
                os.remove(out_path)
            except:
                pass

            if qimg.isNull():
                self.error.emit("결과 이미지를 읽을 수 없습니다")
                return

            self.finished.emit(qimg)
        except subprocess.TimeoutExpired:
            self.error.emit("배경 제거 시간 초과 (5분)")
        except Exception as e:
            self.error.emit(str(e))


class _InpaintWorker(QThread):
    """OpenCV 인페인팅을 서브프로세스로 실행"""
    finished = pyqtSignal(QImage)
    error = pyqtSignal(str)

    def __init__(self, image_path: str, mask_path: str, parent=None):
        super().__init__(parent)
        self._img_path = image_path
        self._mask_path = mask_path

    def run(self):
        try:
            import tempfile as _tf
            out_fd, out_path = _tf.mkstemp(suffix=".png")
            os.close(out_fd)

            helper = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_inpaint_helper.py")
            result = subprocess.run(
                [sys.executable, helper, self._img_path, self._mask_path, out_path],
                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
            )

            if result.returncode != 0:
                err_msg = result.stderr.strip() or f"인페인팅 실패 (code {result.returncode})"
                try: os.remove(out_path)
                except: pass
                self.error.emit(err_msg)
                return

            qimg = QImage(out_path)
            try: os.remove(out_path)
            except: pass

            if qimg.isNull():
                self.error.emit("결과 이미지를 읽을 수 없습니다")
                return

            self.finished.emit(qimg)
        except subprocess.TimeoutExpired:
            self.error.emit("인페인팅 시간 초과 (2분)")
        except Exception as e:
            self.error.emit(str(e))


class _BgCanvas(QWidget):
    """투명 배경 시각화 캔버스 – 체커보드 + 이미지 + 지우개/복원/인페인트 도구"""

    TOOL_NONE = 0
    TOOL_ERASER = 1
    TOOL_RESTORE = 2
    TOOL_INPAINT = 3

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(300, 300)
        self.setMouseTracking(True)

        self._image: QImage | None = None       # RGBA 이미지 (편집 중)
        self._original: QImage | None = None    # 원본 이미지 (복원용)
        self._scale = 1.0
        self._offset_x = 0.0
        self._offset_y = 0.0

        # 도구
        self._tool = self.TOOL_NONE
        self._brush_size = 20
        self._is_painting = False
        self._last_pt: QPoint | None = None

        # 인페인트 마스크 (흰=칠한영역, 검=안칠한영역)
        self._mask: QImage | None = None

        # Undo / Redo
        self._undo_stack: list[QImage] = []
        self._redo_stack: list[QImage] = []
        self._max_undo = 20

    # ── 좌표 변환 ──
    def _widget_to_image(self, wx: float, wy: float):
        """위젯 좌표 → 이미지 픽셀 좌표"""
        if not self._image:
            return -1, -1
        ix = (wx - self._offset_x) / self._scale
        iy = (wy - self._offset_y) / self._scale
        return int(ix), int(iy)

    def _fit_image(self):
        """이미지를 캔버스에 맞게 중앙 배치"""
        if not self._image:
            return
        w, h = self.width(), self.height()
        iw, ih = self._image.width(), self._image.height()
        if iw == 0 or ih == 0:
            return
        scale = min(w / iw, h / ih) * 0.9
        self._scale = scale
        self._offset_x = (w - iw * scale) / 2
        self._offset_y = (h - ih * scale) / 2

    def set_image(self, qimg: QImage, keep_undo: bool = False):
        if keep_undo and self._image:
            self.push_undo()
        else:
            self._undo_stack.clear()
        self._redo_stack.clear()
        self._image = qimg.convertToFormat(QImage.Format.Format_ARGB32)
        self._mask = None
        self._fit_image()
        self.update()

    def set_original(self, qimg: QImage):
        """복원 브러시용 원본 저장"""
        self._original = qimg.convertToFormat(QImage.Format.Format_ARGB32)

    def get_image(self) -> QImage | None:
        return self._image

    def set_tool(self, tool: int):
        self._tool = tool
        self.setCursor(Qt.CursorShape.CrossCursor if tool != self.TOOL_NONE else Qt.CursorShape.ArrowCursor)
        # 인페인트 모드 진입 시 빈 마스크 생성
        if tool == self.TOOL_INPAINT and self._image and self._mask is None:
            self._mask = QImage(self._image.size(), QImage.Format.Format_ARGB32)
            self._mask.fill(QColor(0, 0, 0, 0))
        self.update()

    def clear_mask(self):
        """인페인트 마스크 초기화"""
        if self._image:
            self._mask = QImage(self._image.size(), QImage.Format.Format_ARGB32)
            self._mask.fill(QColor(0, 0, 0, 0))
            self.update()

    def get_mask(self) -> QImage | None:
        return self._mask

    def has_mask_content(self) -> bool:
        """마스크에 칠한 영역이 있는지"""
        if not self._mask:
            return False
        # 빠른 체크: 알파값이 0이 아닌 픽셀이 하나라도 있으면
        for y in range(0, self._mask.height(), 10):
            for x in range(0, self._mask.width(), 10):
                if self._mask.pixelColor(x, y).alpha() > 0:
                    return True
        return False

    def set_brush_size(self, size: int):
        self._brush_size = size
        self.update()

    def push_undo(self):
        if self._image:
            if len(self._undo_stack) >= self._max_undo:
                self._undo_stack.pop(0)
            self._undo_stack.append(self._image.copy())
            self._redo_stack.clear()

    def undo(self) -> bool:
        if self._undo_stack and self._image:
            self._redo_stack.append(self._image.copy())
            self._image = self._undo_stack.pop()
            self.update()
            return True
        return False

    def redo(self) -> bool:
        if self._redo_stack and self._image:
            self._undo_stack.append(self._image.copy())
            self._image = self._redo_stack.pop()
            self.update()
            return True
        return False

    def can_undo(self) -> bool:
        return len(self._undo_stack) > 0

    def can_redo(self) -> bool:
        return len(self._redo_stack) > 0

    # ── 브러시 그리기 ──
    def _paint_at(self, ix: int, iy: int):
        if not self._image:
            return
        r = self._brush_size / 2 / self._scale
        if self._tool == self.TOOL_ERASER:
            painter = QPainter(self._image)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(0, 0, 0, 0)))
            painter.drawEllipse(QPoint(ix, iy), int(r), int(r))
            painter.end()
        elif self._tool == self.TOOL_RESTORE and self._original:
            self._restore_circle(ix, iy, int(r))
        elif self._tool == self.TOOL_INPAINT and self._mask:
            painter = QPainter(self._mask)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(255, 60, 60, 160)))
            painter.drawEllipse(QPoint(ix, iy), int(r), int(r))
            painter.end()
        self.update()

    def _paint_line(self, x0, y0, x1, y1):
        if not self._image:
            return
        r = self._brush_size / 2 / self._scale
        if self._tool == self.TOOL_ERASER:
            painter = QPainter(self._image)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            pen = QPen(QColor(0, 0, 0, 0), r * 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.drawLine(QPoint(x0, y0), QPoint(x1, y1))
            painter.end()
        elif self._tool == self.TOOL_RESTORE and self._original:
            # 두 점 사이 보간
            import math
            dx, dy = x1 - x0, y1 - y0
            dist = max(1, int(math.hypot(dx, dy)))
            ri = int(r)
            for i in range(dist + 1):
                t = i / dist
                cx = int(x0 + dx * t)
                cy = int(y0 + dy * t)
                self._restore_circle(cx, cy, ri)
        elif self._tool == self.TOOL_INPAINT and self._mask:
            painter = QPainter(self._mask)
            pen = QPen(QColor(255, 60, 60, 160), r * 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.drawLine(QPoint(x0, y0), QPoint(x1, y1))
            painter.end()
        self.update()

    def _restore_circle(self, cx: int, cy: int, r: int):
        """원본 이미지에서 원형 영역 복원"""
        if not self._image or not self._original:
            return
        iw, ih = self._image.width(), self._image.height()
        ow, oh = self._original.width(), self._original.height()
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                if dx * dx + dy * dy > r * r:
                    continue
                px, py = cx + dx, cy + dy
                if 0 <= px < iw and 0 <= py < ih and px < ow and py < oh:
                    self._image.setPixelColor(px, py, self._original.pixelColor(px, py))

    # ── 마우스 이벤트 ──
    def mousePressEvent(self, event):
        if self._tool != self.TOOL_NONE and event.button() == Qt.MouseButton.LeftButton and self._image:
            self.push_undo()
            ix, iy = self._widget_to_image(event.position().x(), event.position().y())
            self._paint_at(ix, iy)
            self._is_painting = True
            self._last_pt = QPoint(ix, iy)

    def mouseMoveEvent(self, event):
        if self._is_painting and self._tool != self.TOOL_NONE and self._image:
            ix, iy = self._widget_to_image(event.position().x(), event.position().y())
            if self._last_pt:
                self._paint_line(self._last_pt.x(), self._last_pt.y(), ix, iy)
            self._last_pt = QPoint(ix, iy)
        self.update()

    def mouseReleaseEvent(self, event):
        self._is_painting = False
        self._last_pt = None

    def wheelEvent(self, event):
        """마우스 휠로 확대/축소"""
        if not self._image:
            return
        delta = event.angleDelta().y()
        factor = 1.1 if delta > 0 else 0.9
        mx, my = event.position().x(), event.position().y()
        # 마우스 포인트 중심 줌
        self._offset_x = mx - (mx - self._offset_x) * factor
        self._offset_y = my - (my - self._offset_y) * factor
        self._scale *= factor
        self._scale = max(0.1, min(self._scale, 10.0))
        self.update()

    # ── 체커보드 + 이미지 그리기 ──
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor("#0f172a"))

        if not self._image:
            p.setPen(QColor("#475569"))
            p.setFont(QFont("Segoe UI", 14))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "이미지를 드래그하여 놓으세요")
            p.end()
            return

        # 체커보드 (투명 영역 표시)
        iw = int(self._image.width() * self._scale)
        ih = int(self._image.height() * self._scale)
        ox, oy = int(self._offset_x), int(self._offset_y)
        checker = 12
        c1, c2 = QColor("#1e293b"), QColor("#334155")
        for row in range(0, ih, checker):
            for col in range(0, iw, checker):
                color = c1 if (row // checker + col // checker) % 2 == 0 else c2
                rx = ox + col
                ry = oy + row
                rw = min(checker, iw - col)
                rh = min(checker, ih - row)
                p.fillRect(rx, ry, rw, rh, color)

        # 이미지 그리기
        p.drawImage(ox, oy, self._image.scaled(
            iw, ih, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation
        ))

        # 인페인트 마스크 오버레이 (빨간 반투명)
        if self._mask and self._tool == self.TOOL_INPAINT:
            p.drawImage(ox, oy, self._mask.scaled(
                iw, ih, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation
            ))

        # 브러시 커서 미리보기
        if self._tool != self.TOOL_NONE and self.underMouse():
            cursor = self.mapFromGlobal(QCursor.pos())
            r = int(self._brush_size / 2)
            _cursor_colors = {
                self.TOOL_ERASER: QColor("#ef4444"),
                self.TOOL_RESTORE: QColor("#22c55e"),
                self.TOOL_INPAINT: QColor("#f59e0b"),
            }
            color = _cursor_colors.get(self._tool, QColor("#ef4444"))
            p.setPen(QPen(color, 1.5, Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(cursor, r, r)

        p.end()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._image:
            self._fit_image()


class BgRemovePage(QWidget):
    """배경 제거 페이지 – 드래그앤드롭 → 자동 제거 → 지우개 후처리 → PNG 다운로드"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._worker: _BgRemoveWorker | None = None
        self._inpaint_worker: _InpaintWorker | None = None
        self._original_path: str = ""
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ═══ 전체 배경 ═══
        self.setStyleSheet("background: #0c1322;")

        # ═══ 상단 바: 미니멀 헤더 ═══
        header_bar = QFrame()
        header_bar.setFixedHeight(48)
        header_bar.setStyleSheet("""
            QFrame { background: #111827; border: none; border-bottom: 1px solid #1f2937; }
        """)
        hbar = QHBoxLayout(header_bar)
        hbar.setContentsMargins(20, 0, 20, 0)
        hbar.setSpacing(12)

        title = QLabel("배경 제거")
        title.setFont(QFont("Segoe UI Semibold", 13))
        title.setStyleSheet("color: #f1f5f9; background: transparent; border: none;")
        hbar.addWidget(title)

        subtitle = QLabel("AI 자동 제거 + 수동 지우개")
        subtitle.setStyleSheet("color: #4b5563; font-size: 11px; background: transparent; border: none;")
        hbar.addWidget(subtitle)
        hbar.addStretch()

        # 상태 텍스트 (헤더 우측)
        self._status = QLabel("")
        self._status.setStyleSheet("color: #6b7280; font-size: 11px; background: transparent; border: none;")
        hbar.addWidget(self._status)

        # 프로그레스 (헤더 아래에 오버레이)
        self._progress = QProgressBar()
        self._progress.setFixedHeight(2)
        self._progress.setRange(0, 0)
        self._progress.setStyleSheet("""
            QProgressBar { background: transparent; border: none; }
            QProgressBar::chunk { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #6366f1, stop:1 #a78bfa); }
        """)
        self._progress.hide()

        layout.addWidget(header_bar)
        layout.addWidget(self._progress)

        # ═══ 메인 캔버스 영역 (전체 채움) ═══
        canvas_container = QWidget()
        canvas_container.setStyleSheet("background: #0c1322; border: none;")
        canvas_main = QVBoxLayout(canvas_container)
        canvas_main.setContentsMargins(0, 0, 0, 0)
        canvas_main.setSpacing(0)

        self._stack = QStackedWidget()
        self._stack.setStyleSheet("background: transparent;")

        # ── 드롭존 (세련된 디자인) ──
        self._dropzone = QWidget()
        drop_layout = QVBoxLayout(self._dropzone)
        drop_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drop_layout.setSpacing(0)
        self._dropzone.setStyleSheet("background: #0c1322;")

        # 드롭 카드 (중앙 고정 크기)
        drop_card = QFrame()
        drop_card.setObjectName("dropCard")
        drop_card.setFixedSize(360, 280)
        drop_card.setStyleSheet("""
            QFrame#dropCard {
                background: #111827;
                border: 2px dashed #1f2937;
                border-radius: 20px;
            }
            QFrame#dropCard:hover {
                border-color: #6366f1;
                background: #0f1729;
            }
        """)
        dc_layout = QVBoxLayout(drop_card)
        dc_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dc_layout.setSpacing(16)

        # 아이콘 원형 배경
        icon_bg = QLabel("🖼️")
        icon_bg.setFixedSize(72, 72)
        icon_bg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_bg.setFont(QFont("Segoe UI", 30))
        icon_bg.setStyleSheet("""
            background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #1e1b4b, stop:1 #172554);
            border: none; border-radius: 36px;
        """)
        dc_layout.addWidget(icon_bg, 0, Qt.AlignmentFlag.AlignCenter)

        drop_title = QLabel("이미지를 드래그하세요")
        drop_title.setFont(QFont("Segoe UI Semibold", 14))
        drop_title.setStyleSheet("color: #e5e7eb; background: transparent; border: none;")
        drop_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dc_layout.addWidget(drop_title)

        drop_sub = QLabel("또는 아래 버튼으로 파일을 선택하세요")
        drop_sub.setStyleSheet("color: #4b5563; font-size: 11px; background: transparent; border: none;")
        drop_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dc_layout.addWidget(drop_sub)

        # 파일 선택 버튼 (드롭존 안에)
        self._btn_new = QPushButton("파일 선택")
        self._btn_new.setFixedSize(140, 36)
        self._btn_new.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_new.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #4f46e5, stop:1 #7c3aed);
                color: white; border: none; border-radius: 18px;
                font-size: 12px; font-weight: 600;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #6366f1, stop:1 #8b5cf6);
            }
        """)
        self._btn_new.clicked.connect(self._open_image)
        dc_layout.addWidget(self._btn_new, 0, Qt.AlignmentFlag.AlignCenter)

        drop_fmt = QLabel("PNG · JPG · WEBP · BMP")
        drop_fmt.setStyleSheet("color: #374151; font-size: 10px; background: transparent; border: none;")
        drop_fmt.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dc_layout.addWidget(drop_fmt)

        drop_layout.addWidget(drop_card, 0, Qt.AlignmentFlag.AlignCenter)
        self._stack.addWidget(self._dropzone)

        # ── 캔버스 + 플로팅 툴바 ──
        canvas_page = QWidget()
        canvas_page.setStyleSheet("background: #0c1322;")
        cp_layout = QVBoxLayout(canvas_page)
        cp_layout.setContentsMargins(0, 0, 0, 0)
        cp_layout.setSpacing(0)

        # 플로팅 툴바
        toolbar_wrap = QWidget()
        toolbar_wrap.setFixedHeight(52)
        toolbar_wrap.setStyleSheet("background: #111827; border: none; border-bottom: 1px solid #1f2937;")
        toolbar = QHBoxLayout(toolbar_wrap)
        toolbar.setContentsMargins(12, 0, 12, 0)
        toolbar.setSpacing(4)

        # 공통 버튼 스타일
        _tbtn = """
            QPushButton {{
                background: {bg}; color: {fg}; border: {bd};
                border-radius: 8px; padding: 4px 12px; font-size: 11px; font-weight: 600;
                min-height: 30px;
            }}
            QPushButton:hover {{ background: {hover}; color: #f1f5f9; }}
            {extra}
        """

        self._btn_remove = QPushButton("✨ BG제거")
        self._btn_remove.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_remove.setStyleSheet(_tbtn.format(
            bg="qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #4f46e5, stop:1 #7c3aed)",
            fg="white", bd="none",
            hover="qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #6366f1, stop:1 #8b5cf6)",
            extra="QPushButton:disabled { background: #1f2937; color: #374151; }"
        ))
        self._btn_remove.clicked.connect(self._do_remove_bg)
        self._btn_remove.setEnabled(False)
        toolbar.addWidget(self._btn_remove)

        # 세로 구분선
        def _vsep():
            s = QFrame()
            s.setFixedSize(1, 24)
            s.setStyleSheet("background: #1f2937; border: none;")
            return s

        toolbar.addWidget(_vsep())

        self._btn_eraser = QPushButton("⊘ 지우개")
        self._btn_eraser.setCheckable(True)
        self._btn_eraser.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_eraser.setStyleSheet(_tbtn.format(
            bg="transparent", fg="#9ca3af", bd="1px solid #1f2937",
            hover="#1f2937",
            extra="QPushButton:checked { background: #dc2626; color: white; border-color: #dc2626; }"
        ))
        self._btn_eraser.toggled.connect(self._toggle_eraser)
        self._btn_eraser.setEnabled(False)
        toolbar.addWidget(self._btn_eraser)

        # 복원 브러시
        self._btn_restore = QPushButton("🖌 복원")
        self._btn_restore.setCheckable(True)
        self._btn_restore.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_restore.setStyleSheet(_tbtn.format(
            bg="transparent", fg="#9ca3af", bd="1px solid #1f2937",
            hover="#1f2937",
            extra="QPushButton:checked { background: #059669; color: white; border-color: #059669; }"
        ))
        self._btn_restore.toggled.connect(self._toggle_restore)
        self._btn_restore.setEnabled(False)
        toolbar.addWidget(self._btn_restore)

        toolbar.addWidget(_vsep())

        # 자막지우개 (인페인트)
        self._btn_inpaint = QPushButton("Aa 자막제거")
        self._btn_inpaint.setCheckable(True)
        self._btn_inpaint.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_inpaint.setStyleSheet(_tbtn.format(
            bg="transparent", fg="#9ca3af", bd="1px solid #1f2937",
            hover="#1f2937",
            extra="QPushButton:checked { background: #d97706; color: white; border-color: #d97706; }"
        ))
        self._btn_inpaint.toggled.connect(self._toggle_inpaint)
        self._btn_inpaint.setEnabled(False)
        toolbar.addWidget(self._btn_inpaint)

        # 인페인트 적용 버튼
        self._btn_inpaint_apply = QPushButton("✨ 적용")
        self._btn_inpaint_apply.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_inpaint_apply.setStyleSheet(_tbtn.format(
            bg="qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #d97706, stop:1 #f59e0b)",
            fg="white", bd="none",
            hover="qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #f59e0b, stop:1 #fbbf24)",
            extra="QPushButton:disabled { background: #1f2937; color: #374151; }"
        ))
        self._btn_inpaint_apply.clicked.connect(self._do_inpaint)
        self._btn_inpaint_apply.setEnabled(False)
        self._btn_inpaint_apply.hide()
        toolbar.addWidget(self._btn_inpaint_apply)

        # 마스크 초기화 버튼
        self._btn_mask_clear = QPushButton("✕ 초기화")
        self._btn_mask_clear.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_mask_clear.setStyleSheet(_tbtn.format(
            bg="transparent", fg="#9ca3af", bd="1px solid #1f2937",
            hover="#1f2937", extra=""
        ))
        self._btn_mask_clear.clicked.connect(self._clear_mask)
        self._btn_mask_clear.hide()
        toolbar.addWidget(self._btn_mask_clear)

        # 브러시 크기
        size_label = QLabel("크기")
        size_label.setStyleSheet("color: #4b5563; font-size: 10px; background: transparent; border: none; margin-left: 4px;")
        toolbar.addWidget(size_label)

        self._slider_size = QSlider(Qt.Orientation.Horizontal)
        self._slider_size.setRange(4, 100)
        self._slider_size.setValue(20)
        self._slider_size.setFixedWidth(90)
        self._slider_size.setStyleSheet("""
            QSlider { background: transparent; border: none; }
            QSlider::groove:horizontal {
                height: 3px; background: #1f2937; border-radius: 1px;
            }
            QSlider::handle:horizontal {
                width: 12px; height: 12px; margin: -5px 0;
                background: #818cf8; border-radius: 6px;
            }
            QSlider::handle:horizontal:hover { background: #a78bfa; }
        """)
        self._slider_size.valueChanged.connect(self._on_size_changed)
        toolbar.addWidget(self._slider_size)

        self._size_val = QLabel("20")
        self._size_val.setFixedWidth(24)
        self._size_val.setStyleSheet("color: #6b7280; font-size: 10px; background: transparent; border: none;")
        toolbar.addWidget(self._size_val)

        toolbar.addWidget(_vsep())

        self._btn_undo = QPushButton("↩")
        self._btn_undo.setToolTip("되돌리기 (Undo)")
        self._btn_undo.setFixedWidth(40)
        self._btn_undo.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_undo.setStyleSheet(_tbtn.format(
            bg="transparent", fg="#9ca3af", bd="1px solid #1f2937",
            hover="#1f2937", extra=""
        ))
        self._btn_undo.clicked.connect(self._undo)
        self._btn_undo.setEnabled(False)
        toolbar.addWidget(self._btn_undo)

        self._btn_redo = QPushButton("↪")
        self._btn_redo.setToolTip("앞으로 (Redo)")
        self._btn_redo.setFixedWidth(40)
        self._btn_redo.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_redo.setStyleSheet(_tbtn.format(
            bg="transparent", fg="#9ca3af", bd="1px solid #1f2937",
            hover="#1f2937", extra=""
        ))
        self._btn_redo.clicked.connect(self._redo)
        self._btn_redo.setEnabled(False)
        toolbar.addWidget(self._btn_redo)

        # 새 이미지
        self._btn_open2 = QPushButton("📂 열기")
        self._btn_open2.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_open2.setStyleSheet(_tbtn.format(
            bg="transparent", fg="#9ca3af", bd="1px solid #1f2937",
            hover="#1f2937", extra=""
        ))
        self._btn_open2.clicked.connect(self._open_image)
        toolbar.addWidget(self._btn_open2)

        toolbar.addStretch()

        self._btn_download = QPushButton("PNG 저장")
        self._btn_download.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_download.setStyleSheet(_tbtn.format(
            bg="#059669", fg="white", bd="none",
            hover="#10b981",
            extra="QPushButton:disabled { background: #1f2937; color: #374151; }"
        ))
        self._btn_download.clicked.connect(self._save_png)
        self._btn_download.setEnabled(False)
        toolbar.addWidget(self._btn_download)

        cp_layout.addWidget(toolbar_wrap)

        # 캔버스
        self._canvas = _BgCanvas()
        cp_layout.addWidget(self._canvas, 1)

        self._stack.addWidget(canvas_page)

        canvas_main.addWidget(self._stack, 1)
        layout.addWidget(canvas_container, 1)

        # 초기 상태: 드롭존
        self._stack.setCurrentIndex(0)

    # ── 드래그앤드롭 ──
    _DROP_NORMAL = """
        QFrame#dropCard {
            background: #111827;
            border: 2px dashed #1f2937;
            border-radius: 20px;
        }
    """
    _DROP_ACTIVE = """
        QFrame#dropCard {
            background: #0f1729;
            border: 2px dashed #6366f1;
            border-radius: 20px;
        }
    """

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.toLocalFile().lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.bmp')):
                    event.acceptProposedAction()
                    card = self._dropzone.findChild(QFrame, "dropCard")
                    if card:
                        card.setStyleSheet(self._DROP_ACTIVE)
                    return

    def dragLeaveEvent(self, event):
        card = self._dropzone.findChild(QFrame, "dropCard")
        if card:
            card.setStyleSheet(self._DROP_NORMAL)

    def dropEvent(self, event):
        card = self._dropzone.findChild(QFrame, "dropCard")
        if card:
            card.setStyleSheet(self._DROP_NORMAL)
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.bmp')):
                self._load_image(path)
                return

    # ── 이미지 로드 ──
    def _open_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "이미지 열기", "",
            "이미지 파일 (*.png *.jpg *.jpeg *.webp *.bmp);;모든 파일 (*)"
        )
        if path:
            self._load_image(path)

    def _load_image(self, path: str):
        self._original_path = path
        qimg = QImage(path)
        if qimg.isNull():
            self._status.setText("❌ 이미지를 읽을 수 없습니다")
            return

        self._canvas.set_original(qimg)
        self._canvas.set_image(qimg)
        self._stack.setCurrentIndex(1)
        self._btn_remove.setEnabled(True)
        self._btn_eraser.setEnabled(False)
        self._btn_eraser.setChecked(False)
        self._btn_restore.setEnabled(False)
        self._btn_restore.setChecked(False)
        self._btn_inpaint.setEnabled(True)
        self._btn_inpaint.setChecked(False)
        self._btn_inpaint_apply.setEnabled(False)
        self._btn_inpaint_apply.hide()
        self._btn_mask_clear.hide()
        self._btn_undo.setEnabled(False)
        self._btn_redo.setEnabled(False)
        self._btn_download.setEnabled(True)
        fname = os.path.basename(path)
        self._status.setText(f"📷 {fname}  ({qimg.width()}×{qimg.height()})")

    # ── 배경 제거 실행 ──
    def _do_remove_bg(self):
        if not self._original_path:
            return
        self._btn_remove.setEnabled(False)
        self._progress.show()
        self._status.setText("⏳ 배경 제거 처리 중...")

        self._worker = _BgRemoveWorker(self._original_path)
        self._worker.status.connect(self._on_worker_status)
        self._worker.finished.connect(self._on_bg_removed)
        self._worker.error.connect(self._on_bg_error)
        self._worker.start()

    def _on_worker_status(self, msg: str):
        self._status.setText(f"⏳ {msg}")

    def _on_bg_removed(self, result_img: QImage):
        self._progress.hide()
        self._canvas.set_image(result_img, keep_undo=True)
        self._btn_remove.setEnabled(True)
        self._btn_eraser.setEnabled(True)
        self._btn_restore.setEnabled(True)
        self._btn_download.setEnabled(True)
        self._update_undo_redo_btns()
        w, h = result_img.width(), result_img.height()
        self._status.setText(f"✅ 배경 제거 완료  ({w}×{h}) — 🖌복원으로 잘린 부분 살리기 / ⊘지우개로 남은 배경 지우기")
        self._worker = None

    def _on_bg_error(self, err: str):
        self._progress.hide()
        self._btn_remove.setEnabled(True)
        self._status.setText(f"❌ 오류: {err}")
        self._worker = None

    # ── 도구 토글 (상호 배타적) ──
    def _uncheck_others(self, *buttons):
        for btn in buttons:
            btn.blockSignals(True)
            btn.setChecked(False)
            btn.blockSignals(False)

    def _toggle_eraser(self, on: bool):
        if on:
            self._uncheck_others(self._btn_restore, self._btn_inpaint)
            self._canvas.set_tool(_BgCanvas.TOOL_ERASER)
            self._btn_inpaint_apply.hide()
            self._btn_mask_clear.hide()
        elif not self._btn_restore.isChecked() and not self._btn_inpaint.isChecked():
            self._canvas.set_tool(_BgCanvas.TOOL_NONE)

    def _toggle_restore(self, on: bool):
        if on:
            self._uncheck_others(self._btn_eraser, self._btn_inpaint)
            self._canvas.set_tool(_BgCanvas.TOOL_RESTORE)
            self._btn_inpaint_apply.hide()
            self._btn_mask_clear.hide()
        elif not self._btn_eraser.isChecked() and not self._btn_inpaint.isChecked():
            self._canvas.set_tool(_BgCanvas.TOOL_NONE)

    def _toggle_inpaint(self, on: bool):
        if on:
            self._uncheck_others(self._btn_eraser, self._btn_restore)
            self._canvas.set_tool(_BgCanvas.TOOL_INPAINT)
            self._btn_inpaint_apply.show()
            self._btn_inpaint_apply.setEnabled(True)
            self._btn_mask_clear.show()
            self._status.setText("🔤 자막/텍스트 영역을 브러시로 칠한 후 [✨ 적용] 클릭")
        else:
            if not self._btn_eraser.isChecked() and not self._btn_restore.isChecked():
                self._canvas.set_tool(_BgCanvas.TOOL_NONE)
            self._btn_inpaint_apply.hide()
            self._btn_mask_clear.hide()

    def _clear_mask(self):
        self._canvas.clear_mask()
        self._status.setText("🔤 마스크 초기화됨 — 다시 칠해주세요")

    # ── 인페인팅 실행 ──
    def _do_inpaint(self):
        if not self._canvas.get_image() or not self._canvas.has_mask_content():
            self._status.setText("⚠️ 먼저 제거할 자막 영역을 칠해주세요")
            return

        import tempfile as _tf

        # 이미지 저장
        img_fd, img_path = _tf.mkstemp(suffix=".png")
        os.close(img_fd)
        self._canvas.get_image().save(img_path, "PNG")

        # 마스크 저장 (RGBA PNG — 헬퍼에서 알파>0 을 흰색으로 변환)
        mask_fd, mask_path = _tf.mkstemp(suffix=".png")
        os.close(mask_fd)
        self._canvas.get_mask().save(mask_path, "PNG")

        self._inpaint_img_path = img_path
        self._inpaint_mask_path = mask_path
        self._btn_inpaint_apply.setEnabled(False)
        self._progress.show()
        self._status.setText("⏳ 자막 제거 처리 중...")

        self._inpaint_worker = _InpaintWorker(img_path, mask_path)
        self._inpaint_worker.finished.connect(self._on_inpaint_done)
        self._inpaint_worker.error.connect(self._on_inpaint_error)
        self._inpaint_worker.start()

    def _on_inpaint_done(self, result_img: QImage):
        self._progress.hide()
        self._canvas.set_image(result_img, keep_undo=True)
        self._canvas.clear_mask()
        self._btn_inpaint_apply.setEnabled(True)
        self._update_undo_redo_btns()
        w, h = result_img.width(), result_img.height()
        self._status.setText(f"✅ 자막 제거 완료  ({w}×{h}) — 더 지울 영역이 있으면 다시 칠해주세요")
        self._inpaint_worker = None
        # 임시 파일 정리
        for p in (getattr(self, '_inpaint_img_path', ''), getattr(self, '_inpaint_mask_path', '')):
            try: os.remove(p)
            except: pass

    def _on_inpaint_error(self, err: str):
        self._progress.hide()
        self._btn_inpaint_apply.setEnabled(True)
        self._status.setText(f"❌ 인페인팅 오류: {err}")
        self._inpaint_worker = None
        for p in (getattr(self, '_inpaint_img_path', ''), getattr(self, '_inpaint_mask_path', '')):
            try: os.remove(p)
            except: pass

    def _on_size_changed(self, val: int):
        self._size_val.setText(f"{val}")
        self._canvas.set_brush_size(val)

    def _undo(self):
        self._canvas.undo()
        self._update_undo_redo_btns()

    def _redo(self):
        self._canvas.redo()
        self._update_undo_redo_btns()

    def _update_undo_redo_btns(self):
        self._btn_undo.setEnabled(self._canvas.can_undo())
        self._btn_redo.setEnabled(self._canvas.can_redo())

    # ── PNG 저장 ──
    def _save_png(self):
        img = self._canvas.get_image()
        if not img:
            return
        base = os.path.splitext(os.path.basename(self._original_path))[0]
        default_name = f"{base}_no_bg.png"
        path, _ = QFileDialog.getSaveFileName(
            self, "투명 PNG 저장", default_name,
            "PNG 이미지 (*.png)"
        )
        if path:
            img.save(path, "PNG")
            self._status.setText(f"💾 저장 완료: {path}")


class MainShell(QMainWindow):
    """메인 셸 - 왼쪽 네비게이션 바 + 콘텐츠 페이지"""

    def __init__(self, manager, engine, app_settings):
        super().__init__()
        self.manager = manager
        self.engine = engine
        self.app_settings = app_settings

        self.setWindowTitle("Q-fred")
        self.setMinimumSize(964, 550)
        self.resize(1020, 600)

        logo_path = os.path.join(RESOURCE_DIR, "q_logo_hd.ico")
        if os.path.exists(logo_path):
            self.setWindowIcon(QIcon(logo_path))

        # QfredApp 생성 (UI 위젯만 사용, 창은 표시하지 않음)
        self.qfred = QfredApp(manager, engine, app_settings)
        self.qfred.tray_icon.hide()
        qfred_widget = self.qfred.centralWidget()

        # 셸 UI
        central = QWidget()
        central.setStyleSheet("background-color: #0f172a;")
        self.setCentralWidget(central)

        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ===== 왼쪽 네비게이션 바 =====
        nav = QFrame()
        nav.setFixedWidth(64)
        nav.setStyleSheet("""
            QFrame {
                background-color: #0b1120;
                border-right: 1px solid #1e293b;
            }
        """)
        nav_layout = QVBoxLayout(nav)
        nav_layout.setContentsMargins(0, 8, 0, 12)
        nav_layout.setSpacing(2)

        # Q 로고 이미지
        q_logo = QLabel()
        q_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        q_logo.setFixedHeight(36)
        q_logo.setStyleSheet("background: transparent; border: none;")
        logo_img_path = os.path.join(RESOURCE_DIR, "q_logo.png")
        if os.path.exists(logo_img_path):
            pixmap = QPixmap(logo_img_path).scaled(28, 28, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            q_logo.setPixmap(pixmap)
        else:
            q_logo.setText("Q")
            q_logo.setStyleSheet("color: #4a946c; font-size: 18px; font-weight: bold; background: transparent; border: none;")
        nav_layout.addWidget(q_logo)

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background-color: #1e293b; border: none;")
        nav_layout.addWidget(sep)
        nav_layout.addSpacing(8)

        self.nav_buttons = []

        snippets_btn = NavButton("⚡", "Snippets")
        snippets_btn.clicked.connect(lambda: self.switch_page(0))
        nav_layout.addWidget(snippets_btn)
        self.nav_buttons.append(snippets_btn)

        download_btn = NavButton("⬇", "Download")
        download_btn.clicked.connect(lambda: self.switch_page(1))
        nav_layout.addWidget(download_btn)
        self.nav_buttons.append(download_btn)

        color_btn = NavButton("🎨", "Color")
        color_btn.clicked.connect(lambda: self.switch_page(2))
        nav_layout.addWidget(color_btn)
        self.nav_buttons.append(color_btn)

        bg_btn = NavButton("🧽", "BG Remove")
        bg_btn.clicked.connect(lambda: self.switch_page(3))
        nav_layout.addWidget(bg_btn)
        self.nav_buttons.append(bg_btn)

        nav_layout.addStretch()

        # 버전 표시
        ver_label = QLabel(f"v{APP_VERSION}")
        ver_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ver_label.setStyleSheet("color: #334155; font-size: 9px; background: transparent; border: none;")
        nav_layout.addWidget(ver_label)

        main_layout.addWidget(nav)

        # ===== 콘텐츠 페이지 스택 =====
        self.page_stack = QStackedWidget()
        self.page_stack.addWidget(qfred_widget)

        self.downloader = DownloaderPage(app_settings=self.app_settings)
        self.page_stack.addWidget(self.downloader)

        self.color_picker = ColorPickerPage()
        self.page_stack.addWidget(self.color_picker)

        self.bg_remover = BgRemovePage()
        self.page_stack.addWidget(self.bg_remover)

        main_layout.addWidget(self.page_stack, 1)

        # 트레이 아이콘 설정
        self.setup_tray()

        # 기본 페이지: Snippets
        self.switch_page(0)

    def switch_page(self, index):
        self.page_stack.setCurrentIndex(index)
        for i, btn in enumerate(self.nav_buttons):
            btn.active = (i == index)

    def setup_tray(self):
        self.tray_icon = QSystemTrayIcon(self)
        logo_path = os.path.join(RESOURCE_DIR, "q_logo_hd.ico")
        if os.path.exists(logo_path):
            self.tray_icon.setIcon(QIcon(logo_path))

        tray_menu = QMenu()
        show_action = QAction("열기", self)
        show_action.triggered.connect(self.show_window)
        tray_menu.addAction(show_action)
        tray_menu.addSeparator()
        quit_action = QAction("종료", self)
        quit_action.triggered.connect(self.quit_app)
        tray_menu.addAction(quit_action)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self.on_tray_activated)
        self.tray_icon.setToolTip("Q-fred")
        self.tray_icon.show()

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show_window()

    def show_window(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        event.ignore()
        self.hide()

    def quit_app(self):
        self.engine.stop()
        self.tray_icon.hide()
        QApplication.quit()

    def show_update_notification(self, latest_ver, download_url):
        QTimer.singleShot(0, lambda: self._show_update_dialog(latest_ver, download_url))

    def _show_update_dialog(self, latest_ver, download_url):
        if not download_url:
            return
        reply = QMessageBox.question(
            self, 'Q-fred 업데이트',
            f"새 버전 {latest_ver}이(가) 있습니다.\n현재 버전: {APP_VERSION}\n\n업데이트 하시겠습니까?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        progress = QMessageBox(self)
        progress.setWindowTitle("업데이트")
        progress.setText("다운로드 중... 0%")
        progress.setStandardButtons(QMessageBox.StandardButton.NoButton)
        progress.show()
        QApplication.processEvents()

        def on_progress(percent):
            progress.setText(f"다운로드 중... {percent}%")
            QApplication.processEvents()

        update_path = download_update(download_url, progress_callback=on_progress)
        progress.close()

        if update_path:
            QMessageBox.information(self, '업데이트', '다운로드 완료! 앱을 재시작합니다.')
            self.engine.stop()
            self.tray_icon.hide()
            apply_update(update_path)
        else:
            QMessageBox.warning(self, '업데이트 실패', '다운로드에 실패했습니다.\n나중에 다시 시도해주세요.')


def kill_existing_qfred():
    """기존 실행 중인 Q-fred 프로세스를 강제 종료"""
    try:
        current_pid = os.getpid()
        # tasklist로 Qfred 프로세스 찾기
        result = subprocess.run(
            ['tasklist', '/FI', 'IMAGENAME eq Qfred.exe', '/FO', 'CSV', '/NH'],
            capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW
        )
        for line in result.stdout.strip().split('\n'):
            if 'Qfred.exe' in line:
                parts = line.strip('"').split('","')
                if len(parts) >= 2:
                    pid = int(parts[1])
                    if pid != current_pid:
                        os.kill(pid, 9)
                        print(f"[Startup] 기존 Q-fred (PID {pid}) 종료")
                        time.sleep(0.5)
        # python으로 실행 중인 경우도 처리
        result2 = subprocess.run(
            ['wmic', 'process', 'where', "commandline like '%qfred_pyqt%'", 'get', 'processid'],
            capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW
        )
        for line in result2.stdout.strip().split('\n'):
            line = line.strip()
            if line.isdigit():
                pid = int(line)
                if pid != current_pid:
                    os.kill(pid, 9)
                    print(f"[Startup] 기존 Q-fred python (PID {pid}) 종료")
                    time.sleep(0.5)
    except Exception as e:
        print(f"[Startup] 기존 프로세스 종료 실패 (무시): {e}")


def main():
    # 기존 Q-fred 프로세스 강제 종료 후 시작
    import tempfile
    lock_file = os.path.join(tempfile.gettempdir(), "qfred.lock")

    try:
        lock_handle = open(lock_file, 'w')
        import msvcrt
        msvcrt.locking(lock_handle.fileno(), msvcrt.LK_NBLCK, 1)
    except:
        # 이미 실행 중 → 기존 프로세스 강제 종료
        print("[Startup] 기존 Q-fred 감지, 강제 종료 후 재시작...")
        kill_existing_qfred()
        # 락 파일 삭제 후 다시 시도
        try:
            os.remove(lock_file)
        except:
            pass
        time.sleep(1)
        try:
            lock_handle = open(lock_file, 'w')
            import msvcrt
            msvcrt.locking(lock_handle.fileno(), msvcrt.LK_NBLCK, 1)
        except:
            print("[Startup] 락 획득 실패, 강제 진행")
            lock_handle = None

    # 앱 종료 시 lock 파일 자동 삭제
    import atexit
    def cleanup_lock():
        try:
            if lock_handle:
                lock_handle.close()
            os.remove(lock_file)
        except:
            pass
    atexit.register(cleanup_lock)

    app = QApplication(sys.argv)

    # 앱 설정 초기화
    app_settings = AppSettings()

    # 저장 폴더 생성
    os.makedirs(app_settings.storage_folder, exist_ok=True)

    # 매니저 및 엔진 초기화
    manager = SnippetManager(snippets_file=app_settings.snippets_file)
    engine = SnippetEngine(manager)

    # MainShell로 감싸서 네비게이션 바 추가
    window = MainShell(manager, engine, app_settings)

    # 트레이 모드: 설정에 따라 창 표시 여부 결정
    if app_settings.start_minimized:
        window.hide()
    else:
        window.show()

    # 엔진 시작 (창이 숨겨져 있어도 동작)
    engine.start()

    # 백그라운드에서 업데이트 체크
    def check_update_background():
        has_update, latest_ver, download_url = check_for_updates()
        if has_update:
            window.show_update_notification(latest_ver, download_url)

    update_thread = threading.Thread(target=check_update_background, daemon=True)
    update_thread.start()

    sys.exit(app.exec())


if __name__ == "__main__":
    import faulthandler
    _fh_log = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qfred_crash.log")
    _fh_file = open(_fh_log, 'w', encoding='utf-8')
    faulthandler.enable(file=_fh_file)
    try:
        main()
    except Exception as e:
        import traceback
        error_log = os.path.join(
            os.path.dirname(sys.executable) if getattr(sys, 'frozen', False)
            else os.path.dirname(os.path.abspath(__file__)),
            "qfred_error.log"
        )
        with open(error_log, 'w', encoding='utf-8') as f:
            f.write(traceback.format_exc())
        raise
