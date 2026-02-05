"""
Qfred - Smart Snippet Manager for Windows (PyQt6 Version)
시스템 전역에서 단축어를 감지하고 치환하는 프로그램
"""

import json
import os
import sys
import threading
import time
import pyperclip
import uuid
import ctypes
from datetime import datetime
from pynput import keyboard as pynput_keyboard
from pynput.keyboard import Key, Controller

# 콘솔 창 숨기기
def hide_console():
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE = 0
    except:
        pass

hide_console()
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QTextEdit, QPushButton, QListWidget, QListWidgetItem,
    QFrame, QScrollArea, QSystemTrayIcon, QMenu, QSplitter, QMessageBox,
    QSizePolicy, QStackedWidget, QSpacerItem
)
from PyQt6.QtCore import Qt, QSize, pyqtSignal, QObject, QTimer, QEvent
from PyQt6.QtGui import QIcon, QPixmap, QFont, QColor, QPalette, QAction, QFontDatabase, QCursor

# 설정 파일 경로
APP_DIR = os.path.dirname(os.path.abspath(__file__))
SNIPPETS_FILE = os.path.join(APP_DIR, "snippets.json")

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

# Virtual Key Code -> QWERTY 키 매핑 (pynput용)
VK_TO_QWERTY = {
    # 숫자 키 (상단)
    0x30: '0', 0x31: '1', 0x32: '2', 0x33: '3', 0x34: '4',
    0x35: '5', 0x36: '6', 0x37: '7', 0x38: '8', 0x39: '9',
    # 알파벳 키 (A-Z는 0x41-0x5A)
    0x41: 'a', 0x42: 'b', 0x43: 'c', 0x44: 'd', 0x45: 'e',
    0x46: 'f', 0x47: 'g', 0x48: 'h', 0x49: 'i', 0x4A: 'j',
    0x4B: 'k', 0x4C: 'l', 0x4D: 'm', 0x4E: 'n', 0x4F: 'o',
    0x50: 'p', 0x51: 'q', 0x52: 'r', 0x53: 's', 0x54: 't',
    0x55: 'u', 0x56: 'v', 0x57: 'w', 0x58: 'x', 0x59: 'y', 0x5A: 'z',
    # 기호 키
    0xBD: '-', 0xBB: '=', 0xDB: '[', 0xDD: ']', 0xDC: '\\',
    0xBA: ';', 0xDE: "'", 0xBC: ',', 0xBE: '.', 0xBF: '/',
    0xC0: '`',
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


class SnippetManager:
    """스니펫 데이터 관리"""

    def __init__(self):
        self.snippets = []
        self.load()

    def load(self):
        if os.path.exists(SNIPPETS_FILE):
            try:
                with open(SNIPPETS_FILE, 'r', encoding='utf-8') as f:
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
        with open(SNIPPETS_FILE, 'w', encoding='utf-8') as f:
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
        self.listener = None
        self.keyboard_controller = Controller()
        self.ctrl_pressed = False
        self.alt_pressed = False
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
        if key == Key.cmd:
            self.buffer = ""
            return

        # Modifier가 눌려있으면 버퍼 초기화
        if self.ctrl_pressed or self.alt_pressed:
            self.buffer = ""
            return

        # 종결키 처리
        if key == Key.space or key == Key.enter or key == Key.tab:
            matched = self._check_triggers_on_end_key()
            if not matched:
                self.buffer = ""
            return

        # Backspace 처리
        if key == Key.backspace:
            if self.buffer:
                self.buffer = self.buffer[:-1]
            return

        # 네비게이션 키 - 버퍼 초기화
        if key in [Key.esc, Key.left, Key.right, Key.up, Key.down, Key.home, Key.end, Key.delete]:
            self.buffer = ""
            return

        # 일반 문자 키 처리 (VK 코드 기반)
        try:
            if vk and vk in VK_TO_QWERTY:
                char = VK_TO_QWERTY[vk]
                self.buffer += char
                if len(self.buffer) > self.max_trigger_len + 5:
                    self.buffer = self.buffer[-(self.max_trigger_len + 5):]
        except:
            pass

    def on_release(self, key):
        # Modifier 키 해제 추적
        if key == Key.ctrl_l or key == Key.ctrl_r:
            self.ctrl_pressed = False
        if key == Key.alt_l or key == Key.alt_r or key == Key.alt_gr:
            self.alt_pressed = False

    def _check_triggers_on_end_key(self) -> bool:
        if self.is_replacing:
            return False
        for trigger, content in self.trigger_map.items():
            if self.buffer.endswith(trigger):
                self.is_replacing = True  # 먼저 플래그 설정
                self.buffer = ""
                threading.Thread(target=self._replace, args=(trigger, content), daemon=True).start()
                return True
        return False

    def _replace(self, trigger: str, content: str):
        try:
            time.sleep(0.05)

            try:
                old_clipboard = pyperclip.paste()
            except:
                old_clipboard = ""

            backspace_count = len(trigger) + 1
            for _ in range(backspace_count):
                self.keyboard_controller.press(Key.backspace)
                self.keyboard_controller.release(Key.backspace)
                time.sleep(0.02)

            time.sleep(0.05)
            pyperclip.copy(content)
            time.sleep(0.05)

            # Ctrl+V
            self.keyboard_controller.press(Key.ctrl_l)
            time.sleep(0.01)
            self.keyboard_controller.press('v')
            time.sleep(0.01)
            self.keyboard_controller.release('v')
            time.sleep(0.01)
            self.keyboard_controller.release(Key.ctrl_l)
            time.sleep(0.15)

            try:
                pyperclip.copy(old_clipboard)
            except:
                pass
        except:
            pass
        finally:
            self.buffer = ""  # 버퍼 초기화
            self.is_replacing = False

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

        # 하단: 내용 미리보기
        preview = self.snippet["content"].replace('\n', ' ')[:25]
        if len(self.snippet["content"]) > 25:
            preview += "..."
        preview_label = QLabel(preview)
        preview_label.setStyleSheet("color: #94a3b8; font-size: 12px;")
        preview_label.setMaximumWidth(260)
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

    def __init__(self, manager: SnippetManager, engine: SnippetEngine):
        super().__init__()
        self.manager = manager
        self.engine = engine
        self.selected_id = None
        self.current_tab = "snippets"

        self.setWindowTitle("Qfred - Smart Snippet Manager")
        self.setMinimumSize(900, 550)
        self.resize(950, 600)

        # 아이콘 설정
        logo_path = os.path.join(APP_DIR, "q_logo.png")
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

        # 타이틀
        title = QLabel("Qfred")
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #ffffff;")
        sidebar_layout.addWidget(title)

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
        self.trigger_input.setPlaceholderText("e.g. rt")
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
        help_label = QLabel("한글 또는 영문으로 입력")
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

        logo_path = os.path.join(APP_DIR, "q_logo.png")
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
        self.tray_icon.setToolTip("Qfred - 단축어 관리자")
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

        # 한글 입력 처리
        has_korean = any('\uAC00' <= c <= '\uD7A3' or '\u3131' <= c <= '\u3163' for c in trigger_input)

        if has_korean:
            qwerty_converted = convert_to_qwerty(trigger_input)
            trigger = convert_to_korean(qwerty_converted)
        else:
            trigger = convert_to_korean(trigger_input)

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


def main():
    # 중복 실행 방지
    import tempfile
    lock_file = os.path.join(tempfile.gettempdir(), "qfred_pyqt.lock")

    try:
        # Windows에서 파일 잠금
        lock_handle = open(lock_file, 'w')
        import msvcrt
        msvcrt.locking(lock_handle.fileno(), msvcrt.LK_NBLCK, 1)
    except:
        # 이미 실행 중
        print("Qfred is already running.")
        sys.exit(0)

    app = QApplication(sys.argv)

    # 매니저 및 엔진 초기화
    manager = SnippetManager()
    engine = SnippetEngine(manager)

    # GUI 생성 (창이 열린 상태에서는 훅 비활성화)
    window = QfredApp(manager, engine)
    window.show()

    # 엔진 시작 (창이 열려있어도 동작)
    engine.start()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
