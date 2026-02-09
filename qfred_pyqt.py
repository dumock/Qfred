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
APP_VERSION = "1.0.22"
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
    QComboBox, QProgressBar
)
from PyQt6.QtCore import Qt, QSize, pyqtSignal, QObject, QTimer, QEvent, QThread
from PyQt6.QtGui import QIcon, QPixmap, QFont, QColor, QPalette, QAction, QFontDatabase, QCursor

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
                dur_str = f"{duration // 60}:{duration % 60:02d}" if duration else ""
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
