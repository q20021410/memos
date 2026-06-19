import sys
import os
import sqlite3
import base64
import re
import json
import traceback
import webbrowser
import shutil
import subprocess
from datetime import datetime
from urllib.parse import unquote

try:
    import pymysql
except ImportError:
    pymysql = None

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QListWidget, QPushButton, QLineEdit, 
                             QMessageBox, QListWidgetItem, QStackedWidget, QLabel, 
                             QComboBox, QFileDialog, QDialog, QTextEdit, 
                             QSplitter, QFrame, QInputDialog, QTreeWidget, 
                             QTreeWidgetItem, QGridLayout, QMenu, QAbstractItemView, QStyle, QColorDialog, QTreeWidgetItemIterator)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineSettings, QWebEnginePage
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtCore import Qt, QUrl, QObject, pyqtSlot, QTimer, QEvent, QByteArray 
from PyQt6.QtGui import QFont, QIcon, QColor, QBrush, QPixmap, QShortcut, QKeySequence

def global_exception_handler(exc_type, exc_value, exc_traceback):
    print("\n=========================================")
    print("❌ 프로그램 구동 도중 오류가 발생했습니다:")
    traceback.print_exception(exc_type, exc_value, exc_traceback)
    print("=========================================\n")
sys.excepthook = global_exception_handler

# --- 경로 및 보안 설정 ---
if getattr(sys, 'frozen', False):
    APP_PATH = os.path.dirname(sys.executable)
else:
    APP_PATH = os.path.dirname(os.path.abspath(__file__))

CONFIG_PATH = os.path.join(APP_PATH, 'config.json')
DEFAULT_SQLITE_PATH = os.path.join(APP_PATH, 'memo.db')
SECRET_KEY = b'fIbT6dVeEmoGaMW5sw2Lr8wciuE9kxFIEEpxckpVeAZh2VM9jl'
IS_RESTARTING = False

# --- 🌟 DB 하이브리드 매니저 (Zero-Config) ---
def get_db_config():
    # 기본 위치는 exe(또는 py) 파일과 같은 폴더
    sqlite_path = os.path.join(APP_PATH, 'memo.db') 
    
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
                # config에 지정된 커스텀 경로가 있다면 덮어쓰기
                if "sqlite_path" in cfg and cfg["sqlite_path"]:
                    sqlite_path = cfg["sqlite_path"]
                return cfg.get("db_type", "sqlite"), cfg.get("mysql", {}), sqlite_path
        except: pass
    return "sqlite", {}, sqlite_path

# 전역 변수 세팅 (DEFAULT 대신 CURRENT_SQLITE_PATH를 씁니다)
DB_TYPE, MYSQL_CONFIG, CURRENT_SQLITE_PATH = get_db_config()

class MySQLWrapperCursor:
    def __init__(self, cursor):
        self.cursor = cursor
        
    def execute(self, query, params=()):
        if isinstance(query, str):
            query = query.replace("AUTOINCREMENT", "AUTO_INCREMENT")
            query = query.replace(
                "IN (SELECT history_id FROM memo_history WHERE memo_id = ? ORDER BY history_id DESC LIMIT 10)",
                "IN (SELECT history_id FROM (SELECT history_id FROM memo_history WHERE memo_id = ? ORDER BY history_id DESC LIMIT 10) AS _tmp)"
            )
            query = query.replace("?", "%s")
            query = query.replace("datetime('now', 'localtime')", "NOW()")
            query = query.replace("datetime('now', '-7 days')", "DATE_SUB(NOW(), INTERVAL 7 DAY)")
            query = query.replace("datetime('now', '-5 days')", "DATE_SUB(NOW(), INTERVAL 5 DAY)")
            query = query.replace("datetime(deleted_at)", "deleted_at")
            query = query.replace("INSERT OR REPLACE INTO", "REPLACE INTO")
            query = query.replace("INSERT OR IGNORE INTO", "INSERT IGNORE INTO")
            
            # ★ 추가됨: 본문(content)을 64KB 제한에서 4GB(LONGTEXT)로 무제한 확장!
            query = query.replace("content TEXT", "content LONGTEXT")
            
            query = query.replace("key TEXT PRIMARY KEY", "`key` VARCHAR(100) PRIMARY KEY")
            query = query.replace("username TEXT UNIQUE", "username VARCHAR(100) UNIQUE")
            query = query.replace("TEXT DEFAULT ''", "VARCHAR(255) DEFAULT ''")
            query = query.replace("TEXT DEFAULT '알 수 없음'", "VARCHAR(100) DEFAULT '알 수 없음'")
            query = query.replace("TEXT DEFAULT NULL", "VARCHAR(255) DEFAULT NULL")
            
            query = query.replace("(key, value)", "(`key`, value)")
            query = query.replace("WHERE key=", "WHERE `key`=")
            query = query.replace("WHERE key =", "WHERE `key` =")
            
            if "PRAGMA table_info" in query:
                table_name = query.split("(")[1].split(")")[0]
                query = f"SHOW COLUMNS FROM {table_name}"
                
        self.cursor.execute(query, params)
        return self
        
    def fetchone(self): return self.cursor.fetchone()
    def fetchall(self): return self.cursor.fetchall()
    def close(self): self.cursor.close()
    
    @property
    def lastrowid(self): return self.cursor.lastrowid

class MySQLWrapperConnection:
    def __init__(self, conn):
        self.conn = conn
        self.isolation_level = None
    def cursor(self): return MySQLWrapperCursor(self.conn.cursor())
    def commit(self): self.conn.commit()
    def close(self): self.conn.close()
    def execute(self, query, params=()):
        c = self.cursor()
        c.execute(query, params)
        return c

def get_db_connection():
    if DB_TYPE == "mysql":
        if pymysql is None:
            raise Exception("pymysql 라이브러리가 설치되지 않았습니다. 서버 연동을 위해 'pip install pymysql'을 실행하세요.")
        conn = pymysql.connect(
            host=MYSQL_CONFIG.get("host", "localhost"),
            port=MYSQL_CONFIG.get("port", 3306),
            user=MYSQL_CONFIG.get("user", "root"),
            password=MYSQL_CONFIG.get("password", ""),
            database=MYSQL_CONFIG.get("database", "memo_db"),
            charset='utf8mb4'
        )
        return MySQLWrapperConnection(conn)
    else:
        return sqlite3.connect(CURRENT_SQLITE_PATH)

# --- 🖥️ 로컬 개인화 설정 매니저 (서버 공유 방지용) ---
def get_local_db_connection():
    return sqlite3.connect(CURRENT_SQLITE_PATH)

def get_local_setting(key, default_value=""):
    conn = get_local_db_connection()
    c = conn.cursor()
    try:
        c.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        c.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = c.fetchone()
    except: row = None
    conn.close()
    return row[0] if row else default_value

def set_local_setting(key, value):
    conn = get_local_db_connection()
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()
    conn.close()

# --- 텍스트 암호화 유틸 ---
def obfuscate_text(text):
    if not text: return ""
    text_bytes = text.encode('utf-8')
    xored = bytearray([b ^ SECRET_KEY[i % len(SECRET_KEY)] for i, b in enumerate(text_bytes)])
    return base64.b64encode(xored).decode('utf-8')

def deobfuscate_text(obfuscated_str):
    if not obfuscated_str: return ""
    try:
        xored = base64.b64decode(obfuscated_str)
        text_bytes = bytearray([b ^ SECRET_KEY[i % len(SECRET_KEY)] for i, b in enumerate(xored)])
        return text_bytes.decode('utf-8')
    except:
        return obfuscated_str

def format_size(size_bytes):
    if size_bytes < 1024: return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024: return f"{size_bytes / 1024:.1f} KB"
    else: return f"{size_bytes / (1024 * 1024):.1f} MB"

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS memos
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, content TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE)''')
    c.execute('''CREATE TABLE IF NOT EXISTS folders
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)''')
    
    try: c.execute("ALTER TABLE folders ADD COLUMN parent_id INTEGER DEFAULT NULL")
    except Exception: pass
    
    try: c.execute("ALTER TABLE folders ADD COLUMN color TEXT DEFAULT ''")
    except Exception: pass

    try: c.execute("ALTER TABLE folders ADD COLUMN sort_order INTEGER DEFAULT 0")
    except Exception: pass
    
    try: c.execute("ALTER TABLE memos ADD COLUMN password TEXT DEFAULT ''")
    except Exception: pass
    
    c.execute("SELECT COUNT(*) FROM users")
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO users (username) VALUES ('관리자')")
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('current_user', '관리자')")
        
    c.execute("SELECT COUNT(*) FROM folders")
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO folders (name) VALUES ('기본 폴더')")
    
    try: c.execute("ALTER TABLE memos ADD COLUMN tags TEXT DEFAULT ''")
    except Exception: pass
    try: c.execute("ALTER TABLE memos ADD COLUMN created_at TEXT")
    except Exception: pass
    try: c.execute("ALTER TABLE memos ADD COLUMN updated_at TEXT")
    except Exception: pass
    try: c.execute("ALTER TABLE memos ADD COLUMN author TEXT DEFAULT '알 수 없음'")
    except Exception: pass
    try: c.execute("ALTER TABLE memos ADD COLUMN last_editor TEXT DEFAULT '알 수 없음'")
    except Exception: pass
    try: c.execute("ALTER TABLE memos ADD COLUMN deleted_at TEXT DEFAULT NULL")
    except Exception: pass
    try: c.execute("ALTER TABLE memos ADD COLUMN revision INTEGER DEFAULT 1")
    except Exception: pass
    try: c.execute("ALTER TABLE memos ADD COLUMN folder_id INTEGER DEFAULT 1")
    except Exception: pass
    
    c.execute("UPDATE memos SET created_at = datetime('now', 'localtime') WHERE created_at IS NULL")
    c.execute("UPDATE memos SET updated_at = datetime('now', 'localtime') WHERE updated_at IS NULL")
    c.execute("UPDATE memos SET revision = 1 WHERE revision IS NULL")
    c.execute("UPDATE memos SET folder_id = 1 WHERE folder_id IS NULL")

    c.execute('''CREATE TABLE IF NOT EXISTS memo_history
                 (history_id INTEGER PRIMARY KEY AUTOINCREMENT, memo_id INTEGER,
                  title TEXT, content TEXT, tags TEXT, saved_at TEXT)''')
    try: c.execute("ALTER TABLE memo_history ADD COLUMN editor TEXT DEFAULT '알 수 없음'")
    except Exception: pass
    try: c.execute("ALTER TABLE memo_history ADD COLUMN revision INTEGER DEFAULT 1")
    except Exception: pass
    
    c.execute("SELECT COUNT(*) FROM settings WHERE key='lock_timeout'")
    if c.fetchone()[0] == 0: 
        c.execute("INSERT INTO settings (key, value) VALUES ('lock_timeout', '10')")
        
    c.execute("SELECT COUNT(*) FROM settings WHERE key='theme_mode'")
    if c.fetchone()[0] == 0: c.execute("INSERT INTO settings (key, value) VALUES ('theme_mode', 'system')")
    
    c.execute("SELECT COUNT(*) FROM settings WHERE key='zoom_factor'")
    if c.fetchone()[0] == 0: c.execute("INSERT INTO settings (key, value) VALUES ('zoom_factor', '1.0')")
                  
    conn.commit()
    conn.close()

def get_current_username():
    import socket
    hostname = socket.gethostname()
    
    # 서버/로컬 가리지 않고 무조건 PC 귀속(MDM) 우선 검사!
    conn_main = get_db_connection()
    c_main = conn_main.cursor()
    try:
        c_main.execute("SELECT value FROM settings WHERE `key`=?", (f"pc_owner_{hostname}",))
        owner_row = c_main.fetchone()
    except: owner_row = None
    conn_main.close()
    
    if owner_row and owner_row[0]:
        return owner_row[0] 
        
    # 귀속이 안 된 자유 PC일 때만 내 로컬 설정에서 가져옴
    return get_local_setting("current_user", "관리자")

def inject_html_safe(viewer, html_content, margin="0px", theme_js=""):
    if not html_content: html_content = ""
    b64_content = base64.b64encode(html_content.encode('utf-8')).decode('utf-8')
    js_code = f"""
        if (!document.getElementById('custom-spacing-fix')) {{
            var style = document.createElement('style');
            style.id = 'custom-spacing-fix';
            style.innerHTML = `
                body {{ margin: {margin}; padding: 0px; line-height: 1.5; word-wrap: break-word; font-family: sans-serif; }}
                p, div, blockquote, ul, ol, li, h1, h2, h3, h4, h5, h6 {{ 
                    margin-top: 0.3em !important; 
                    margin-bottom: 0.3em !important; 
                }}
                img {{ max-width: 100%; height: auto; }}
            `;
            document.head.appendChild(style);
            document.addEventListener('contextmenu', event => event.preventDefault());
        }}
        try {{
            var decoded = decodeURIComponent(escape(atob('{b64_content}')));
            document.body.innerHTML = decoded;
        }} catch(e) {{
            document.body.innerHTML = "내용을 불러오는 중 오류가 발생했습니다.";
        }}
        {theme_js}
    """
    viewer.page().runJavaScript(js_code)

def get_folder_path_list(conn, folder_id):
    path = []
    curr = folder_id
    c = conn.cursor()
    while curr:
        c.execute("SELECT name, parent_id FROM folders WHERE id=?", (curr,))
        row = c.fetchone()
        if row:
            path.insert(0, row[0])
            curr = row[1]
        else:
            break
    return path

def ensure_folder_path_exists(conn, path_list):
    if not path_list: return 1
    c = conn.cursor()
    parent_id = None
    curr_id = 1
    for fname in path_list:
        if parent_id is None:
            c.execute("SELECT id FROM folders WHERE name=? AND parent_id IS NULL", (fname,))
        else:
            c.execute("SELECT id FROM folders WHERE name=? AND parent_id=?", (fname, parent_id))
        row = c.fetchone()
        if row:
            curr_id = row[0]
            parent_id = row[0]
        else:
            c.execute("INSERT INTO folders (name, parent_id) VALUES (?, ?)", (fname, parent_id))
            curr_id = c.lastrowid
            parent_id = curr_id
    conn.commit()
    return curr_id

class ImageBridge(QObject):
    def __init__(self, app_instance):
        super().__init__()
        self.app = app_instance
    @pyqtSlot(str, str)
    def save_pasted_image(self, base64_data, filename): pass

# --- 🌟 커스텀 웹 페이지 라우터 (내부 링크 memo:// 감지용) ---
class CustomWebPage(QWebEnginePage):
    def __init__(self, parent_app, profile, parent=None):
        super().__init__(profile, parent)
        self.parent_app = parent_app

    def acceptNavigationRequest(self, url, _type, isMainFrame):
        if _type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked:
            scheme = url.scheme()
            
            # 우리만의 전용 프로토콜 (memo://) 인 경우
            if scheme == "memo":
                target = url.toString().replace("memo://", "").replace("memo:", "").strip("/")
                target = unquote(target) 
                
                # 1. 한글 도메인 퓨니코드(xn--) 해독
                if target.startswith("xn--"):
                    try:
                        target = target.encode('ascii').decode('idna')
                    except Exception:
                        pass
                
                # 2. ★ 핵심: 브라우저가 숫자(ID)를 IP주소로 착각해서 변환한 것을 다시 숫자로 완벽 복구!
                # 예: memo://3 입력 -> 브라우저가 0.0.0.3 으로 변환 -> 다시 3으로 역산하여 복원
                if re.match(r'^\d+\.\d+\.\d+\.\d+$', target):
                    try:
                        parts = target.split('.')
                        recovered_id = (int(parts[0]) << 24) + (int(parts[1]) << 16) + (int(parts[2]) << 8) + int(parts[3])
                        target = str(recovered_id)
                    except: pass
                
                conn = get_db_connection()
                c = conn.cursor()
                if target.isdigit():
                    c.execute("SELECT id FROM memos WHERE id=? AND deleted_at IS NULL", (int(target),))
                else:
                    c.execute("SELECT id FROM memos WHERE title=? AND deleted_at IS NULL", (target,))
                row = c.fetchone()
                conn.close()
                
                main_app = self.parent_app if hasattr(self.parent_app, 'load_memo_content') else self.parent_app.main_app
                
                if row:
                    m_id = row[0]
                    item = QListWidgetItem()
                    item.setData(Qt.ItemDataRole.UserRole, m_id)
                    main_app.load_memo_content(item)
                else:
                    QMessageBox.warning(main_app, "링크 오류", f"해당 문서('{target}')를 찾을 수 없거나 이미 삭제되었습니다.")
                return False 
                
            # 일반 인터넷 링크 (http://, https://) 인 경우
            elif scheme in ["http", "https"]:
                webbrowser.open(url.toString())
                return False
                
        return super().acceptNavigationRequest(url, _type, isMainFrame)

class SearchBarWidget(QFrame):
    def __init__(self, webview, parent=None):
        super().__init__(parent)
        self.webview = webview
        self.setFixedHeight(40)
        self.setStyleSheet("QFrame { background-color: rgba(128, 128, 128, 0.15); border-radius: 6px; }")
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 10, 0)
        
        lbl_icon = QLabel("🔍", self)
        lbl_icon.setStyleSheet("background: transparent;")
        
        self.input = QLineEdit(self)
        self.input.setPlaceholderText("검색어 입력 후 Enter...")
        self.input.setStyleSheet("background: transparent; border: none; font-size: 14px;")
        self.input.returnPressed.connect(self.find_next)
        
        self.btn_prev = QPushButton("↑", self)
        self.btn_prev.setFixedSize(30, 30)
        self.btn_prev.setStyleSheet("background: transparent; border: none; font-weight: bold;")
        self.btn_prev.clicked.connect(self.find_prev)
        
        self.btn_next = QPushButton("↓", self)
        self.btn_next.setFixedSize(30, 30)
        self.btn_next.setStyleSheet("background: transparent; border: none; font-weight: bold;")
        self.btn_next.clicked.connect(self.find_next)
        
        self.btn_close = QPushButton("✕", self)
        self.btn_close.setFixedSize(30, 30)
        self.btn_close.setStyleSheet("background: transparent; border: none; font-weight: bold; color: #ff6666;")
        self.btn_close.clicked.connect(self.hide_bar)
        
        layout.addWidget(lbl_icon)
        layout.addWidget(self.input)
        layout.addWidget(self.btn_prev)
        layout.addWidget(self.btn_next)
        layout.addWidget(self.btn_close)
        
        self.hide() 
        
    def find_next(self):
        text = self.input.text()
        if text: self.webview.findText(text)
        
    def find_prev(self):
        text = self.input.text()
        if text: self.webview.findText(text, QWebEnginePage.FindFlag.FindBackward)
        
    def hide_bar(self):
        self.hide()
        self.webview.findText("") 

class MemoTreeWidget(QTreeWidget):
    def __init__(self, parent_app, parent=None):
        super().__init__(parent)
        self.parent_app = parent_app
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)

    def dropEvent(self, event):
        dragged_items = self.selectedItems()
        drop_target = self.itemAt(event.position().toPoint())
        
        target_data = drop_target.data(0, Qt.ItemDataRole.UserRole) if drop_target else None
        target_folder_id = None

        if target_data and target_data.startswith("memo_"):
            drop_target_parent = drop_target.parent()
            if drop_target_parent:
                p_data = drop_target_parent.data(0, Qt.ItemDataRole.UserRole)
                if p_data and p_data.startswith("folder_"):
                    target_folder_id = int(p_data.split("_")[1])
            if not target_folder_id:
                target_folder_id = 1
        elif target_data and target_data.startswith("folder_"):
            target_folder_id = int(target_data.split("_")[1])
        elif drop_target is None: 
            target_folder_id = "ROOT"

        if target_folder_id is None:
            event.ignore()
            return

        conn = get_db_connection()
        c = conn.cursor()
        moved = False

        for item in dragged_items:
            drag_data = item.data(0, Qt.ItemDataRole.UserRole)
            
            if drag_data and drag_data.startswith("memo_"):
                memo_id = int(drag_data.split("_")[1])
                t_id = 1 if target_folder_id == "ROOT" else target_folder_id
                c.execute("UPDATE memos SET folder_id = ? WHERE id = ?", (t_id, memo_id))
                moved = True
                
            elif drag_data and drag_data.startswith("folder_"):
                folder_id = int(drag_data.split("_")[1])
                if folder_id == 1:
                    continue 
                if target_folder_id == "ROOT":
                    c.execute("UPDATE folders SET parent_id = NULL WHERE id = ?", (folder_id,))
                    moved = True
                elif target_folder_id != folder_id:
                    c.execute("UPDATE folders SET parent_id = ? WHERE id = ?", (target_folder_id, folder_id))
                    moved = True

        if moved:
            conn.commit()
            event.accept()
            QTimer.singleShot(10, self.parent_app.load_tree)
        else:
            event.ignore()
            
        conn.close()

class FolderSelectDialog(QDialog):
    def __init__(self, parent=None, current_f_id=1):
        super().__init__(parent)
        self.setWindowTitle("📂 소속 폴더 지정")
        self.setWindowIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon))
        self.resize(350, 450)
        self.selected_id = current_f_id
        self.selected_name = "기본 폴더"

        layout = QVBoxLayout(self)
        self.tree = QTreeWidget(self)
        self.tree.setHeaderHidden(True)
        layout.addWidget(self.tree)

        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT id, name, parent_id FROM folders ORDER BY sort_order ASC, id ASC")
        folders = c.fetchall()
        conn.close()

        folder_dict = {}
        for f_id, name, p_id in folders:
            item = QTreeWidgetItem([f"📂 {name}"])
            item.setData(0, Qt.ItemDataRole.UserRole, f_id)
            folder_dict[f_id] = {'item': item, 'p_id': p_id}

        root_items = []
        for f_id, data in folder_dict.items():
            p_id = data['p_id']
            if p_id and p_id in folder_dict:
                folder_dict[p_id]['item'].addChild(data['item'])
            else:
                root_items.append(data['item'])

        self.tree.addTopLevelItems(root_items)
        self.tree.expandAll()

        def select_item(item):
            if item.data(0, Qt.ItemDataRole.UserRole) == self.selected_id:
                self.tree.setCurrentItem(item)
            for i in range(item.childCount()):
                select_item(item.child(i))

        for i in range(self.tree.topLevelItemCount()):
            select_item(self.tree.topLevelItem(i))

        btn_layout = QHBoxLayout()
        btn_ok = QPushButton("선택 완료", self)
        btn_ok.clicked.connect(self.on_ok)
        btn_cancel = QPushButton("취소", self)
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_ok)
        btn_layout.addWidget(btn_cancel)
        layout.addLayout(btn_layout)

    def on_ok(self):
        item = self.tree.currentItem()
        if item:
            self.selected_id = item.data(0, Qt.ItemDataRole.UserRole)
            self.selected_name = item.text(0).replace("📂 ", "")
        self.accept()

class HiddenConfigDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("🕵️ 숨겨진 시스템 관리 (중앙 통제 마스터 패널)")
        self.resize(550, 650)
        layout = QVBoxLayout(self)

        import socket
        self.physical_hostname = socket.gethostname()

        title = QLabel("🛠️ 중앙 권한 통제 및 Hostname 매핑", self)
        title.setStyleSheet("font-size: 16px; font-weight: bold; color: #ff9800;")
        layout.addWidget(title)

        lbl_physical = QLabel(f"🖥️ 현재 접속 중인 관리자 물리적 PC: <b>{self.physical_hostname}</b>", self)
        layout.addWidget(lbl_physical)

        # ----------------------------------------------------
        # 1. 사용자 풀 관리 영역
        # ----------------------------------------------------
        layout.addWidget(QLabel("<br><b>[ 1. 전체 사용자 풀 관리 ]</b>", self))
        self.user_list = QListWidget(self)
        self.user_list.setMaximumHeight(150)
        layout.addWidget(self.user_list)

        add_user_layout = QHBoxLayout()
        self.input_new_user = QLineEdit(self)
        self.input_new_user.setPlaceholderText("새로운 사용자 강제 추가")
        
        btn_add = QPushButton("➕ 사용자 추가", self)
        btn_add.clicked.connect(self.add_user)
        
        btn_del = QPushButton("🗑️ 삭제", self)
        btn_del.setStyleSheet("color: red;")
        btn_del.clicked.connect(self.del_user)
        
        add_user_layout.addWidget(self.input_new_user)
        add_user_layout.addWidget(btn_add)
        add_user_layout.addWidget(btn_del)
        layout.addLayout(add_user_layout)

        line1 = QFrame(self)
        line1.setFrameShape(QFrame.Shape.HLine)
        line1.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(line1)

        # ----------------------------------------------------
        # 2. Hostname ↔ 사용자 매핑(귀속) 수동 관리 영역
        # ----------------------------------------------------
        layout.addWidget(QLabel("<b>[ 2. Hostname(PC명) ↔ 사용자 귀속 매핑 현황 ]</b>", self))
        
        self.mapping_list = QListWidget(self)
        self.mapping_list.setMaximumHeight(150)
        self.mapping_list.itemClicked.connect(self.on_mapping_clicked)
        layout.addWidget(self.mapping_list)

        mapping_input_layout = QHBoxLayout()
        self.input_hostname = QLineEdit(self)
        self.input_hostname.setText(self.physical_hostname)
        self.input_hostname.setPlaceholderText("통제할 Hostname (예: DESKTOP-PC1)")
        mapping_input_layout.addWidget(self.input_hostname)
        
        btn_link = QPushButton("🔗 지정 PC에 귀속", self)
        btn_link.setStyleSheet("font-weight: bold; background-color: #4da6ff; color: white;")
        btn_link.clicked.connect(self.link_user)
        
        btn_unlink = QPushButton("🔓 귀속 해제", self)
        btn_unlink.clicked.connect(self.unlink_user)

        mapping_input_layout.addWidget(btn_link)
        mapping_input_layout.addWidget(btn_unlink)
        layout.addLayout(mapping_input_layout)

        line2 = QFrame(self)
        line2.setFrameShape(QFrame.Shape.HLine)
        line2.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(line2)

        # ----------------------------------------------------
        # 3. ⚡ 빠른 프로비저닝 (원샷 등록)
        # ----------------------------------------------------
        layout.addWidget(QLabel("<b>[ 3. ⚡ 빠른 프로비저닝 (계정 생성 + PC 귀속 동시 처리) ]</b>", self))
        
        quick_layout = QHBoxLayout()
        self.quick_host = QLineEdit(self)
        self.quick_host.setPlaceholderText("Hostname (예: USER-PC)")
        
        self.quick_user = QLineEdit(self)
        self.quick_user.setPlaceholderText("사용자 이름 (예: 김철수)")
        
        btn_quick = QPushButton("🚀 즉시 등록 및 매핑", self)
        btn_quick.setStyleSheet("font-weight: bold; background-color: #28a745; color: white;")
        btn_quick.clicked.connect(self.quick_provision)
        
        quick_layout.addWidget(self.quick_host)
        quick_layout.addWidget(self.quick_user)
        quick_layout.addWidget(btn_quick)
        layout.addLayout(quick_layout)

        self.load_data()

    def load_data(self):
        self.user_list.clear()
        self.mapping_list.clear()
        conn = get_db_connection()
        c = conn.cursor()

        c.execute("SELECT username FROM users ORDER BY id ASC")
        for r in c.fetchall():
            item = QListWidgetItem(f"👤 {r[0]}")
            item.setData(Qt.ItemDataRole.UserRole, r[0])
            self.user_list.addItem(item)
            
        # ★ 수정됨: % 기호 에러 방지를 위해 ? 파라미터 방식으로 우회!
        c.execute("SELECT `key`, value FROM settings WHERE `key` LIKE ?", ("pc_owner_%",))
        for r in c.fetchall():
            host = r[0].replace("pc_owner_", "")
            owner = r[1]
            flag = "🖥️ [내 PC] " if host == self.physical_hostname else "💻 [원격 PC] "
            item = QListWidgetItem(f"{flag}{host} ➔ {owner}")
            item.setData(Qt.ItemDataRole.UserRole, host)
            self.mapping_list.addItem(item)
        conn.close()

    def on_mapping_clicked(self, item):
        host = item.data(Qt.ItemDataRole.UserRole)
        if host: self.input_hostname.setText(host)

    def add_user(self):
        uname = self.input_new_user.text().strip()
        if not uname: return
        conn = get_db_connection()
        c = conn.cursor()
        try:
            c.execute("INSERT INTO users (username) VALUES (?)", (uname,))
            conn.commit()
            self.input_new_user.clear()
        except: pass
        conn.close()
        self.load_data()
        self.parent().load_users()

    def del_user(self):
        item = self.user_list.currentItem()
        if not item: return
        uname = item.data(Qt.ItemDataRole.UserRole)
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("DELETE FROM users WHERE username=?", (uname,))
        # ★ 수정됨: % 기호 에러 방지를 위해 ? 파라미터 방식으로 우회!
        c.execute("DELETE FROM settings WHERE value=? AND `key` LIKE ?", (uname, "pc_owner_%"))
        conn.commit()
        conn.close()
        self.load_data()
        self.parent().load_users()

    def link_user(self):
        host = self.input_hostname.text().strip()
        user_item = self.user_list.currentItem()
        if not host or not user_item:
            QMessageBox.warning(self, "선택 오류", "목록에서 사용자를 선택하고 Hostname을 입력해주세요.")
            return
            
        uname = user_item.data(Qt.ItemDataRole.UserRole)
        conn = get_db_connection()
        c = conn.cursor()
        
        # ★ 추가된 철벽: 이 사용자가 이미 다른 PC에 귀속되어 있는지 확인!
        c.execute("SELECT `key` FROM settings WHERE `key` LIKE ? AND value=?", ("pc_owner_%", uname))
        existing = c.fetchall()
        for r in existing:
            existing_host = r[0].replace("pc_owner_", "")
            if existing_host != host:
                QMessageBox.warning(self, "귀속 충돌", f"'{uname}' 님은 이미 '{existing_host}' PC에 귀속되어 있습니다.\n먼저 해당 PC의 귀속을 해제해야 다른 PC에 연결할 수 있습니다.")
                conn.close()
                return
                
        c.execute("INSERT OR REPLACE INTO settings (`key`, value) VALUES (?, ?)", (f"pc_owner_{host}", uname))
        conn.commit()
        conn.close()
        self.load_data()
        self.parent().load_users()

    def unlink_user(self):
        host = self.input_hostname.text().strip()
        if not host: return
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("DELETE FROM settings WHERE key=?", (f"pc_owner_{host}",))
        conn.commit()
        conn.close()
        self.load_data()
        self.parent().load_users()

    def quick_provision(self):
        host = self.quick_host.text().strip()
        uname = self.quick_user.text().strip()
        if not host or not uname:
            QMessageBox.warning(self, "입력 오류", "Hostname과 사용자 이름을 모두 입력해주세요.")
            return
            
        conn = get_db_connection()
        c = conn.cursor()
        
        # ★ 추가된 철벽: 이 사용자가 이미 다른 PC에 귀속되어 있는지 확인!
        c.execute("SELECT `key` FROM settings WHERE `key` LIKE ? AND value=?", ("pc_owner_%", uname))
        existing = c.fetchall()
        for r in existing:
            existing_host = r[0].replace("pc_owner_", "")
            if existing_host != host:
                QMessageBox.warning(self, "귀속 충돌", f"'{uname}' 님은 이미 '{existing_host}' PC에 귀속되어 있습니다.\n다른 이름을 사용하거나 기존 귀속을 해제하세요.")
                conn.close()
                return
        
        c.execute("SELECT id FROM users WHERE username=?", (uname,))
        if not c.fetchone():
            c.execute("INSERT INTO users (username) VALUES (?)", (uname,))
            
        c.execute("INSERT OR REPLACE INTO settings (`key`, value) VALUES (?, ?)", (f"pc_owner_{host}", uname))
        conn.commit()
        conn.close()
        
        self.quick_host.clear()
        self.quick_user.clear()
        QMessageBox.information(self, "완료", f"사용자 '{uname}' 생성 및 PC('{host}') 귀속 완료!")
        self.load_data()
        self.parent().load_users()

class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("⚙️ 시스템 환경 설정")
        
        try:
            base_path = sys._MEIPASS
        except Exception:
            base_path = os.path.abspath(".")
        icon_path = os.path.join(base_path, "assets", "icon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        else:
            self.setWindowIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon))
            
        self.resize(450, 600)
        self.initUI()
        self.load_users()

    def open_hidden_config(self):
        dialog = HiddenConfigDialog(self)
        dialog.setStyleSheet(self.styleSheet())
        dialog.exec()

    def initUI(self):
        layout = QVBoxLayout(self)
        
        user_label = QLabel("👤 사용자 풀 및 세션 관리", self)
        user_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(user_label)
        
        self.current_user_label = QLabel(self)
        self.current_user_label.setStyleSheet("font-size: 13px; padding: 5px; background-color: rgba(77,166,255,0.1); border-radius: 4px;")
        layout.addWidget(self.current_user_label)
        
        self.user_list_widget = QListWidget(self)
        self.user_list_widget.itemDoubleClicked.connect(self.switch_user)
        layout.addWidget(self.user_list_widget)
        
        btn_layout = QHBoxLayout()
        btn_switch = QPushButton("🔄 선택 세션 전환", self)
        btn_switch.clicked.connect(self.switch_user)
        btn_delete_user = QPushButton("🗑️ 사용자 삭제", self)
        btn_delete_user.setStyleSheet("color: #ff4d4d;")
        btn_delete_user.clicked.connect(self.delete_user)
        btn_layout.addWidget(btn_switch)
        btn_layout.addWidget(btn_delete_user)
        layout.addLayout(btn_layout)
        
        add_box = QHBoxLayout()
        self.new_user_input = QLineEdit(self)
        self.new_user_input.setPlaceholderText("새로운 사용자 이름 입력")
        btn_add = QPushButton("➕ 추가", self)
        btn_add.clicked.connect(self.add_user)
        add_box.addWidget(self.new_user_input)
        add_box.addWidget(btn_add)
        layout.addLayout(add_box)
        
        line1 = QFrame(self)
        line1.setFrameShape(QFrame.Shape.HLine)
        line1.setFrameShadow(QFrame.Shadow.Sunken)
        line1.setStyleSheet("margin: 10px 0px;")
        layout.addWidget(line1)
        
        theme_label = QLabel("🎨 화면 테마 및 디스플레이 설정", self)
        theme_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(theme_label)
        
        theme_layout = QHBoxLayout()
        self.theme_combo = QComboBox(self)
        self.theme_combo.addItems(["시스템 기본값 연동", "☀️ 라이트 모드 강제", "🌙 다크 모드 강제"])
        
        curr_theme = self.parent().current_theme_setting
        theme_map_inv = {"system": 0, "light": 1, "dark": 2}
        self.theme_combo.setCurrentIndex(theme_map_inv.get(curr_theme, 0))
        self.theme_combo.currentIndexChanged.connect(self.change_theme)
        
        theme_layout.addWidget(QLabel("UI 모드 고정: ", self))
        theme_layout.addWidget(self.theme_combo)
        theme_layout.addStretch()
        layout.addLayout(theme_layout)
        
        line2 = QFrame(self)
        line2.setFrameShape(QFrame.Shape.HLine)
        line2.setFrameShadow(QFrame.Shadow.Sunken)
        line2.setStyleSheet("margin: 10px 0px;")
        layout.addWidget(line2)
        
        sec_label = QLabel("🔒 보안 및 자동 잠금 설정", self)
        sec_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(sec_label)
        
        sec_info = QLabel("문서 잠금 해제 후 조작이 없을 때 다시 잠기기까지의 시간을 설정합니다.", self)
        sec_info.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(sec_info)
        
        sec_layout = QHBoxLayout()
        self.timeout_combo = QComboBox(self)
        self.timeout_combo.addItems(["1분", "3분", "5분", "10분", "30분", "60분"])
        self.timeout_combo.currentIndexChanged.connect(self.change_timeout)
        
        sec_layout.addWidget(QLabel("자동 잠금 타이머: ", self))
        sec_layout.addWidget(self.timeout_combo)
        sec_layout.addStretch()
        layout.addLayout(sec_layout)
        
        self.load_timeout_setting()
        
        line3 = QFrame(self)
        line3.setFrameShape(QFrame.Shape.HLine)
        line3.setFrameShadow(QFrame.Shadow.Sunken)
        line3.setStyleSheet("margin: 10px 0px;")
        layout.addWidget(line3)
        
        db_label = QLabel("💽 로컬 데이터베이스 위치 및 유지보수", self)
        db_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(db_label)
        
        # [★추가된 UI] 로컬 DB 경로 표시 및 변경 버튼
        if DB_TYPE == "sqlite":
            path_layout = QHBoxLayout()
            self.lbl_db_path = QLineEdit(self)
            self.lbl_db_path.setText(CURRENT_SQLITE_PATH)
            self.lbl_db_path.setReadOnly(True)
            self.lbl_db_path.setStyleSheet("color: gray; font-size: 11px;")
            
            btn_change_path = QPushButton("📂 위치 이동", self)
            btn_change_path.clicked.connect(self.change_sqlite_path)
            
            path_layout.addWidget(self.lbl_db_path)
            path_layout.addWidget(btn_change_path)
            layout.addLayout(path_layout)
        
        db_info = QLabel("삭제된 문서나 리비전의 빈 공간을 압축하여 성능을 최적화합니다.", self)
        db_info.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(db_info)
        
        btn_vacuum = QPushButton("🧹 DB 파일 최적화 (VACUUM / OPTIMIZE)", self)
        btn_vacuum.setMinimumHeight(35)
        btn_vacuum.clicked.connect(self.optimize_db)
        layout.addWidget(btn_vacuum)

    def change_theme(self):
        idx = self.theme_combo.currentIndex()
        theme_map = {0: "system", 1: "light", 2: "dark"}
        new_theme = theme_map[idx]
        
        # ★ 수정됨: 내 PC 테마만 로컬로 변경
        set_local_setting("theme_mode", new_theme)
        
        self.parent().current_theme_setting = new_theme
        self.parent()._last_is_dark = None
        self.parent().update_theme_styles()
        self.setStyleSheet(self.parent().styleSheet())

    def change_timeout(self):
        val = self.timeout_combo.currentText().replace("분", "")
        # ★ 수정됨: 내 PC 잠금 시간만 로컬로 변경
        set_local_setting("lock_timeout", val)

    def load_timeout_setting(self):
        self.timeout_combo.blockSignals(True)
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT value FROM settings WHERE key='lock_timeout'")
        row = c.fetchone()
        conn.close()
        
        val = row[0] if row else "10"
        idx = self.timeout_combo.findText(f"{val}분")
        if idx >= 0:
            self.timeout_combo.setCurrentIndex(idx)
        else:
            self.timeout_combo.setCurrentIndex(3)
        self.timeout_combo.blockSignals(False)

    def change_timeout(self):
        val = self.timeout_combo.currentText().replace("분", "")
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('lock_timeout', ?)", (val,))
        conn.commit()
        conn.close()

    def load_users(self):
        self.user_list_widget.clear()
        current = get_current_username()
        self.current_user_label.setText(f"현재 접속 계정: <font color='#4da6ff'><b>{current}</b></font> (더블클릭하여 전환)")
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT username FROM users ORDER BY id ASC")
        for row in c.fetchall():
            uname = row[0]
            display_name = f"✓ {uname}" if uname == current else f"  {uname}"
            item = QListWidgetItem(display_name)
            item.setData(Qt.ItemDataRole.UserRole, uname)
            if uname == current:
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            self.user_list_widget.addItem(item)
        conn.close()

    def switch_user(self):
        import socket
        hostname = socket.gethostname()
        conn = get_db_connection()
        c = conn.cursor()
        
        # 1. 내 PC 잠금 확인 (서버/로컬 공통)
        c.execute("SELECT value FROM settings WHERE `key`=?", (f"pc_owner_{hostname}",))
        if c.fetchone():
            QMessageBox.warning(self, "접근 거부", "이 PC는 관리 정책에 의해 특정 사용자에게 전속되어 있습니다.\n세션을 임의로 변경할 수 없습니다.")
            conn.close()
            return
            
        selected = self.user_list_widget.currentItem()
        if not selected: 
            conn.close()
            return
        uname = selected.data(Qt.ItemDataRole.UserRole)
        
        # 2. 타겟 계정이 남의 PC에 잠겨있는지 확인 (서버/로컬 공통)
        c.execute("SELECT `key` FROM settings WHERE `key` LIKE ? AND value=?", ("pc_owner_%", uname))
        owner_rows = c.fetchall()
        for r in owner_rows:
            locked_host = r[0].replace("pc_owner_", "")
            if locked_host != hostname:
                QMessageBox.warning(self, "접근 차단", f"'{uname}' 계정은 이미 다른 PC({locked_host})에서 전용으로 사용 중입니다.\n이 PC에서는 해당 세션으로 전환할 수 없습니다.")
                conn.close()
                return
                
        conn.close()
        set_local_setting("current_user", uname)
        self.load_users()
        QMessageBox.information(self, "세션 전환", f"'{uname}'(으)로 전환되었습니다.")

    def add_user(self):
        uname = self.new_user_input.text().strip()
        if not uname: return

        if uname == "hiddenconfig":
            self.new_user_input.clear()
            self.open_hidden_config()
            return

        import socket
        hostname = socket.gethostname()
        conn = get_db_connection()
        c = conn.cursor()
        
        # 서버/로컬 공통: 이미 이 PC가 귀속되어 있는지 확인
        c.execute("SELECT value FROM settings WHERE `key`=?", (f"pc_owner_{hostname}",))
        owner_row = c.fetchone()
        if owner_row and owner_row[0]:
            QMessageBox.warning(self, "추가 불가", f"이 PC는 이미 '{owner_row[0]}' 계정에 귀속되어 통제 중입니다.")
            conn.close()
            return

        try:
            c.execute("INSERT INTO users (username) VALUES (?)", (uname,))
            # 추가 즉시 이 PC를 해당 사용자에게 귀속시킴!
            c.execute("INSERT OR REPLACE INTO settings (`key`, value) VALUES (?, ?)", (f"pc_owner_{hostname}", uname))
            conn.commit()
            QMessageBox.information(self, "귀속 완료", f"사용자 '{uname}'(이)가 추가되었으며, 이 PC에 귀속되었습니다.")
        except Exception:
            QMessageBox.warning(self, "오류", "이미 존재하는 사용자 이름입니다.")
            
        self.new_user_input.clear()
        conn.close()
        self.load_users()

    def delete_user(self):
        import socket
        hostname = socket.gethostname()
        conn = get_db_connection()
        c = conn.cursor()
        
        # 서버/로컬 공통: 내 PC가 락 걸려있으면 삭제 기능 자체를 막음
        c.execute("SELECT value FROM settings WHERE `key`=?", (f"pc_owner_{hostname}",))
        if c.fetchone():
            QMessageBox.warning(self, "접근 거부", "PC 귀속 모드가 활성화된 상태에서는 사용자를 삭제할 수 없습니다.")
            conn.close()
            return

        selected = self.user_list_widget.currentItem()
        if not selected: 
            conn.close()
            return
        uname = selected.data(Qt.ItemDataRole.UserRole)
        
        if uname == get_current_username():
            QMessageBox.warning(self, "오류", "사용 중인 세션은 삭제할 수 없습니다.")
            conn.close()
            return
            
        # ★ 남의 PC에 잠겨있는 계정은 절대 삭제 불가!
        c.execute("SELECT `key` FROM settings WHERE `key` LIKE ? AND value=?", ("pc_owner_%", uname))
        existing = c.fetchall()
        for r in existing:
            existing_host = r[0].replace("pc_owner_", "")
            QMessageBox.warning(self, "삭제 불가", f"'{uname}' 계정은 현재 '{existing_host}' PC에서 사용 중(귀속)이므로 함부로 삭제할 수 없습니다.")
            conn.close()
            return
            
        c.execute("DELETE FROM users WHERE username=?", (uname,))
        c.execute("DELETE FROM settings WHERE value=? AND `key` LIKE ?", (uname, "pc_owner_%"))
            
        conn.commit()
        conn.close()
        self.load_users()
        
    def optimize_db(self):
        if DB_TYPE == "mysql":
            try:
                conn = get_db_connection()
                conn.execute("OPTIMIZE TABLE memos")
                conn.execute("OPTIMIZE TABLE memo_history")
                conn.execute("OPTIMIZE TABLE folders")
                conn.close()
                QMessageBox.information(self, "최적화 완료", "MySQL 서버 데이터베이스의 테이블 최적화가 성공적으로 완료되었습니다.")
            except Exception as e:
                QMessageBox.warning(self, "오류", f"DB 최적화 중 오류가 발생했습니다:\n{e}")
            return
            
        if not os.path.exists(DEFAULT_SQLITE_PATH): return
        old_size = os.path.getsize(DEFAULT_SQLITE_PATH)
        try:
            conn = get_db_connection()
            conn.isolation_level = None 
            conn.execute("VACUUM")
            conn.close()
            new_size = os.path.getsize(DEFAULT_SQLITE_PATH)
            reclaimed = old_size - new_size if old_size > new_size else 0
            QMessageBox.information(self, "최적화 완료", 
                                    f"DB 단편화 제거가 성공적으로 완료되었습니다.\n\n"
                                    f"• 이전 용량: {format_size(old_size)}\n"
                                    f"• 현재 용량: {format_size(new_size)}\n"
                                    f"• 확보된 공간: {format_size(reclaimed)}")
        except Exception as e:
            QMessageBox.warning(self, "오류", f"DB 최적화 중 오류가 발생했습니다:\n{e}")
    
    def change_sqlite_path(self):
        dir_path = QFileDialog.getExistingDirectory(self, "로컬 DB(memo.db)를 옮겨둘 새로운 폴더를 선택하세요")
        if not dir_path: return

        new_db_path = os.path.join(dir_path, "memo.db")
        new_db_path = os.path.normpath(new_db_path)
        current_path = os.path.normpath(CURRENT_SQLITE_PATH)

        if new_db_path == current_path:
            return

        should_move = True
        if os.path.exists(new_db_path):
            reply = QMessageBox.question(self, "중복 확인",
                f"선택한 폴더에 이미 memo.db 파일이 존재합니다.\n\n"
                f"• [Yes] 기존에 있던 파일을 그대로 불러오기\n"
                f"• [No] 현재 내 데이터를 저 위치로 덮어쓰기\n"
                f"• [Cancel] 취소",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel)

            if reply == QMessageBox.StandardButton.Cancel: return
            should_move = (reply == QMessageBox.StandardButton.No)

        if should_move:
            try:
                if os.path.exists(current_path):
                    shutil.copy2(current_path, new_db_path) # 안전하게 복사 먼저
            except Exception as e:
                QMessageBox.warning(self, "오류", f"DB 파일 이동 중 오류가 발생했습니다: {e}")
                return

        # config.json 업데이트
        cfg = {}
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
            except: pass

        cfg['sqlite_path'] = new_db_path
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, indent=4, ensure_ascii=False)

        # 복사 완료 시 원본 삭제
        if should_move and os.path.exists(current_path) and current_path != new_db_path:
            try: os.remove(current_path)
            except: pass

        QMessageBox.information(self, "완료", "데이터베이스 위치가 성공적으로 변경되었습니다.\n적용을 위해 프로그램이 재실행됩니다.")

        # ★ 전역 스위치 ON & 프로그램 재시작 (이게 핵심!)
        global IS_RESTARTING
        IS_RESTARTING = True

        subprocess.Popen([sys.executable] + sys.argv[1:])
        QApplication.quit()


class TrashDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_app = parent
        self.selected_trash_id = None
        self.setWindowTitle("🗑️ 휴지통 관리 및 미리보기")
        self.setWindowIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon))
        self.resize(950, 650)
        self.initUI()
        self.load_trash()

    def initUI(self):
        layout = QHBoxLayout(self)
        left_layout = QVBoxLayout()
        info_label = QLabel("휴지통 (7일 경과 시 자동 영구 삭제)", self)
        info_label.setStyleSheet("color: gray; font-weight: bold;")
        left_layout.addWidget(info_label)

        self.trash_list = QListWidget(self)
        self.trash_list.setMaximumWidth(350)
        self.trash_list.itemClicked.connect(self.preview_trash)
        left_layout.addWidget(self.trash_list)

        btn_layout = QHBoxLayout()
        self.btn_restore = QPushButton("♻️ 복원", self)
        self.btn_restore.setMinimumHeight(35)
        self.btn_restore.clicked.connect(self.restore_memo)

        self.btn_delete = QPushButton("❌ 영구 삭제", self)
        self.btn_delete.setMinimumHeight(35)
        self.btn_delete.setStyleSheet("color: red;")
        self.btn_delete.clicked.connect(self.delete_memo)

        btn_layout.addWidget(self.btn_restore)
        btn_layout.addWidget(self.btn_delete)
        left_layout.addLayout(btn_layout)

        self.btn_empty = QPushButton("🔥 휴지통 비우기", self)
        self.btn_empty.setMinimumHeight(35)
        self.btn_empty.setStyleSheet("background-color: #ff4d4d; color: white; font-weight: bold;")
        self.btn_empty.clicked.connect(self.empty_trash)
        left_layout.addWidget(self.btn_empty)

        right_layout = QVBoxLayout()
        self.preview_title = QLabel("미리보기 화면", self)
        self.preview_title.setStyleSheet("font-size: 16px; font-weight: bold;")
        
        self.viewer = QWebEngineView(self)
        self.viewer.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.viewer.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        self.viewer.setZoomFactor(self.parent_app.current_zoom_factor)
        
        view_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "assets", "viewer.html"))
        self.viewer.load(QUrl.fromLocalFile(view_path))
        self.viewer.loadFinished.connect(lambda ok: self.viewer.page().runJavaScript(self.parent_app.get_theme_js()) if ok else None)
        
        right_layout.addWidget(self.preview_title)
        right_layout.addWidget(self.viewer, 1)

        layout.addLayout(left_layout, 1)
        layout.addLayout(right_layout, 3)

    def load_trash(self):
        self.trash_list.clear()
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT id, title, deleted_at, revision, password FROM memos WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC")
        rows = c.fetchall()
        now = datetime.now()
        
        for row in rows:
            m_id, title, del_at_str, rev, pw = row[0], row[1], row[2], row[3], row[4]
            try:
                del_at = datetime.strptime(del_at_str, "%Y-%m-%d %H:%M:%S")
                days_passed = (now - del_at).days
                days_left = max(0, 7 - days_passed)
            except:
                days_left = 0
                
            icon_str = "🔒" if pw and m_id not in self.parent_app.unlocked_memos else "📄"
            display_text = f"{icon_str} {title} (r{rev})\n(남은기한: {days_left}일)"
            
            item = QListWidgetItem(display_text)
            item.setData(Qt.ItemDataRole.UserRole, m_id)
            if days_left <= 1: item.setForeground(Qt.GlobalColor.red)
            self.trash_list.addItem(item)
        conn.close()

        if self.trash_list.count() == 0:
            self.trash_list.addItem("휴지통이 비어있습니다.")
            self.trash_list.item(0).setFlags(Qt.ItemFlag.NoItemFlags)

    def preview_trash(self, item):
        m_id = item.data(Qt.ItemDataRole.UserRole)
        if not m_id: return
        self.selected_trash_id = m_id
        
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT title, content, revision, password FROM memos WHERE id=?", (m_id,))
        row = c.fetchone()
        conn.close()
        
        if row:
            title, content, rev, pw = row[0], row[1], row[2], row[3]
            if pw and m_id not in self.parent_app.unlocked_memos:
                self.preview_title.setText(f"🔒 [삭제됨] {title} (r{rev})")
                inject_html_safe(self.viewer, "<h3 style='text-align:center; color:#ff6666; margin-top:50px;'>🔒 이 문서는 보안 잠금 상태입니다.<br><br>내용을 보려면 복원 후 잠금을 해제해야 합니다.</h3>", theme_js=self.parent_app.get_theme_js())
            else:
                self.preview_title.setText(f"[삭제됨] {title} (r{rev})")
                inject_html_safe(self.viewer, deobfuscate_text(content), theme_js=self.parent_app.get_theme_js())

    def restore_memo(self):
        if not self.selected_trash_id: return
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT revision FROM memos WHERE id=?", (self.selected_trash_id,))
        rev = c.fetchone()[0] + 1
        c.execute("UPDATE memos SET deleted_at = NULL, revision=? WHERE id=?", (rev, self.selected_trash_id))
        conn.commit()
        conn.close()
        QMessageBox.information(self, "복원 완료", f"문서가 복원되었습니다. (새 리비전: r{rev})")
        self.load_trash()
        self.parent_app.refresh_all_views()
        self.parent_app.update_home_dashboard()

    def delete_memo(self):
        if not self.selected_trash_id: return
        reply = QMessageBox.question(self, '영구 삭제', '선택한 문서를 영구히 삭제하시겠습니까?\n(버전 이력도 삭제됩니다)',
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("DELETE FROM memo_history WHERE memo_id=?", (self.selected_trash_id,))
            c.execute("DELETE FROM memos WHERE id=?", (self.selected_trash_id,))
            conn.commit()
            conn.close()
            
            if self.selected_trash_id in self.parent_app.unlocked_memos:
                self.parent_app.unlocked_memos.remove(self.selected_trash_id)
                
            self.selected_trash_id = None
            inject_html_safe(self.viewer, "", theme_js=self.parent_app.get_theme_js())
            self.preview_title.setText("미리보기 화면")
            self.load_trash()
            self.parent_app.refresh_all_views()
            self.parent_app.update_home_dashboard()

    def empty_trash(self):
        if self.trash_list.count() == 0 or not self.trash_list.item(0).data(Qt.ItemDataRole.UserRole): return
        reply = QMessageBox.question(self, '휴지통 비우기', '휴지통에 있는 모든 문서를 영구히 삭제하시겠습니까?',
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("SELECT id FROM memos WHERE deleted_at IS NOT NULL")
            ids = [row[0] for row in c.fetchall()]
            if ids:
                placeholders = ','.join('?' * len(ids))
                c.execute(f"DELETE FROM memo_history WHERE memo_id IN ({placeholders})", ids)
                c.execute("DELETE FROM memos WHERE deleted_at IS NOT NULL")
                conn.commit()
                
                for m_id in ids:
                    if m_id in self.parent_app.unlocked_memos:
                        self.parent_app.unlocked_memos.remove(m_id)
            conn.close()
            self.selected_trash_id = None
            inject_html_safe(self.viewer, "", theme_js=self.parent_app.get_theme_js())
            self.preview_title.setText("미리보기 화면")
            self.load_trash()
            self.parent_app.refresh_all_views()
            self.parent_app.update_home_dashboard()

    def closeEvent(self, event):
        try:
            zf = self.viewer.zoomFactor()
            self.parent_app.current_zoom_factor = zf
            self.parent_app.viewer.setZoomFactor(zf)
            self.parent_app.browser.setZoomFactor(zf)
            if self.parent_app.popup_window: self.parent_app.popup_window.viewer.setZoomFactor(zf)
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('zoom_factor', ?)", (str(zf),))
            conn.commit()
            conn.close()
        except: pass
        super().closeEvent(event)

class HistoryDialog(QDialog):
    def __init__(self, memo_id, parent=None):
        super().__init__(parent)
        self.memo_id = memo_id
        self.parent_app = parent
        self.selected_history_id = None
        self.selected_history_rev = None
        self.setWindowTitle("문서 변경 이력 및 복구 (최근 10개 버전)")
        self.setWindowIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        self.resize(950, 650)
        self.initUI()
        self.load_history()

    def initUI(self):
        layout = QHBoxLayout(self)
        left_layout = QVBoxLayout()
        label = QLabel("저장된 과거 버전 (클릭하여 미리보기)", self)
        label.setStyleSheet("font-weight: bold;")
        
        self.history_list = QListWidget(self)
        self.history_list.itemClicked.connect(self.preview_history)
        self.history_list.setMaximumWidth(280)
        
        self.btn_restore = QPushButton("🔄 이 버전으로 복구하기", self)
        self.btn_restore.setMinimumHeight(40)
        self.btn_restore.setStyleSheet("background-color: #ff6666; color: white; font-weight: bold;")
        self.btn_restore.clicked.connect(self.restore_action)
        self.btn_restore.setEnabled(False)
        
        left_layout.addWidget(label)
        left_layout.addWidget(self.history_list)
        left_layout.addWidget(self.btn_restore)
        
        right_layout = QVBoxLayout()
        self.preview_title = QLabel("미리보기 화면", self)
        self.preview_title.setStyleSheet("font-size: 16px; font-weight: bold;")
        
        self.viewer = QWebEngineView(self)
        self.viewer.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.viewer.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        self.viewer.setZoomFactor(self.parent_app.current_zoom_factor)
        
        view_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "assets", "viewer.html"))
        self.viewer.load(QUrl.fromLocalFile(view_path))
        self.viewer.loadFinished.connect(lambda ok: self.viewer.page().runJavaScript(self.parent_app.get_theme_js()) if ok else None)
        
        right_layout.addWidget(self.preview_title)
        right_layout.addWidget(self.viewer, 1)
        layout.addLayout(left_layout, 1)
        layout.addLayout(right_layout, 3)

    def load_history(self):
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT history_id, saved_at, editor, revision FROM memo_history WHERE memo_id=? ORDER BY history_id DESC", (self.memo_id,))
        for row in c.fetchall():
            h_id, saved_at, editor, rev = row[0], row[1], row[2], row[3]
            editor_display = editor if editor else "알 수 없음"
            item = QListWidgetItem(f"[r{rev}] 🕒 {saved_at}\n👤 수정: {editor_display}")
            item.setData(Qt.ItemDataRole.UserRole, h_id)
            item.setData(Qt.ItemDataRole.UserRole + 1, rev) 
            self.history_list.addItem(item)
        conn.close()
        
        if self.history_list.count() == 0:
            self.history_list.addItem("과거 이력이 없습니다.")
            self.history_list.item(0).setFlags(Qt.ItemFlag.NoItemFlags)

    def preview_history(self, item):
        h_id = item.data(Qt.ItemDataRole.UserRole)
        rev_id = item.data(Qt.ItemDataRole.UserRole + 1)
        if not h_id: return
        self.selected_history_id = h_id
        self.selected_history_rev = rev_id
        self.btn_restore.setEnabled(True)
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT title, content, editor, revision FROM memo_history WHERE history_id=?", (h_id,))
        row = c.fetchone()
        conn.close()
        if row:
            title, content, editor, rev = row[0], row[1], row[2], row[3]
            self.preview_title.setText(f"[과거 버전 r{rev}] {title} (수정자: {editor})")
            inject_html_safe(self.viewer, deobfuscate_text(content), theme_js=self.parent_app.get_theme_js())

    def restore_action(self):
        if not self.selected_history_id: return
        reply = QMessageBox.question(self, '복구 확인', f'과거 r{self.selected_history_rev} 버전의 내용으로 문서를 덮어씌웁니다.\n진행하시겠습니까?', 
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.parent_app.restore_memo(self.memo_id, self.selected_history_id)
            self.accept()

    def closeEvent(self, event):
        try:
            zf = self.viewer.zoomFactor()
            self.parent_app.current_zoom_factor = zf
            self.parent_app.viewer.setZoomFactor(zf)
            self.parent_app.browser.setZoomFactor(zf)
            if self.parent_app.popup_window: self.parent_app.popup_window.viewer.setZoomFactor(zf)
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('zoom_factor', ?)", (str(zf),))
            conn.commit()
            conn.close()
        except: pass
        super().closeEvent(event)

class MemoPopupWindow(QWidget):
    def __init__(self, main_app, memo_id, title, tags, html_content, created_at, updated_at, author, last_editor, rev):
        super().__init__()
        self.main_app = main_app
        self.memo_id = memo_id
        
        try:
            base_path = sys._MEIPASS
        except Exception:
            base_path = os.path.abspath(".")
        icon_path = os.path.join(base_path, "assets", "icon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        else:
            self.setWindowIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon))
            
        self.setWindowTitle(f"팝업 뷰어 - {title}")
        self.resize(900, 700)
        self.setWindowFlags(Qt.WindowType.Window)
        self.html_content = html_content
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 10, 15, 0)
        layout.setSpacing(0)
        
        view_header_layout = QVBoxLayout()
        view_header_layout.setContentsMargins(0, 0, 0, 5) 
        view_header_layout.setSpacing(2) 
        
        self.view_title = QLabel(f"{title} <span style='font-size: 14px; color: gray; font-weight: normal;'>(ID: {self.memo_id} | r{rev})</span>", self)
        self.view_title.setStyleSheet("font-size: 22px; font-weight: bold; margin: 0px; padding: 0px;")
        
        self.view_authors = QLabel(f"✍️ 작성: {author}  |  📝 수정: {last_editor}", self)
        self.view_authors.setStyleSheet("color: #4da6ff; font-size: 13px; font-weight: bold; margin: 0px; padding: 0px;")
        
        self.view_dates = QLabel(f"작성일: {created_at}  |  수정일: {updated_at}", self)
        self.view_dates.setStyleSheet("color: gray; font-size: 12px; margin: 0px; padding: 0px;")

        self.tags_container = QWidget(self)
        self.tags_layout = QHBoxLayout(self.tags_container)
        self.tags_layout.setContentsMargins(0, 3, 0, 3) 
        self.tags_layout.setSpacing(5)
        self.tags_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        
        if tags.strip():
            for t in [x.strip() for x in tags.split(",") if x.strip()]:
                lbl = QLabel(f"#{t}", self)
                lbl.setStyleSheet("background-color: #ff9800; color: white; padding: 3px 7px; border-radius: 4px; font-weight: bold; font-size: 12px;")
                self.tags_layout.addWidget(lbl)
            self.tags_container.show()
        else:
            self.tags_container.hide()

        view_header_layout.addWidget(self.view_title)
        view_header_layout.addWidget(self.view_authors)
        view_header_layout.addWidget(self.view_dates)
        view_header_layout.addWidget(self.tags_container) 
        
        toolbar = QHBoxLayout()
        toolbar.setAlignment(Qt.AlignmentFlag.AlignRight)
        
        self.btn_ontop = QPushButton("📌 항상 위: OFF", self)
        self.btn_ontop.setCheckable(True)
        self.btn_ontop.clicked.connect(self.toggle_on_top)
        
        btn_search = QPushButton("🔍 본문 검색", self)
        btn_search.clicked.connect(self.toggle_search_bar)
        btn_history = QPushButton("📜 이력/복구", self)
        btn_history.clicked.connect(self.main_app.open_history_dialog)
        btn_export = QPushButton("📤 내보내기", self)
        btn_export.clicked.connect(self.main_app.export_to_worknb)
        btn_pdf = QPushButton("📄 PDF 저장", self)
        btn_pdf.clicked.connect(self.export_to_pdf)
        btn_edit = QPushButton("✏️ 수정", self)
        btn_edit.clicked.connect(self.trigger_edit)
        btn_delete = QPushButton("🗑️ 삭제", self)
        btn_delete.setStyleSheet("color: red;")
        btn_delete.clicked.connect(self.trigger_delete)
        
        toolbar.addWidget(self.btn_ontop)
        toolbar.addWidget(btn_search)
        toolbar.addWidget(btn_history)
        toolbar.addWidget(btn_export)
        toolbar.addWidget(btn_pdf)
        toolbar.addWidget(btn_edit)
        toolbar.addWidget(btn_delete)
        
        layout.addLayout(view_header_layout)
        layout.addLayout(toolbar)
        
        self.viewer = QWebEngineView(self)
        self.custom_page = CustomWebPage(self, self.viewer.page().profile(), self.viewer)
        self.viewer.setPage(self.custom_page)
        self.viewer.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.viewer.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        self.viewer.page().pdfPrintingFinished.connect(self.on_pdf_printed)
        
        if hasattr(self.main_app, 'current_zoom_factor'):
            self.viewer.setZoomFactor(self.main_app.current_zoom_factor)
        
        self.search_bar = SearchBarWidget(self.viewer, self)
        layout.addWidget(self.search_bar)
        layout.addWidget(self.viewer, 1)
        
        view_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "assets", "viewer.html"))
        self.viewer.load(QUrl.fromLocalFile(view_path))
        self.viewer.loadFinished.connect(self.on_load_finished)

    def toggle_on_top(self, checked):
        if checked:
            self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
            self.btn_ontop.setText("📌 항상 위: ON")
            self.btn_ontop.setStyleSheet("background-color: #4da6ff; color: white; font-weight: bold;")
        else:
            self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowStaysOnTopHint)
            self.btn_ontop.setText("📌 항상 위: OFF")
            self.btn_ontop.setStyleSheet("")
        self.show()
        
    def toggle_search_bar(self):
        if self.search_bar.isVisible(): self.search_bar.hide_bar()
        else: 
            self.search_bar.show()
            self.search_bar.input.setFocus()
            
    def on_load_finished(self, ok):
        if ok: inject_html_safe(self.viewer, self.html_content, margin="20px", theme_js=self.main_app.get_theme_js())

    def trigger_edit(self):
        self.main_app.current_memo_id = self.memo_id
        self.main_app.edit_memo()
        self.close()

    def trigger_delete(self):
        self.main_app.current_memo_id = self.memo_id
        self.main_app.delete_memo()
        self.close()

    def export_to_pdf(self):
        safe_title = "".join(c for c in self.windowTitle().replace("팝업 뷰어 - ", "") if c.isalnum() or c in (' ', '-', '_')).strip()
        if not safe_title: safe_title = "memo_document"
        filepath, _ = QFileDialog.getSaveFileName(self, "PDF로 내보내기", f"{safe_title}.pdf", "PDF Files (*.pdf)")
        if filepath:
            self.viewer.page().printToPdf(filepath)

    def on_pdf_printed(self, filepath, success):
        if success: QMessageBox.information(self, "저장 완료", f"저장 경로: {filepath}")
        else: QMessageBox.warning(self, "오류", "저장에 실패했습니다.")

    def closeEvent(self, event):
        try:
            zf = self.viewer.zoomFactor()
            self.main_app.current_zoom_factor = zf
            self.main_app.viewer.setZoomFactor(zf)
            self.main_app.browser.setZoomFactor(zf)
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('zoom_factor', ?)", (str(zf),))
            conn.commit()
            conn.close()
        except: pass
        super().closeEvent(event)

class ScratchpadWindow(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("📌 빠른 임시 메모")
        
        try:
            base_path = sys._MEIPASS
        except Exception:
            base_path = os.path.abspath(".")
        icon_path = os.path.join(base_path, "assets", "icon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        else:
            self.setWindowIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogContentsView))
            
        self.resize(380, 480)
        self.setWindowFlags(Qt.WindowType.Window) 
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        self.editor = QTextEdit(self)
        self.editor.setPlaceholderText("자유롭게 창 크기를 조절할 수 있습니다.\n\n여기에 임시 정보를 적어두세요. 자동 저장됩니다.")
        self.editor.textChanged.connect(self.save_data)
        layout.addWidget(self.editor)
        self.load_data()

    def load_data(self):
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT value FROM settings WHERE key='scratchpad'")
        row = c.fetchone()
        conn.close()
        if row:
            self.editor.blockSignals(True)
            self.editor.setPlainText(deobfuscate_text(row[0]))
            self.editor.blockSignals(False)

    def save_data(self):
        text = self.editor.toPlainText()
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('scratchpad', ?)", (obfuscate_text(text),))
        conn.commit()
        conn.close()

class WebEngineMemoApp(QMainWindow):
    def __init__(self):
        super().__init__()
        
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT value FROM settings WHERE key='theme_mode'")
        t_row = c.fetchone()
        self.current_theme_setting = t_row[0] if t_row else "system"
        
        c.execute("SELECT value FROM settings WHERE key='zoom_factor'")
        z_row = c.fetchone()
        try: self.current_zoom_factor = float(z_row[0]) if z_row else 1.0
        except: self.current_zoom_factor = 1.0
        conn.close()
        
        self._last_is_dark = None

        self.current_memo_id = None
        self.scratchpad_window = None
        self.popup_window = None 
        self.tree_initialized = False 
        
        self.unlocked_memos = set()
        
        self.last_activity_time = datetime.now()
        
        self.idle_timer = QTimer(self)
        self.idle_timer.timeout.connect(self.lock_all_memos)
        self.load_lock_timeout()
        
        self.countdown_timer = QTimer(self)
        self.countdown_timer.timeout.connect(self.update_countdown)
        self.countdown_timer.start(1000)
        
        self.zoom_sync_timer = QTimer(self)
        self.zoom_sync_timer.timeout.connect(self.sync_zoom_factors)
        self.zoom_sync_timer.start(300)
        
        QApplication.instance().installEventFilter(self)
        
        self.initUI()
        self.update_tag_combo()
        self.load_tree() 
        self.update_home_dashboard() 

        self.theme_timer = QTimer(self)
        self.theme_timer.timeout.connect(self.update_theme_styles)
        self.theme_timer.start(200)

        # F5 수동 새로고침 단축키 장착
        self.shortcut_refresh = QShortcut(QKeySequence("F5"), self)
        self.shortcut_refresh.activated.connect(self.manual_refresh)

        # 타 PC 변경점 10초 자동 동기화 타이머
        self.last_known_db_state = None
        self.sync_timer = QTimer(self)
        self.sync_timer.timeout.connect(self.check_remote_updates)
        self.sync_timer.start(10000)
        self.check_remote_updates()

    def manual_refresh(self):
        """F5를 눌렀을 때 수동으로 즉시 새로고침합니다."""
        self.refresh_all_views()
        self.update_home_dashboard()
        self.check_remote_updates()

    def check_remote_updates(self):
        """다른 PC에서 문서를 추가/수정했는지 가볍게 확인하고, 변경이 있으면 화면을 갱신합니다."""
        if self.right_stack.currentIndex() == 2:
            return

        try:
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("SELECT MAX(updated_at), COUNT(*) FROM memos")
            m_state = c.fetchone()
            c.execute("SELECT COUNT(*) FROM folders")
            f_state = c.fetchone()
            conn.close()
            
            current_state = f"{m_state}_{f_state}"
            
            if self.last_known_db_state is not None and self.last_known_db_state != current_state:
                self.refresh_all_views()
                self.update_home_dashboard()
                
            self.last_known_db_state = current_state
        except Exception:
            pass

    def get_theme_js(self):
        if self.current_theme_setting == 'light':
            mode_str = "light"
        elif self.current_theme_setting == 'dark':
            mode_str = "dark"
        else:
            palette = QApplication.palette()
            mode_str = "dark" if palette.color(palette.ColorGroup.Active, palette.ColorRole.Text).lightness() > 128 else "light"
            
        return f"""
            (function() {{
                var mode_str = '{mode_str}';
                var app_bg = (mode_str === 'dark') ? '#1e1e1e' : '#f0f2f5';
                var panel_bg = (mode_str === 'dark') ? '#2b2b2b' : '#ffffff';
                var text_color = (mode_str === 'dark') ? '#e0e0e0' : '#111111';
                var btn_bg = (mode_str === 'dark') ? '#333333' : '#e4e4e4';
                var border_color = (mode_str === 'dark') ? '#444444' : '#cccccc';
                var invert_val = (mode_str === 'dark') ? 'invert(0.9)' : 'none';

                if (typeof window.setThemeMode === 'function') {{
                    window.setThemeMode(mode_str, app_bg, panel_bg, text_color, btn_bg, border_color);
                    return;
                }}
                
                document.documentElement.setAttribute('data-theme', mode_str);
                document.body.style.setProperty('background-color', panel_bg, 'important');
                document.body.style.setProperty('color', text_color, 'important');
                
                var els = document.querySelectorAll('[style]');
                for(var k=0; k<els.length; k++) {{
                    var eBg = els[k].style.backgroundColor;
                    if(eBg) {{
                        var bgNorm = eBg.replace(/\\s/g, '').toLowerCase();
                        if (mode_str === 'light' && (bgNorm==='#2b2b2b' || bgNorm==='rgb(43,43,43)' || bgNorm==='#1e1e1e' || bgNorm==='#353535')) {{
                            els[k].style.backgroundColor = 'transparent';
                        }} else if (mode_str === 'dark' && (bgNorm==='#ffffff' || bgNorm==='rgb(255,255,255)' || bgNorm==='white' || bgNorm==='#f0f2f5')) {{
                            els[k].style.backgroundColor = 'transparent';
                        }}
                    }}
                    var eFg = els[k].style.color;
                    if(eFg) {{
                        var fgNorm = eFg.replace(/\\s/g, '').toLowerCase();
                        if (mode_str === 'light' && (fgNorm==='#e0e0e0' || fgNorm==='rgb(224,224,224)' || fgNorm==='#ffffff' || fgNorm==='white')) {{
                            els[k].style.color = '';
                        }} else if (mode_str === 'dark' && (fgNorm==='#000000' || fgNorm==='rgb(0,0,0)' || fgNorm==='#111111' || fgNorm==='black')) {{
                            els[k].style.color = '';
                        }}
                    }}
                }}
            }})();
        """

    def sync_zoom_factors(self):
        new_zoom = None
        
        if hasattr(self, 'viewer') and self.right_stack.currentIndex() == 1 and self.viewer.zoomFactor() != self.current_zoom_factor:
            new_zoom = self.viewer.zoomFactor()
        elif hasattr(self, 'browser') and self.right_stack.currentIndex() == 2 and self.browser.zoomFactor() != self.current_zoom_factor:
            new_zoom = self.browser.zoomFactor()
        elif self.popup_window and self.popup_window.isActiveWindow() and hasattr(self.popup_window, 'viewer') and self.popup_window.viewer.zoomFactor() != self.current_zoom_factor:
            new_zoom = self.popup_window.viewer.zoomFactor()
            
        if new_zoom is not None:
            self.current_zoom_factor = new_zoom
            
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('zoom_factor', ?)", (str(new_zoom),))
            conn.commit()
            conn.close()
            
            if hasattr(self, 'viewer') and self.viewer.zoomFactor() != new_zoom: self.viewer.setZoomFactor(new_zoom)
            if hasattr(self, 'browser') and self.browser.zoomFactor() != new_zoom: self.browser.setZoomFactor(new_zoom)
            if self.popup_window and hasattr(self.popup_window, 'viewer') and self.popup_window.viewer.zoomFactor() != new_zoom:
                self.popup_window.viewer.setZoomFactor(new_zoom)

    def load_lock_timeout(self):
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT value FROM settings WHERE key='lock_timeout'")
        row = c.fetchone()
        conn.close()
        
        try:
            mins = int(row[0]) if row and row[0] else 10
        except:
            mins = 10
            
        self.idle_timeout_ms = mins * 60 * 1000
        self.idle_timer.setInterval(self.idle_timeout_ms)
        self.last_activity_time = datetime.now() 
        self.idle_timer.start()

    def eventFilter(self, obj, event):
        ev_type = event.type()
        if ev_type in (QEvent.Type.KeyPress, QEvent.Type.MouseMove, QEvent.Type.MouseButtonPress, QEvent.Type.Wheel):
            self.last_activity_time = datetime.now()
            if hasattr(self, 'idle_timer'):
                self.idle_timer.start()
        
        if ev_type == QEvent.Type.Wheel:
            QTimer.singleShot(100, self.sync_zoom_factors)
            
        return super().eventFilter(obj, event)
        
    def update_countdown(self):
        if not hasattr(self, 'unlocked_memos') or not self.unlocked_memos:
            return
        if self.right_stack.currentIndex() != 1 or not self.current_memo_id:
            return
        if self.current_memo_id not in self.unlocked_memos:
            return

        elapsed = (datetime.now() - self.last_activity_time).total_seconds()
        remaining = int((self.idle_timeout_ms / 1000) - elapsed)
        if remaining < 0: remaining = 0

        mins, secs = divmod(remaining, 60)
        time_str = f"{mins:02d}:{secs:02d}"

        if hasattr(self, 'current_dates_text'):
            self.view_dates.setText(f"{self.current_dates_text}  |  ⏱️ 자동 잠금: {time_str}")
        
    def lock_all_memos(self):
        if not self.unlocked_memos: return
        self.unlocked_memos.clear()
        
        if self.popup_window and self.popup_window.memo_id:
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("SELECT password FROM memos WHERE id=?", (self.popup_window.memo_id,))
            row = c.fetchone()
            conn.close()
            if row and row[0]:
                self.popup_window.close()
        
        if self.current_memo_id and self.right_stack.currentIndex() == 1:
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("SELECT title, revision, password FROM memos WHERE id=?", (self.current_memo_id,))
            row = c.fetchone()
            conn.close()
            if row and row[2]: 
                self.show_locked_view(row[0], row[1])
                QMessageBox.warning(self, "보안 잠금 전환", "지정된 시간 동안 조작이 없어 보안을 위해 문서가 다시 잠겼습니다.")
                
        self.refresh_all_views()
        self.update_home_dashboard()

    def relock_memo_by_id(self, m_id):
        if m_id in self.unlocked_memos:
            self.unlocked_memos.remove(m_id)
            self.refresh_all_views()
            self.update_home_dashboard()
            
            if self.popup_window and self.popup_window.memo_id == m_id:
                self.popup_window.close()
                
            if self.current_memo_id == m_id and self.right_stack.currentIndex() == 1:
                conn = get_db_connection()
                c = conn.cursor()
                c.execute("SELECT title, revision FROM memos WHERE id=?", (m_id,))
                row = c.fetchone()
                conn.close()
                if row:
                    self.show_locked_view(row[0], row[1])

    def initUI(self):
        self.setWindowTitle('오프라인 HTML 메모장')
        
        try:
            base_path = sys._MEIPASS
        except Exception:
            base_path = os.path.abspath(".")
        icon_path = os.path.join(base_path, "assets", "icon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        else:
            self.setWindowIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon))
            
        self.resize(1200, 830)
        
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT value FROM settings WHERE key='window_geometry'")
        geom_row = c.fetchone()
        conn.close()
        
        if geom_row and geom_row[0]:
            try:
                self.restoreGeometry(QByteArray.fromHex(geom_row[0].encode('utf-8')))
            except Exception:
                pass

        self.central_widget = QWidget(self)
        self.setCentralWidget(self.central_widget)
        self.main_layout = QHBoxLayout(self.central_widget)

        self.left_widget = QWidget(self)
        self.left_layout = QVBoxLayout(self.left_widget)
        
        self.top_btn_layout = QGridLayout()
        self.top_btn_layout.setSpacing(5)
        
        btn_home = QPushButton("🏠 홈 화면", self)
        btn_home.setMinimumHeight(40)
        btn_home.clicked.connect(self.go_home)
        
        btn_new = QPushButton("📝 새 문서", self)
        btn_new.setMinimumHeight(40)
        btn_new.clicked.connect(self.new_memo)
        
        btn_import = QPushButton("📥 문서 가져오기 (.worknb)", self)
        btn_import.setMinimumHeight(35)
        btn_import.clicked.connect(self.import_from_worknb)
        
        self.top_btn_layout.addWidget(btn_home, 0, 0)
        self.top_btn_layout.addWidget(btn_new, 0, 1)
        self.top_btn_layout.addWidget(btn_import, 1, 0, 1, 2)
        
        self.search_input = QLineEdit(self)
        self.search_input.setPlaceholderText("🔍 제목, 태그, 본문 검색...")
        self.search_input.textChanged.connect(self.load_tree)
        
        self.tag_filter_combo = QComboBox(self)
        self.tag_filter_combo.currentIndexChanged.connect(self.load_tree)
        
        self.memo_tree = MemoTreeWidget(self, self)
        self.memo_tree.setHeaderHidden(True)
        self.memo_tree.setIndentation(12)
        self.memo_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.memo_tree.customContextMenuRequested.connect(self.show_tree_context_menu)
        self.memo_tree.itemClicked.connect(self.on_tree_item_clicked)
        
        btn_scratchpad = QPushButton("📌 빠른 임시 메모 열기", self)
        btn_scratchpad.setMinimumHeight(40)
        btn_scratchpad.setStyleSheet("font-weight: bold;")
        btn_scratchpad.clicked.connect(self.open_scratchpad)

        self.bottom_menu_layout = QGridLayout()
        self.bottom_menu_layout.setSpacing(5)
        
        btn_settings = QPushButton("⚙️ 설정", self)
        btn_settings.clicked.connect(self.open_settings)
        btn_trash = QPushButton("🗑️ 휴지통", self)
        btn_trash.clicked.connect(self.open_trash_dialog)
        btn_about = QPushButton("ℹ️ 정보", self)
        btn_about.clicked.connect(self.show_about)
        btn_help = QPushButton("❓ 도움말", self)
        btn_help.clicked.connect(self.show_help)
        
        self.bottom_menu_layout.addWidget(btn_settings, 0, 0)
        self.bottom_menu_layout.addWidget(btn_trash, 0, 1)
        self.bottom_menu_layout.addWidget(btn_about, 1, 0)
        self.bottom_menu_layout.addWidget(btn_help, 1, 1)

        self.left_layout.addLayout(self.top_btn_layout)
        self.left_layout.addWidget(self.search_input)
        self.left_layout.addWidget(self.tag_filter_combo)
        self.left_layout.addWidget(self.memo_tree, 1) 
        self.left_layout.addWidget(btn_scratchpad) 
        self.left_layout.addLayout(self.bottom_menu_layout)
        self.left_widget.setMinimumWidth(250) 

        self.right_stack = QStackedWidget(self)

        self.home_widget = QWidget(self)
        self.home_layout = QVBoxLayout(self.home_widget)
        self.home_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.home_layout.setContentsMargins(40, 40, 40, 40)
        self.home_layout.setSpacing(20)

        self.home_title = QLabel("🏠 메인 대시보드", self)
        self.home_title.setStyleSheet("font-size: 26px; font-weight: bold;")
        self.home_layout.addWidget(self.home_title)
        
        self.current_session_label = QLabel(self)
        self.current_session_label.setStyleSheet("font-size: 15px; color: gray; margin-bottom: 5px;")
        self.home_layout.addWidget(self.current_session_label)

        self.stats_label = QLabel(self)
        self.home_layout.addWidget(self.stats_label)

        recent_title = QLabel("🕒 최근 업데이트 된 문서", self)
        recent_title.setStyleSheet("font-size: 18px; font-weight: bold; margin-top: 15px;")
        self.home_layout.addWidget(recent_title)

        self.recent_list = QListWidget(self)
        self.recent_list.setMaximumHeight(350) 
        self.recent_list.itemClicked.connect(self.load_memo_content) 
        self.home_layout.addWidget(self.recent_list)

        btn_quick_new = QPushButton("📝 새로운 메모 작성하기", self)
        btn_quick_new.setMinimumHeight(50)
        btn_quick_new.setStyleSheet("font-size: 16px; font-weight: bold;")
        btn_quick_new.clicked.connect(self.new_memo)
        self.home_layout.addWidget(btn_quick_new)
        self.home_layout.addStretch()
        
        self.right_stack.addWidget(self.home_widget) 

        self.view_widget = QWidget(self)
        self.view_layout = QVBoxLayout(self.view_widget)
        self.view_layout.setSpacing(0)
        self.view_layout.setContentsMargins(15, 10, 15, 0)
        
        self.view_header_layout = QVBoxLayout()
        self.view_header_layout.setContentsMargins(0, 0, 0, 5) 
        self.view_header_layout.setSpacing(2) 
        
        self.view_title = QLabel(self)
        self.view_title.setStyleSheet("font-size: 22px; font-weight: bold; margin: 0px; padding: 0px;")
        self.view_authors = QLabel(self)
        self.view_authors.setStyleSheet("color: #4da6ff; font-size: 13px; font-weight: bold; margin: 0px; padding: 0px;")
        self.view_dates = QLabel(self)
        self.view_dates.setStyleSheet("color: gray; font-size: 12px; margin: 0px; padding: 0px;")

        self.tags_container = QWidget(self)
        self.tags_layout = QHBoxLayout(self.tags_container)
        self.tags_layout.setContentsMargins(0, 3, 0, 3) 
        self.tags_layout.setSpacing(5)
        self.tags_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.tags_container.hide()

        self.view_header_layout.addWidget(self.view_title)
        self.view_header_layout.addWidget(self.view_authors)
        self.view_header_layout.addWidget(self.view_dates)
        self.view_header_layout.addWidget(self.tags_container) 
        
        self.view_toolbar_layout = QHBoxLayout()
        self.view_toolbar_layout.setAlignment(Qt.AlignmentFlag.AlignRight)
        
        self.view_btn_unlock = QPushButton("🔓 잠금 해제", self)
        self.view_btn_unlock.setStyleSheet("background-color: #ff9800; color: white; font-weight: bold;")
        self.view_btn_unlock.clicked.connect(self.prompt_unlock)
        
        self.view_btn_search = QPushButton("🔍 본문 검색", self)
        self.view_btn_search.clicked.connect(self.toggle_search_bar)
        self.view_btn_history = QPushButton("📜 이력/복구", self)
        self.view_btn_history.clicked.connect(self.open_history_dialog)
        self.view_btn_popup = QPushButton("🗔 팝업 보기", self)
        self.view_btn_popup.clicked.connect(self.open_popup_viewer)
        self.view_btn_export = QPushButton("📤 내보내기", self)
        self.view_btn_export.clicked.connect(self.export_to_worknb)
        self.view_btn_pdf = QPushButton("📄 PDF 저장", self)
        self.view_btn_pdf.clicked.connect(self.export_to_pdf)
        self.view_btn_edit = QPushButton("✏️ 수정", self)
        self.view_btn_edit.clicked.connect(self.edit_memo)
        self.view_btn_delete = QPushButton("🗑️ 삭제", self)
        self.view_btn_delete.setStyleSheet("color: red;")
        self.view_btn_delete.clicked.connect(self.delete_memo) 
        
        self.view_toolbar_layout.addWidget(self.view_btn_unlock)
        self.view_toolbar_layout.addWidget(self.view_btn_search)
        self.view_toolbar_layout.addWidget(self.view_btn_history)
        self.view_toolbar_layout.addWidget(self.view_btn_popup)
        self.view_toolbar_layout.addWidget(self.view_btn_export)
        self.view_toolbar_layout.addWidget(self.view_btn_pdf)
        self.view_toolbar_layout.addWidget(self.view_btn_edit)
        self.view_toolbar_layout.addWidget(self.view_btn_delete)

        self.viewer = QWebEngineView(self)
        
        # 커스텀 라우터 장착
        self.custom_page = CustomWebPage(self, self.viewer.page().profile(), self.viewer)
        self.viewer.setPage(self.custom_page)
        
        self.viewer.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.viewer.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        self.viewer.page().pdfPrintingFinished.connect(self.on_pdf_printed)
        
        self.viewer.setZoomFactor(self.current_zoom_factor)
        
        self.search_bar = SearchBarWidget(self.viewer, self)
        
        view_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "assets", "viewer.html"))
        self.viewer.load(QUrl.fromLocalFile(view_path))
        self.viewer.loadFinished.connect(lambda ok: self.viewer.page().runJavaScript(self.get_theme_js()) if ok else None)
        
        self.view_layout.addLayout(self.view_header_layout)
        self.view_layout.addLayout(self.view_toolbar_layout) 
        self.view_layout.addWidget(self.search_bar)         
        self.view_layout.addWidget(self.viewer, 1)
        self.right_stack.addWidget(self.view_widget) 

        self.edit_widget = QWidget(self)
        self.edit_layout = QVBoxLayout(self.edit_widget)
        
        self.edit_meta_layout = QHBoxLayout()
        self.current_edit_folder_id = 1
        self.btn_folder_select = QPushButton("📂 소속 폴더 지정: 기본 폴더", self)
        self.btn_folder_select.setStyleSheet("text-align: left; padding: 5px;")
        self.btn_folder_select.clicked.connect(self.open_folder_select_dialog)
        
        self.edit_meta_layout.addWidget(self.btn_folder_select, 1)
        
        self.title_input = QLineEdit(self)
        self.title_input.setPlaceholderText("제목을 입력하세요")
        self.tags_input = QLineEdit(self)
        self.tags_input.setPlaceholderText("🏷️ 태그 입력 (쉼표로 구분. 예: 서버, 네트워크)")
        
        self.browser = QWebEngineView(self)
        self.browser.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        
        self.browser.setZoomFactor(self.current_zoom_factor)
        
        self.channel = QWebChannel(self)
        self.bridge = ImageBridge(self)
        self.channel.registerObject("pyObj", self.bridge)
        self.browser.page().setWebChannel(self.channel)
        html_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "assets", "editor.html"))
        self.browser.load(QUrl.fromLocalFile(html_path))
        
        self.browser.loadFinished.connect(lambda ok: self.browser.page().runJavaScript(self.get_theme_js()) if ok else None)

        self.edit_bottom_layout = QHBoxLayout()
        btn_save = QPushButton("💾 저장하기", self)
        btn_save.setMinimumHeight(35)
        btn_save.clicked.connect(self.save_memo_request)
        btn_cancel = QPushButton("❌ 취소", self)
        btn_cancel.clicked.connect(self.cancel_edit)
        self.edit_bottom_layout.addStretch()
        self.edit_bottom_layout.addWidget(btn_cancel)
        self.edit_bottom_layout.addWidget(btn_save)

        self.edit_layout.addLayout(self.edit_meta_layout) 
        self.edit_layout.addWidget(self.title_input)
        self.edit_layout.addWidget(self.tags_input)
        self.edit_layout.addWidget(self.browser)
        self.edit_layout.addLayout(self.edit_bottom_layout)
        self.right_stack.addWidget(self.edit_widget)      

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.main_splitter.addWidget(self.left_widget)
        self.main_splitter.addWidget(self.right_stack)
        
        # ★ 추가: DB에서 스플리터 상태를 불러와서 복원
        try:
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("SELECT value FROM settings WHERE `key`='splitter_state'")
            sp_row = c.fetchone()
            conn.close()
            
            if sp_row and sp_row[0]:
                self.main_splitter.restoreState(QByteArray.fromHex(sp_row[0].encode('utf-8')))
            else:
                self.main_splitter.setSizes([300, 900]) # 저장된 게 없으면 기본값
        except Exception:
            self.main_splitter.setSizes([300, 900])
            
        self.main_layout.addWidget(self.main_splitter)
        self.right_stack.setCurrentIndex(0) 

        self.update_theme_styles() 

    def import_from_worknb(self):
        filepath, _ = QFileDialog.getOpenFileName(self, ".worknb 파일 불러오기", "", "Work Notebook (*.worknb)")
        if filepath:
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    secure_data = f.read()
                    
                json_str = deobfuscate_text(secure_data)
                data = json.loads(json_str)
                
                items = data if isinstance(data, list) else [data]
                
                conn = get_db_connection()
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                current_user = get_current_username()
                
                imported_memos = 0
                imported_folders = 0
                last_id = None
                
                for item in items:
                    item_type = item.get("type", "memo")
                    folder_path_list = item.get("folder_path", [])
                    target_folder_id = ensure_folder_path_exists(conn, folder_path_list)
                    
                    if item_type == "folder":
                        color = item.get("color", "")
                        if color:
                            c = conn.cursor()
                            c.execute("UPDATE folders SET color=? WHERE id=?", (color, target_folder_id))
                        imported_folders += 1
                    else:
                        title = item.get("title", "가져온 문서")
                        html_content = item.get("content", "")
                        tags = item.get("tags", "")
                        raw_password = item.get("password", "")
                        
                        enc_password = obfuscate_text(raw_password) if raw_password else ""
                        enc_content = obfuscate_text(html_content)
                        
                        c = conn.cursor()
                        c.execute("""INSERT INTO memos (title, content, tags, created_at, updated_at, author, last_editor, revision, folder_id, password) 
                                     VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""", 
                                  (title, enc_content, tags, now_str, now_str, current_user, current_user, target_folder_id, enc_password))
                        last_id = c.lastrowid
                        imported_memos += 1
                
                conn.commit()
                conn.close()
                
                self.refresh_all_views()
                self.update_home_dashboard()
                
                if imported_memos == 1 and imported_folders == 0 and last_id:
                    pseudo_item = QListWidgetItem()
                    pseudo_item.setData(Qt.ItemDataRole.UserRole, last_id)
                    self.load_memo_content(pseudo_item)
                    QMessageBox.information(self, "불러오기 완료", f"'{title}' 문서가 성공적으로 추가되었습니다.")
                else:
                    msg = f"내보내기 데이터를 성공적으로 불러왔습니다.\n\n- 복원된 폴더: {imported_folders}개\n- 복원된 문서: {imported_memos}개"
                    QMessageBox.information(self, "불러오기 완료", msg)
                    self.go_home()
                    
            except Exception as e:
                QMessageBox.warning(self, "오류", "파일을 읽거나 해독하는 중 오류가 발생했습니다.\n올바른 .worknb 파일인지 확인해주세요.")

    def export_folder_to_worknb(self, f_id):
        conn = get_db_connection()
        c = conn.cursor()
        
        c.execute("SELECT name FROM folders WHERE id=?", (f_id,))
        f_row = c.fetchone()
        if not f_row:
            conn.close()
            return
        folder_name = f_row[0]
        
        target_folders = [f_id]
        def get_subs(p_id):
            c.execute("SELECT id FROM folders WHERE parent_id=?", (p_id,))
            for r in c.fetchall():
                target_folders.append(r[0])
                get_subs(r[0])
        get_subs(f_id)
        
        export_list = []
        
        placeholders = ','.join('?' * len(target_folders))
        c.execute(f"SELECT id, color FROM folders WHERE id IN ({placeholders})", target_folders)
        for r_fid, r_color in c.fetchall():
            f_path = get_folder_path_list(conn, r_fid)
            export_list.append({
                "type": "folder",
                "folder_path": f_path,
                "color": r_color if r_color else ""
            })
        
        c.execute(f"SELECT id, title, content, tags, folder_id, password FROM memos WHERE folder_id IN ({placeholders}) AND deleted_at IS NULL", target_folders)
        rows = c.fetchall()
        
        for row in rows:
            m_id, title, enc_content, tags, m_folder_id, enc_password = row
            html_content = deobfuscate_text(enc_content) if enc_content else ""
            raw_password = deobfuscate_text(enc_password) if enc_password else ""
            f_path_list = get_folder_path_list(conn, m_folder_id)
            
            export_list.append({
                "type": "memo",
                "title": title,
                "content": html_content,
                "tags": tags if tags else "",
                "folder_path": f_path_list,
                "password": raw_password
            })
        conn.close()
        
        json_str = json.dumps(export_list, ensure_ascii=False)
        secure_data = obfuscate_text(json_str) 
        
        safe_title = folder_name
        for ch in ['\\', '/', ':', '*', '?', '"', '<', '>', '|']:
            safe_title = safe_title.replace(ch, '')
        if not safe_title.strip(): safe_title = "folder_export"
        
        filepath, _ = QFileDialog.getSaveFileName(self, "폴더 내보내기", f"{safe_title}_백업.worknb", "Work Notebook (*.worknb)")
        if filepath:
            try:
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(secure_data)
                QMessageBox.information(self, "내보내기 완료", f"총 {len(rows)}개의 문서와 {len(target_folders)}개의 폴더가 구조 그대로 성공적으로 저장되었습니다.\n{filepath}")
            except Exception as e:
                QMessageBox.warning(self, "오류", f"저장 중 오류가 발생했습니다: {e}")

    def export_to_worknb(self):
        if not self.current_memo_id: return
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT title, content, tags, folder_id, password FROM memos WHERE id=?", (self.current_memo_id,))
        row = c.fetchone()
        
        if row:
            title, enc_content, tags, folder_id, enc_password = row
            html_content = deobfuscate_text(enc_content)
            raw_password = deobfuscate_text(enc_password) if enc_password else ""
            
            f_path_list = get_folder_path_list(conn, folder_id)
            
            export_data = {
                "type": "memo",
                "title": title,
                "content": html_content,
                "tags": tags if tags else "",
                "folder_path": f_path_list,
                "password": raw_password
            }
            conn.close()
            
            json_str = json.dumps(export_data, ensure_ascii=False)
            secure_data = obfuscate_text(json_str) 
            
            safe_title = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_')).strip()
            if not safe_title: safe_title = "memo"
            
            filepath, _ = QFileDialog.getSaveFileName(self, ".worknb 형식으로 내보내기", f"{safe_title}.worknb", "Work Notebook (*.worknb)")
            if filepath:
                try:
                    with open(filepath, 'w', encoding='utf-8') as f:
                        f.write(secure_data)
                    QMessageBox.information(self, "내보내기 완료", f"성공적으로 저장되었습니다.\n{filepath}")
                except Exception as e:
                    QMessageBox.warning(self, "오류", f"저장 중 오류가 발생했습니다: {e}")
        else:
            conn.close()

    def toggle_search_bar(self):
        if self.search_bar.isVisible():
            self.search_bar.hide_bar()
        else:
            self.search_bar.show()
            self.search_bar.input.setFocus()

    def go_home(self):
        self.current_memo_id = None
        self.memo_tree.clearSelection()
        self.update_home_dashboard()    
        self.right_stack.setCurrentIndex(0)

    def open_scratchpad(self):
        if not hasattr(self, 'scratchpad_window') or self.scratchpad_window is None or not self.scratchpad_window.isVisible():
            self.scratchpad_window = ScratchpadWindow()
            self.scratchpad_window.setStyleSheet(self.styleSheet())
            self.scratchpad_window.show()
        else:
            self.scratchpad_window.activateWindow()

    def refresh_all_views(self):
        self.update_tag_combo()
        self.load_tree()

    def reorder_folder(self, f_id, direction):
        if f_id == 1:
            QMessageBox.warning(self, "이동 불가", "'기본 폴더'는 최상단에 고정됩니다.")
            return

        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT parent_id FROM folders WHERE id=?", (f_id,))
        row = c.fetchone()
        if not row:
            conn.close()
            return
            
        p_id = row[0]
        if p_id is None:
            c.execute("SELECT id FROM folders WHERE parent_id IS NULL AND id != 1 ORDER BY sort_order ASC, id ASC")
            siblings = [1] + [r[0] for r in c.fetchall()] 
        else:
            c.execute("SELECT id FROM folders WHERE parent_id=? ORDER BY sort_order ASC, id ASC", (p_id,))
            siblings = [r[0] for r in c.fetchall()]
            
        if f_id not in siblings:
            conn.close()
            return
            
        idx = siblings.index(f_id)
        
        if direction == "up" and idx > 0:
            if p_id is None and idx == 1:
                pass 
            else:
                siblings[idx], siblings[idx-1] = siblings[idx-1], siblings[idx]
        elif direction == "down" and idx < len(siblings) - 1:
            siblings[idx], siblings[idx+1] = siblings[idx+1], siblings[idx]
            
        for i, s_id in enumerate(siblings):
            c.execute("UPDATE folders SET sort_order=? WHERE id=?", (i, s_id))
            
        conn.commit()
        conn.close()
        self.refresh_all_views()

    def move_folder_to_root(self, f_id):
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("UPDATE folders SET parent_id = NULL WHERE id = ?", (f_id,))
        
        c.execute("SELECT MAX(sort_order) FROM folders WHERE parent_id IS NULL")
        max_sort = c.fetchone()[0]
        new_sort = (max_sort + 1) if max_sort is not None else 0
        c.execute("UPDATE folders SET sort_order = ? WHERE id = ?", (new_sort, f_id))
        
        conn.commit()
        conn.close()
        self.refresh_all_views()

    def show_tree_context_menu(self, pos):
        item = self.memo_tree.itemAt(pos)
        menu = QMenu(self)

        if not item: 
            action_add_root = menu.addAction("➕ 새 루트 폴더 만들기")
            action = menu.exec(self.memo_tree.viewport().mapToGlobal(pos))
            if action == action_add_root:
                self.add_root_folder()
        else:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data and data.startswith("folder_"):
                f_id = int(data.split("_")[1])
                action_add_sub = menu.addAction("📂 하위 폴더 추가")
                action_ren = menu.addAction("✏️ 이름 변경")
                action_col = menu.addAction("🎨 폴더 색상 변경")
                action_export = menu.addAction("📤 이 폴더 내보내기 (.worknb)")
                
                action_to_root = None
                if f_id != 1 and item.parent() is not None:
                    menu.addSeparator()
                    action_to_root = menu.addAction("🚀 최상위(루트) 폴더로 빼기")
                    
                menu.addSeparator() 
                action_up = menu.addAction("⬆️ 위로 이동")
                action_down = menu.addAction("⬇️ 아래로 이동")
                menu.addSeparator() 
                action_del = menu.addAction("🗑️ 폴더 삭제")
                
                action = menu.exec(self.memo_tree.viewport().mapToGlobal(pos))
                
                if action == action_add_sub:
                    self.add_sub_folder(f_id)
                elif action == action_ren:
                    self.rename_folder_by_id(f_id)
                elif action == action_col:
                    self.change_folder_color_by_id(f_id)
                elif action == action_export:
                    self.export_folder_to_worknb(f_id)
                elif action_to_root and action == action_to_root:
                    self.move_folder_to_root(f_id)
                elif action == action_up:
                    self.reorder_folder(f_id, "up")
                elif action == action_down:
                    self.reorder_folder(f_id, "down")
                elif action == action_del:
                    self.delete_folder_by_id(f_id)
                    
            elif data and data.startswith("memo_"):
                m_id = int(data.split("_")[1])
                
                conn = get_db_connection()
                c = conn.cursor()
                c.execute("SELECT password FROM memos WHERE id=?", (m_id,))
                pw_row = c.fetchone()
                conn.close()
                has_pw = bool(pw_row and pw_row[0])

                action_ren_memo = menu.addAction("✏️ 문서 이름 변경")
                
                action_relock = None
                if has_pw: 
                    action_pw = menu.addAction("🔓 비밀번호 해제 (영구)")
                    if m_id in self.unlocked_memos:
                        action_relock = menu.addAction("🔒 바로 잠금")
                else: 
                    action_pw = menu.addAction("🔒 비밀번호 설정")
                    
                action_del_memo = menu.addAction("🗑️ 이 문서 휴지통으로")
                
                action = menu.exec(self.memo_tree.viewport().mapToGlobal(pos))
                
                if action == action_ren_memo:
                    self.rename_memo_by_id(m_id)
                elif action == action_pw:
                    if has_pw: self.remove_memo_password(m_id)
                    else: self.set_memo_password(m_id)
                elif action_relock and action == action_relock:
                    self.relock_memo_by_id(m_id)
                elif action == action_del_memo:
                    self.current_memo_id = m_id
                    self.delete_memo()

    def set_memo_password(self, m_id):
        self.browser.clearFocus()
        self.viewer.clearFocus()
        self.setFocus()
        
        pwd, ok = QInputDialog.getText(self, '비밀번호 설정', '이 문서에 걸어둘 새 비밀번호를 입력하세요:', QLineEdit.EchoMode.Password)
        if ok and pwd:
            enc_pw = obfuscate_text(pwd)
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("UPDATE memos SET password=? WHERE id=?", (enc_pw, m_id))
            conn.commit()
            conn.close()
            QMessageBox.information(self, "설정 완료", "문서가 비밀번호로 안전하게 잠겼습니다.")
            
            self.unlocked_memos.discard(m_id)
            self.refresh_all_views() 
            self.update_home_dashboard()
            
            if self.current_memo_id == m_id:
                pseudo_item = QListWidgetItem()
                pseudo_item.setData(Qt.ItemDataRole.UserRole, m_id)
                self.load_memo_content(pseudo_item)

    def remove_memo_password(self, m_id):
        self.browser.clearFocus()
        self.viewer.clearFocus()
        self.setFocus()
        
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT password FROM memos WHERE id=?", (m_id,))
        enc_pw = c.fetchone()[0]
        real_pw = deobfuscate_text(enc_pw)
        
        pwd, ok = QInputDialog.getText(self, '비밀번호 해제', '해제를 위해 현재 비밀번호를 입력하세요:', QLineEdit.EchoMode.Password)
        if ok:
            if pwd == real_pw:
                c.execute("UPDATE memos SET password='' WHERE id=?", (m_id,))
                conn.commit()
                QMessageBox.information(self, "해제 완료", "비밀번호가 해제되어 문서를 자유롭게 열람할 수 있습니다.")
                self.unlocked_memos.discard(m_id)
                self.refresh_all_views() 
                self.update_home_dashboard()
                
                if self.current_memo_id == m_id:
                    pseudo_item = QListWidgetItem()
                    pseudo_item.setData(Qt.ItemDataRole.UserRole, m_id)
                    self.load_memo_content(pseudo_item)
            else:
                QMessageBox.warning(self, "오류", "비밀번호가 일치하지 않습니다.")
        conn.close()

    def add_root_folder(self):
        self.browser.clearFocus()
        self.viewer.clearFocus()
        self.setFocus()
        text, ok = QInputDialog.getText(self, '새 루트 폴더 생성', '폴더 명칭을 입력하세요:')
        if ok and text.strip():
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("INSERT INTO folders (name, parent_id) VALUES (?, NULL)", (text.strip(),))
            conn.commit()
            conn.close()
            self.refresh_all_views()

    def add_sub_folder(self, parent_id):
        self.browser.clearFocus()
        self.viewer.clearFocus()
        self.setFocus()
        text, ok = QInputDialog.getText(self, '새 하위 폴더 생성', '하위 폴더 명칭을 입력하세요:')
        if ok and text.strip():
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("INSERT INTO folders (name, parent_id) VALUES (?, ?)", (text.strip(), parent_id))
            conn.commit()
            conn.close()
            self.refresh_all_views()

    def rename_folder_by_id(self, f_id):
        if f_id == 1:
            QMessageBox.warning(self, "수정 불가", "인프라 '기본 폴더'는 이름을 변경할 수 없습니다.")
            return
            
        self.browser.clearFocus()
        self.viewer.clearFocus()
        self.setFocus()
        
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT name FROM folders WHERE id=?", (f_id,))
        old_name = c.fetchone()[0]
        
        text, ok = QInputDialog.getText(self, '폴더 이름 수정', '변경할 새 폴더 명칭을 입력하세요:', QLineEdit.EchoMode.Normal, old_name)
        if ok and text.strip():
            c.execute("UPDATE folders SET name = ? WHERE id = ?", (text.strip(), f_id))
            conn.commit()
        conn.close()
        self.refresh_all_views()
        
    def change_folder_color_by_id(self, f_id):
        color = QColorDialog.getColor()
        if color.isValid():
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("UPDATE folders SET color = ? WHERE id = ?", (color.name(), f_id))
            conn.commit()
            conn.close()
            self.refresh_all_views()

    def delete_folder_by_id(self, f_id):
        if f_id == 1:
            QMessageBox.warning(self, "삭제 불가", "인프라 '기본 폴더'는 시스템 보존 항목이므로 삭제할 수 없습니다.")
            return
            
        reply = QMessageBox.question(self, '폴더 삭제 확인', 
                                     '이 폴더를 삭제하시겠습니까?\n내부에 보관 중이던 문서와 하위 폴더들은 모두 [기본 폴더]로 안전하게 백업 이관됩니다.', 
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("UPDATE folders SET parent_id = 1 WHERE parent_id = ?", (f_id,))
            c.execute("UPDATE memos SET folder_id = 1 WHERE folder_id = ?", (f_id,))
            c.execute("DELETE FROM folders WHERE id = ?", (f_id,))
            conn.commit()
            conn.close()
            self.refresh_all_views()
            self.update_home_dashboard()

    def rename_memo_by_id(self, m_id):
        self.browser.clearFocus()
        self.viewer.clearFocus()
        self.setFocus()
        
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT title, content, tags, last_editor, revision FROM memos WHERE id=?", (m_id,))
        row = c.fetchone()
        
        if not row:
            conn.close()
            return
            
        old_title, content, tags, last_editor, old_rev = row
        text, ok = QInputDialog.getText(self, '문서 이름 변경', '새 문서 제목을 입력하세요:', QLineEdit.EchoMode.Normal, old_title)
        
        if ok and text.strip() and text.strip() != old_title:
            new_title = text.strip()
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            current_user = get_current_username()
            new_rev = (old_rev if old_rev else 1) + 1
            
            c.execute("""INSERT INTO memo_history (memo_id, title, content, tags, saved_at, editor, revision) 
                         VALUES (?, ?, ?, ?, ?, ?, ?)""",
                      (m_id, old_title, content, tags, now_str, last_editor, old_rev))
            c.execute("DELETE FROM memo_history WHERE memo_id = ? AND history_id NOT IN (SELECT history_id FROM memo_history WHERE memo_id = ? ORDER BY history_id DESC LIMIT 10)", (m_id, m_id))
            
            c.execute("UPDATE memos SET title=?, updated_at=?, last_editor=?, revision=? WHERE id=?", 
                      (new_title, now_str, current_user, new_rev, m_id))
            conn.commit()
            conn.close()
            self.refresh_all_views()
            
            if self.current_memo_id == m_id:
                pseudo_item = QListWidgetItem()
                pseudo_item.setData(Qt.ItemDataRole.UserRole, m_id)
                self.load_memo_content(pseudo_item)
        else:
            conn.close()

    def load_tree(self, dummy=None):
        search_text = self.search_input.text().strip()
        selected_tag = self.tag_filter_combo.currentText()
        tag_filter = selected_tag.replace("#", "") if selected_tag and selected_tag != "🏷️ 모든 태그 보기" else ""
        
        expanded_ids = set()
        
        if self.tree_initialized and hasattr(self, 'memo_tree') and self.memo_tree.topLevelItemCount() > 0:
            it = QTreeWidgetItemIterator(self.memo_tree)
            while it.value():
                item = it.value()
                if item.isExpanded():
                    data = item.data(0, Qt.ItemDataRole.UserRole)
                    if data: expanded_ids.add(data)
                it += 1
        elif not self.tree_initialized:
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("SELECT value FROM settings WHERE key='expanded_folders'")
            row = c.fetchone()
            conn.close()
            if row and row[0]:
                try:
                    expanded_list = json.loads(row[0])
                    expanded_ids = set(expanded_list)
                except:
                    expanded_ids = "ALL"
            else:
                expanded_ids = "ALL"

        self.memo_tree.clear()
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT id, name, parent_id, color FROM folders ORDER BY sort_order ASC, id ASC")
        folders = c.fetchall()
        
        query = "SELECT id, title, folder_id, revision, tags, password, content FROM memos WHERE deleted_at IS NULL ORDER BY updated_at DESC"
        c.execute(query)
        memos = c.fetchall()
        conn.close()

        folder_dict = {}
        for f_id, name, p_id, color in folders:
            item = QTreeWidgetItem([f"📂 {name}"])
            item.setData(0, Qt.ItemDataRole.UserRole, f"folder_{f_id}")
            if color: 
                item.setForeground(0, QBrush(QColor(color)))
            folder_dict[f_id] = {'item': item, 'p_id': p_id}

        root_items = []
        for f_id, data in folder_dict.items():
            p_id = data['p_id']
            if p_id and p_id in folder_dict:
                folder_dict[p_id]['item'].addChild(data['item'])
            else:
                root_items.append(data['item'])

        self.memo_tree.addTopLevelItems(root_items)

        for m_id, title, f_id, rev, tags_str, pw, enc_content in memos:
            if tag_filter:
                memo_tags = [t.strip() for t in (tags_str or "").split(',') if t.strip()]
                if tag_filter not in memo_tags:
                    continue
                if pw and m_id not in self.unlocked_memos:
                    continue

            if search_text:
                search_lower = search_text.lower()
                title_match = search_lower in title.lower()
                tags_match = False
                content_match = False
                
                if tags_str:
                    memo_tags = [t.strip().lower() for t in tags_str.split(',')]
                    tags_match = any(search_lower in t for t in memo_tags)
                    
                if pw and m_id not in self.unlocked_memos:
                    tags_match = False 
                else:
                    if enc_content:
                        dec_content = deobfuscate_text(enc_content).lower()
                        if search_lower in dec_content:
                            content_match = True
                    
                if not (title_match or tags_match or content_match):
                    continue
                
            if pw:
                icon_str = "🔓" if m_id in self.unlocked_memos else "🔒"
            else:
                icon_str = "📄"
                
            m_item = QTreeWidgetItem([f"{icon_str} {title} (r{rev})"])
            m_item.setData(0, Qt.ItemDataRole.UserRole, f"memo_{m_id}")
            
            if f_id in folder_dict:
                folder_dict[f_id]['item'].addChild(m_item)
            else:
                if 1 in folder_dict: folder_dict[1]['item'].addChild(m_item)

        is_filtering = bool(search_text) or bool(tag_filter)

        def update_visibility(item):
            has_visible_child = False
            for i in range(item.childCount()):
                child = item.child(i)
                child_visible = update_visibility(child)
                if child_visible: has_visible_child = True

            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data and str(data).startswith("memo_"):
                return True

            if is_filtering:
                item.setHidden(not has_visible_child)
                if has_visible_child: item.setExpanded(True)
                return has_visible_child
            else:
                if data == "folder_1" and item.childCount() == 0:
                    item.setHidden(True)
                    return False
                    
                item.setHidden(False)
                return True

        for i in range(self.memo_tree.topLevelItemCount()):
            update_visibility(self.memo_tree.topLevelItem(i))

        if not is_filtering:
            if expanded_ids == "ALL":
                self.memo_tree.expandAll()
            else:
                it = QTreeWidgetItemIterator(self.memo_tree)
                while it.value():
                    item = it.value()
                    data = item.data(0, Qt.ItemDataRole.UserRole)
                    if data in expanded_ids:
                        item.setExpanded(True)
                    it += 1
            self.tree_initialized = True

    def on_tree_item_clicked(self, item, column):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data: return
        if data.startswith("memo_"):
            m_id = int(data.split("_")[1])
            pseudo_item = QListWidgetItem()
            pseudo_item.setData(Qt.ItemDataRole.UserRole, m_id)
            self.load_memo_content(pseudo_item)

    def open_folder_select_dialog(self):
        dialog = FolderSelectDialog(self, self.current_edit_folder_id)
        dialog.setStyleSheet(self.styleSheet())
        if dialog.exec():
            self.current_edit_folder_id = dialog.selected_id
            self.btn_folder_select.setText(f"📂 소속 폴더 지정: {dialog.selected_name}")

    def new_memo(self):
        self.current_memo_id = None
        f_id = 1
        f_name = "기본 폴더"
        
        selected = self.memo_tree.currentItem()
        if selected:
            data = selected.data(0, Qt.ItemDataRole.UserRole)
            if data and data.startswith("folder_"):
                f_id = int(data.split("_")[1])
                f_name = selected.text(0).replace("📂 ", "")
            elif data and data.startswith("memo_"):
                parent = selected.parent()
                if parent:
                    p_data = parent.data(0, Qt.ItemDataRole.UserRole)
                    if p_data:
                        f_id = int(p_data.split("_")[1])
                        f_name = parent.text(0).replace("📂 ", "")
                        
        self.current_edit_folder_id = f_id
        self.btn_folder_select.setText(f"📂 소속 폴더 지정: {f_name}")
            
        self.title_input.clear()
        self.tags_input.clear()
        
        js_code = """
            (function() {
                if (typeof window.CKEDITOR !== 'undefined') {
                    for (var key in window.CKEDITOR.instances) {
                        window.CKEDITOR.instances[key].setData('');
                        return;
                    }
                }
                if (typeof window.setEditorData === 'function') window.setEditorData('');
            })();
        """
        self.browser.page().runJavaScript(js_code)
        self.right_stack.setCurrentIndex(2)

    def edit_memo(self):
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT title, content, tags, folder_id FROM memos WHERE id=?", (self.current_memo_id,))
        row = c.fetchone()
        
        f_name = "기본 폴더"
        if row:
            f_id = row[3] if row[3] is not None else 1
            self.current_edit_folder_id = f_id
            c.execute("SELECT name FROM folders WHERE id=?", (f_id,))
            f_row = c.fetchone()
            if f_row: f_name = f_row[0]
            self.btn_folder_select.setText(f"📂 소속 폴더 지정: {f_name}")
            
            self.title_input.setText(row[0]) 
            content = deobfuscate_text(row[1]) if row[1] is not None else ""
            tags = row[2] if row[2] is not None else ""
            self.tags_input.setText(tags)
            
            b64_content = base64.b64encode(content.encode('utf-8')).decode('utf-8')
            js_code = f"""
                (function() {{
                    var decoded = decodeURIComponent(escape(atob('{b64_content}')));
                    if (typeof window.CKEDITOR !== 'undefined') {{
                        for (var key in window.CKEDITOR.instances) {{
                            window.CKEDITOR.instances[key].setData(decoded);
                            return;
                        }}
                    }}
                    if (typeof window.setEditorData === 'function') window.setEditorData(decoded);
                }})();
            """
            self.browser.page().runJavaScript(js_code)
            self.right_stack.setCurrentIndex(2)
        conn.close()

    def cancel_edit(self):
        if self.current_memo_id is None: self.go_home() 
        else: self.right_stack.setCurrentIndex(1)

    def delete_memo(self):
        if self.current_memo_id is None: return
        reply = QMessageBox.question(self, '휴지통 이동', '문서를 휴지통으로 이동하시겠습니까?\n(7일 후 자동으로 영구 삭제됩니다.)', 
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("UPDATE memos SET deleted_at=? WHERE id=?", (now_str, self.current_memo_id))
            conn.commit()
            conn.close()
            
            self.unlocked_memos.discard(self.current_memo_id)
            self.current_memo_id = None
            self.refresh_all_views()
            self.go_home() 
            QMessageBox.information(self, "이동 완료", "문서가 휴지통으로 분리되었습니다.")

    def save_memo_request(self):
        title = self.title_input.text()
        if not title.strip():
            QMessageBox.warning(self, "알림", "제목을 입력해주세요.")
            return
            
        js_code = """
            (function() {
                if (typeof window.CKEDITOR !== 'undefined') {
                    for (var key in window.CKEDITOR.instances) {
                        return window.CKEDITOR.instances[key].getData();
                    }
                }
                if (typeof window.getEditorData === 'function') return window.getEditorData();
                return "";
            })();
        """
        self.browser.page().runJavaScript(js_code, self.save_to_db)

    def save_to_db(self, html_content):
        if html_content is None: html_content = ""
        title = self.title_input.text()
        tags = self.tags_input.text().strip()
        target_folder_id = self.current_edit_folder_id
        
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        current_user = get_current_username()
        enc_content = obfuscate_text(html_content)
        
        conn = get_db_connection()
        c = conn.cursor()
        
        if self.current_memo_id is None:
            c.execute("""INSERT INTO memos (title, content, tags, created_at, updated_at, author, last_editor, revision, folder_id) 
                         VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)""", 
                      (title, enc_content, tags, now_str, now_str, current_user, current_user, target_folder_id))
            self.current_memo_id = c.lastrowid
        else:
            c.execute("SELECT title, content, tags, last_editor, revision FROM memos WHERE id=?", (self.current_memo_id,))
            old_data = c.fetchone()
            if old_data:
                old_rev = old_data[4] if old_data[4] else 1
                new_rev = old_rev + 1
                c.execute("""INSERT INTO memo_history (memo_id, title, content, tags, saved_at, editor, revision) 
                             VALUES (?, ?, ?, ?, ?, ?, ?)""",
                          (self.current_memo_id, old_data[0], old_data[1], old_data[2], now_str, old_data[3], old_rev))
                c.execute("DELETE FROM memo_history WHERE memo_id = ? AND history_id NOT IN (SELECT history_id FROM memo_history WHERE memo_id = ? ORDER BY history_id DESC LIMIT 10)", (self.current_memo_id, self.current_memo_id))

            c.execute("UPDATE memos SET title=?, content=?, tags=?, updated_at=?, last_editor=?, revision=?, folder_id=? WHERE id=?", 
                      (title, enc_content, tags, now_str, current_user, new_rev, target_folder_id, self.current_memo_id))
            
        conn.commit()
        conn.close()
        
        self.refresh_all_views()
        self.update_home_dashboard() 
        
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, self.current_memo_id)
        self.load_memo_content(item)

    def load_memo_content(self, item):
        target_id = item.data(Qt.ItemDataRole.UserRole)
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT title, content, tags, created_at, updated_at, author, last_editor, revision, password FROM memos WHERE id=?", (target_id,))
        row = c.fetchone()
        conn.close()
        
        if row:
            title = row[0]
            html_content = deobfuscate_text(row[1]) if row[1] is not None else ""
            tags = row[2] if row[2] is not None else ""
            created_at = row[3] if row[3] is not None else "기록 없음"
            updated_at = row[4] if row[4] is not None else "기록 없음"
            author = row[5] if row[5] is not None else "알 수 없음"
            last_editor = row[6] if row[6] is not None else "알 수 없음"
            rev = row[7] if row[7] is not None else 1
            enc_pw = row[8]
            
            self.current_memo_id = target_id
            
            if enc_pw and target_id not in self.unlocked_memos:
                self.show_locked_view(title, rev)
            else:
                self.refresh_view_mode(title, tags, html_content, created_at, updated_at, author, last_editor, rev)

    def show_locked_view(self, title, rev):
        self.view_title.setText(f"🔒 {title} <span style='font-size: 14px; color: gray; font-weight: normal;'>(ID: {self.current_memo_id} | r{rev})</span>")
        self.view_authors.setText("보안 잠금 활성화")
        self.view_dates.setText("")
        self.tags_container.hide()
        self.search_bar.hide_bar()
        
        self.view_btn_search.hide()
        self.view_btn_history.hide()
        self.view_btn_popup.hide()
        self.view_btn_export.hide()
        self.view_btn_pdf.hide()
        self.view_btn_edit.hide()
        self.view_btn_delete.hide()
        
        self.view_btn_unlock.show()
        
        inject_html_safe(self.viewer, "<h3 style='text-align:center; color:#ff6666; margin-top:50px;'>🔒 문서가 보호되고 있습니다.<br><br>상단의 [잠금 해제] 버튼을 눌러 비밀번호를 입력하세요.</h3>", theme_js=self.get_theme_js())
        self.right_stack.setCurrentIndex(1)

    def prompt_unlock(self):
        if not self.current_memo_id: return
        
        self.browser.clearFocus()
        self.viewer.clearFocus()
        self.setFocus()
        
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT password FROM memos WHERE id=?", (self.current_memo_id,))
        row = c.fetchone()
        conn.close()
        
        if not row or not row[0]: return
        real_pw = deobfuscate_text(row[0])
        
        pwd, ok = QInputDialog.getText(self, '보안 잠금 해제', '이 문서를 열람하려면 비밀번호를 입력하세요:', QLineEdit.EchoMode.Password)
        if ok and pwd == real_pw:
            self.unlocked_memos.add(self.current_memo_id)
            self.refresh_all_views() 
            self.update_home_dashboard() 
            self.force_load_memo_content(self.current_memo_id)
        elif ok:
            QMessageBox.warning(self, "경고", "비밀번호가 일치하지 않습니다.")

    def force_load_memo_content(self, m_id):
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT title, content, tags, created_at, updated_at, author, last_editor, revision FROM memos WHERE id=?", (m_id,))
        row = c.fetchone()
        conn.close()
        
        if row:
            title = row[0]
            html_content = deobfuscate_text(row[1]) if row[1] is not None else ""
            tags = row[2] if row[2] is not None else ""
            created_at = row[3] if row[3] is not None else "기록 없음"
            updated_at = row[4] if row[4] is not None else "기록 없음"
            author = row[5] if row[5] is not None else "알 수 없음"
            last_editor = row[6] if row[6] is not None else "알 수 없음"
            rev = row[7] if row[7] is not None else 1
            
            self.refresh_view_mode(title, tags, html_content, created_at, updated_at, author, last_editor, rev)

    def refresh_view_mode(self, title, tags, html_content, created_at="", updated_at="", author="", last_editor="", revision=1):
        self.view_title.setText(f"{title} <span style='font-size: 14px; color: gray; font-weight: normal;'>(ID: {self.current_memo_id} | r{revision})</span>")
        self.view_authors.setText(f"✍️ 작성: {author}  |  📝 수정: {last_editor}")
        
        self.current_dates_text = f"작성일: {created_at}  |  수정일: {updated_at}"
        
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT password FROM memos WHERE id=?", (self.current_memo_id,))
        pw_row = c.fetchone()
        conn.close()
        
        if pw_row and pw_row[0]:
            self.update_countdown() 
        else:
            self.view_dates.setText(self.current_dates_text)
            
        self.search_bar.hide_bar()
        
        self.view_btn_unlock.hide()
        self.view_btn_search.show()
        self.view_btn_history.show()
        self.view_btn_popup.show()
        self.view_btn_export.show()
        self.view_btn_pdf.show()
        self.view_btn_edit.show()
        self.view_btn_delete.show()
        
        while self.tags_layout.count():
            item = self.tags_layout.takeAt(0)
            widget = item.widget()
            if widget: widget.deleteLater()

        if tags.strip():
            tag_list = [t.strip() for t in tags.split(",") if t.strip()]
            for t in tag_list:
                lbl = QLabel(f"#{t}", self)
                lbl.setStyleSheet("background-color: #ff9800; color: white; padding: 3px 7px; border-radius: 4px; font-weight: bold; font-size: 12px;")
                self.tags_layout.addWidget(lbl)
            self.tags_container.show()
        else:
            self.tags_container.hide()
            
        inject_html_safe(self.viewer, html_content, theme_js=self.get_theme_js())
        self.right_stack.setCurrentIndex(1)

    def cleanup_trash(self):
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT id FROM memos WHERE deleted_at IS NOT NULL AND datetime(deleted_at) <= datetime('now', '-7 days')")
        expired_ids = [row[0] for row in c.fetchall()]
        if expired_ids:
            placeholders = ','.join('?' * len(expired_ids))
            c.execute(f"DELETE FROM memo_history WHERE memo_id IN ({placeholders})", expired_ids)
            c.execute(f"DELETE FROM memos WHERE id IN ({placeholders})", expired_ids)
            conn.commit()
        conn.close()

    def open_popup_viewer(self):
        if not self.current_memo_id: return
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT title, content, tags, created_at, updated_at, author, last_editor, revision FROM memos WHERE id=?", (self.current_memo_id,))
        row = c.fetchone()
        conn.close()
        
        if row:
            title = row[0]
            content = deobfuscate_text(row[1]) if row[1] else ""
            tags = row[2] if row[2] else ""
            created_at = row[3] if row[3] else ""
            updated_at = row[4] if row[4] else ""
            author = row[5] if row[5] else ""
            last_editor = row[6] if row[6] else ""
            rev = row[7] if row[7] else 1
            
            if self.popup_window is not None:
                self.popup_window.close()
                self.popup_window.deleteLater()
                
            self.popup_window = MemoPopupWindow(self, self.current_memo_id, title, tags, content, created_at, updated_at, author, last_editor, rev)
            self.popup_window.setStyleSheet(self.styleSheet())
            self.popup_window.show()

    def open_settings(self):
        dialog = SettingsDialog(self)
        dialog.setStyleSheet(self.styleSheet())
        dialog.exec()
        self.load_lock_timeout() 
        self.update_home_dashboard() 

    def open_trash_dialog(self):
        dialog = TrashDialog(self)
        dialog.setStyleSheet(self.styleSheet())
        dialog.exec()

    def open_history_dialog(self):
        if not self.current_memo_id: return
        dialog = HistoryDialog(self.current_memo_id, self)
        dialog.setStyleSheet(self.styleSheet()) 
        dialog.exec()

    def restore_memo(self, memo_id, history_id):
        conn = get_db_connection()
        c = conn.cursor()
        
        c.execute("SELECT title, content, tags, revision FROM memo_history WHERE history_id=?", (history_id,))
        restored = c.fetchone()
        if not restored: return
        r_title, r_content, r_tags, r_rev = restored
        
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        current_user = get_current_username()
        
        c.execute("SELECT title, content, tags, last_editor, revision FROM memos WHERE id=?", (memo_id,))
        current = c.fetchone()
        if current:
            current_rev = current[4] if current[4] else 1
            new_rev = current_rev + 1
            c.execute("INSERT INTO memo_history (memo_id, title, content, tags, saved_at, editor, revision) VALUES (?, ?, ?, ?, ?, ?, ?)",
                      (memo_id, current[0], current[1], current[2], now_str, current[3], current_rev))
            c.execute("DELETE FROM memo_history WHERE memo_id = ? AND history_id NOT IN (SELECT history_id FROM memo_history WHERE memo_id = ? ORDER BY history_id DESC LIMIT 10)", (memo_id, memo_id))
            
        c.execute("UPDATE memos SET title=?, content=?, tags=?, updated_at=?, last_editor=?, revision=? WHERE id=?", 
                  (r_title, r_content, r_tags, now_str, current_user, new_rev, memo_id))
        conn.commit()
        conn.close()
        
        QMessageBox.information(self, "복구 완료", f"과거 r{r_rev} 버전의 내용으로 덮어씌워\n새 리비전(r{new_rev})을 생성했습니다. (수행자: {current_user})")
        self.refresh_all_views()
        self.update_home_dashboard()
        
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, memo_id)
        self.load_memo_content(item)

    def export_to_pdf(self):
        if not self.current_memo_id: return
        safe_title = "".join(c for c in self.view_title.text().split("<span")[0] if c.isalnum() or c in (' ', '-', '_')).strip()
        if not safe_title: safe_title = "memo_document"
        filepath, _ = QFileDialog.getSaveFileName(self, "PDF로 내보내기", f"{safe_title}.pdf", "PDF Files (*.pdf)")
        if filepath:
            self.viewer.page().printToPdf(filepath)

    def on_pdf_printed(self, filepath, success):
        if success: QMessageBox.information(self, "저장 완료", f"저장 경로: {filepath}")
        else: QMessageBox.warning(self, "오류", "저장에 실패했습니다.")

    def update_home_dashboard(self):
        self.cleanup_trash()
        current_user = get_current_username()
        self.current_session_label.setText(f"👤 현재 사용 중인 사용자: <font color='#4da6ff'><b>{current_user}</b></font>")
        
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM memos WHERE deleted_at IS NULL")
        total_memos = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM memos WHERE deleted_at IS NULL AND (author=? OR last_editor=?)", (current_user, current_user))
        my_memos = c.fetchone()[0]

        c.execute("SELECT COUNT(*) FROM memos WHERE deleted_at IS NULL AND password != ''")
        locked_memos = c.fetchone()[0]

        c.execute("SELECT tags, password FROM memos WHERE deleted_at IS NULL AND tags IS NOT NULL AND tags != ''")
        rows = c.fetchall()
        all_tags = set()
        for row in rows:
            tags_str, pw = row[0], row[1]
            if not pw:
                for t in tags_str.split(','):
                    if t.strip(): all_tags.add(t.strip())
        total_tags = len(all_tags)
        
        c.execute("PRAGMA table_info(memos)")
        total_cols = len(c.fetchall())

        img_count = 0; img_size_bytes = 0
        c.execute("SELECT content FROM memos WHERE deleted_at IS NULL")
        for row in c.fetchall():
            content = deobfuscate_text(row[0]) if row[0] else ""
            if content and "<img " in content:
                img_srcs = re.findall(r'<img[^>]+src=["\'](data:image/[^;]+;base64,[^"\']+)["\']', content)
                for src in img_srcs:
                    img_count += 1
                    img_size_bytes += len(src) 
                    
        c.execute("SELECT COUNT(*) FROM memos WHERE deleted_at IS NOT NULL")
        trash_count = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM memos WHERE deleted_at IS NOT NULL AND datetime(deleted_at) <= datetime('now', '-5 days')")
        imminent_count = c.fetchone()[0]
        conn.close()

        # 하이브리드 지원 로직
        if DB_TYPE == "mysql":
            db_size_str = "서버에서 관리됨"
            db_abs_path = f"MySQL Server: {MYSQL_CONFIG.get('host')}"
        else:
            db_size = os.path.getsize(CURRENT_SQLITE_PATH) if os.path.exists(CURRENT_SQLITE_PATH) else 0
            db_size_str = format_size(db_size)
            db_abs_path = os.path.abspath(CURRENT_SQLITE_PATH)

        stats_html = f"""
        <table width="100%" cellpadding="8" cellspacing="0" style="font-size: 15px;">
            <tr>
                <td width="50%"><b>📝 총 활성 문서:</b> {total_memos} 개 (내 기여: {my_memos}개)</td>
                <td width="50%"><b>🏷️ 공개된 태그:</b> {total_tags} 개</td>
            </tr>
            <tr>
                <td><b>🖼️ DB 내 사진:</b> {img_count} 장 ({format_size(img_size_bytes)})</td>
                <td><b>🔒 잠긴 문서:</b> {locked_memos} 개</td>
            </tr>
            <tr>
                <td><b>💽 총 DB 용량:</b> {db_size_str} (현재 컬럼: {total_cols}개)</td>
                <td><b>📂 DB 경로:</b> <span style="font-size: 13px; color: gray;">{db_abs_path}</span></td>
            </tr>
            <tr>
                <td colspan="2" style="color: #ff6666; background-color: rgba(255, 102, 102, 0.1); border-radius: 4px;">
                    <b>🗑️ 휴지통 (7일 후 자동 영구삭제):</b> 대기 중인 문서 {trash_count} 개 (삭제 임박: {imminent_count} 개)
                </td>
            </tr>
        </table>
        """
        self.stats_label.setText(stats_html)
        
        self.recent_list.clear()
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT id, title, tags, last_editor, revision, password FROM memos WHERE deleted_at IS NULL ORDER BY updated_at DESC LIMIT 10")
        for row in c.fetchall():
            memo_id, title, tags, last_editor, rev, pw = row[0], row[1], row[2], row[3], row[4], row[5]
            
            if pw:
                icon_str = "🔓" if memo_id in self.unlocked_memos else "🔒"
            else:
                icon_str = "📄"
                
            display_text = f"{icon_str} {title} (r{rev})  (수정: {last_editor})"
            
            if tags and (not pw or memo_id in self.unlocked_memos):
                display_text += f"  [{tags}]"
            elif pw:
                display_text += "  [비공개 태그]"
                
            item = QListWidgetItem(display_text)
            item.setData(Qt.ItemDataRole.UserRole, memo_id)
            self.recent_list.addItem(item)
        conn.close()

    def closeEvent(self, event):
        global IS_RESTARTING  # ★ 전역 스위치 호출
        
        if IS_RESTARTING:
            reply = QMessageBox.StandardButton.Yes
        else:
            reply = QMessageBox.question(self, '종료', '프로그램을 종료하시겠습니까?', QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            
        if reply == QMessageBox.StandardButton.Yes:
            
            try:
                geom = self.saveGeometry().toHex().data().decode('utf-8')
                zf = self.browser.zoomFactor() if self.right_stack.currentIndex() == 2 else self.viewer.zoomFactor()
                
                # ★ 추가 1: 스플리터(좌우 패널) 비율 상태 추출
                splitter_state = self.main_splitter.saveState().toHex().data().decode('utf-8')
                
                conn = get_db_connection()
                c = conn.cursor()
                # ★ 수정됨: MySQL 충돌 방지를 위해 `key` 백틱 처리 & 스플리터 상태 저장
                c.execute("INSERT OR REPLACE INTO settings (`key`, value) VALUES ('zoom_factor', ?)", (str(zf),))
                c.execute("INSERT OR REPLACE INTO settings (`key`, value) VALUES ('window_geometry', ?)", (geom,))
                c.execute("INSERT OR REPLACE INTO settings (`key`, value) VALUES ('splitter_state', ?)", (splitter_state,))
                conn.commit()
                conn.close()
            except: pass
            
            expanded_ids = []
            it = QTreeWidgetItemIterator(self.memo_tree)
            while it.value():
                item = it.value()
                data = item.data(0, Qt.ItemDataRole.UserRole)
                if item.isExpanded() and data and str(data).startswith("folder_"):
                    expanded_ids.append(data)
                it += 1
                
            conn = get_db_connection()
            c = conn.cursor()
            # ★ 수정됨: 여기도 `key` 백틱 처리
            c.execute("INSERT OR REPLACE INTO settings (`key`, value) VALUES ('expanded_folders', ?)", (json.dumps(expanded_ids),))
            conn.commit()
            conn.close()

            if hasattr(self, 'theme_timer'): self.theme_timer.stop()
            if hasattr(self, 'countdown_timer'): self.countdown_timer.stop()
            if hasattr(self, 'zoom_sync_timer'): self.zoom_sync_timer.stop()
            if hasattr(self, 'sync_timer'): self.sync_timer.stop()
            if hasattr(self, 'scratchpad_window') and self.scratchpad_window: self.scratchpad_window.close()
            if self.popup_window: self.popup_window.close()
            event.accept()
        else: event.ignore()

    def show_help(self):
        help_text = (
            "💡 [시스템 도움말 및 안내]\n\n"
            "1. 트리 뷰 & 컬러 매핑: 빈 공간 우클릭으로 새 폴더를 만들고, 특정 폴더 우클릭 후 색상을 지정해 시각화하세요.\n"
            "2. 경로 보존 내보내기/가져오기: .worknb 확장자로 문서를 공유하면, 타 PC에서 불러올 때 해당 폴더 구조가 자동 생성됩니다.\n"
            "3. 철통 보안 및 자동 잠금: 문서 우클릭으로 암호를 걸면 설정된 시간 동안 조작이 없을 시 자동 잠금되며, 모든 편집 툴바가 가려집니다.\n"
            "4. 내부 문서 위키 연결: 에디터에서 링크 삽입 후 주소에 'memo://문서제목' 또는 'memo://문서고유번호' 를 적으면 서로 연결됩니다.\n"
            "5. 자동 동기화: 여러 PC에서 서버 DB 연결 시 10초마다 자동 변경 사항이 반영되며, 'F5' 키로 수동 동기화가 가능합니다."
        )
        QMessageBox.information(self, "이용 안내", help_text)

    def show_about(self):
        try:
            base_path = sys._MEIPASS
        except Exception:
            base_path = os.path.abspath(".")
            
        curr_theme = self.current_theme_setting
        if curr_theme == 'light':
            is_dark = False
        elif curr_theme == 'dark':
            is_dark = True
        else:
            palette = QApplication.palette()
            is_dark = palette.color(palette.ColorGroup.Active, palette.ColorRole.Text).lightness() > 128
        
        if is_dark:
            logo_name = "logo_dark.png"
            about_html = (
                "<div style='text-align: center;'>"
                "<h2>오프라인 HTML 메모장</h2>"
                "<p><b>버전:</b> v1.2.1.0</p>"
                "<p>오프라인 전용 및 서버 연동 지원 메모 프로그램입니다.</p>"
                "<hr>"
                "<p style='color: #aaaaaa; font-size: 11px;'>"
                "Copyright © 2026 Sunaookami Kuroko.<br>All rights reserved.<br>Built with PyQt6, SQLite3 & PyMySQL."
                "</p>"
                "</div>"
            )
        else:
            logo_name = "logo_light.png"
            about_html = (
                "<div style='text-align: center;'>"
                "<h2>오프라인 HTML 메모장</h2>"
                "<p><b>버전:</b> v1.2.1.0</p>"
                "<p>오프라인 전용 및 서버 연동 지원 메모 프로그램입니다.</p>"
                "<hr>"
                "<p style='color: #555555; font-size: 11px;'>"
                "Copyright © 2026 Sunaookami Shiroko.<br>All rights reserved.<br>Built with PyQt6, SQLite3 & PyMySQL."
                "</p>"
                "</div>"
            )
            
        logo_path = os.path.join(base_path, "assets", logo_name)
        if not os.path.exists(logo_path):
            logo_path = os.path.join(base_path, "assets", "logo.png")
        
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("프로그램 및 저작권 정보")
        msg_box.setText(about_html)
        
        if os.path.exists(logo_path):
            pixmap = QPixmap(logo_path)
            scaled_pixmap = pixmap.scaled(120, 120, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            msg_box.setIconPixmap(scaled_pixmap)
            
        msg_box.exec()

    def update_theme_styles(self):
        try:
            if self.current_theme_setting == 'light':
                is_dark = False
            elif self.current_theme_setting == 'dark':
                is_dark = True
            else:
                palette = QApplication.palette()
                is_dark = palette.color(palette.ColorGroup.Active, palette.ColorRole.Text).lightness() > 128
            
            if hasattr(self, '_last_is_dark') and self._last_is_dark == is_dark:
                return
            self._last_is_dark = is_dark
            
            if is_dark:
                app_bg = "#1e1e1e"        
                panel_bg = "#2b2b2b"      
                text_color = "#e0e0e0"    
                border_color = "#3e3e3e"  
                focus_bg = "#2b2b2b"      
                card_bg = "#353535"       
                splitter_color = "#555555"
                btn_bg = "#333333"        
                btn_hover = "#444444"     
                mode_str = "dark"
            else:
                app_bg = "#f0f2f5"        
                panel_bg = "#ffffff"      
                text_color = "#111111"    
                border_color = "#cccccc"  
                focus_bg = "#ffffff"      
                card_bg = "#ffffff"       
                splitter_color = "#dddddd"
                btn_bg = "#e4e4e4"        
                btn_hover = "#d4d4d4"     
                mode_str = "light"
                
            global_style = f"""
                QMainWindow, QWidget {{
                    background-color: {app_bg};
                    color: {text_color};
                }}
                QLineEdit, QComboBox, QTextEdit, QListWidget, QTreeWidget {{
                    padding: 8px; font-size: 14px; border: 1px solid {border_color};
                    border-radius: 4px; background-color: {panel_bg}; color: {text_color};
                }}
                QLineEdit:focus, QTextEdit:focus {{ border: 1px solid #4da6ff; background-color: {focus_bg}; }}
                QComboBox QAbstractItemView {{ background-color: {panel_bg}; color: {text_color}; selection-background-color: #4da6ff; }}
                QSplitter::handle {{ background-color: {splitter_color}; margin: 0px 5px; }}
                QPushButton {{
                    background-color: {btn_bg};
                    border: 1px solid {border_color};
                    border-radius: 4px;
                    padding: 6px;
                    color: {text_color};
                }}
                QPushButton:hover {{ background-color: {btn_hover}; }}
                QLabel {{ background: transparent; color: {text_color}; }}
                QMessageBox {{ background-color: {app_bg}; color: {text_color}; }}
                QDialog {{ background-color: {app_bg}; }}
            """
            
            QApplication.instance().setStyleSheet(global_style)
            self.setStyleSheet(global_style)
            
            self.search_bar.setStyleSheet(f"QFrame {{ background-color: rgba(128, 128, 128, 0.15); border-radius: 6px; }}")
            self.search_bar.input.setStyleSheet(f"background: transparent; border: none; font-size: 14px; color: {text_color};")
            self.stats_label.setStyleSheet(f"background-color: {card_bg}; color: {text_color}; padding: 15px; border-radius: 8px; border: 1px solid {border_color};")
            
            if hasattr(self, 'scratchpad_window') and self.scratchpad_window:
                self.scratchpad_window.setStyleSheet(global_style)
                
            if hasattr(self, 'popup_window') and self.popup_window:
                self.popup_window.setStyleSheet(global_style)

            js_theme = self.get_theme_js()
            if hasattr(self, 'viewer'): self.viewer.page().runJavaScript(js_theme)
            if hasattr(self, 'popup_window') and self.popup_window and hasattr(self.popup_window, 'viewer'):
                self.popup_window.viewer.page().runJavaScript(js_theme)
            
            mode_str = 'dark' if is_dark else 'light'
            js_editor_theme = f"if(typeof window.setThemeMode === 'function') window.setThemeMode('{mode_str}');"
            if hasattr(self, 'browser'): self.browser.page().runJavaScript(js_editor_theme)
                
        except RuntimeError: pass

    def update_tag_combo(self):
        self.tag_filter_combo.blockSignals(True)
        current_selection = self.tag_filter_combo.currentText()
        self.tag_filter_combo.clear()
        self.tag_filter_combo.addItem("🏷️ 모든 태그 보기")

        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT id, tags, password FROM memos WHERE deleted_at IS NULL AND tags IS NOT NULL AND tags != ''")
        rows = c.fetchall()
        conn.close()

        all_tags = set()
        for row in rows:
            m_id, tags_str, pw = row
            if pw and m_id not in self.unlocked_memos:
                continue 
            for t in tags_str.split(','):
                if t.strip(): all_tags.add(t.strip())

        for tag in sorted(all_tags): self.tag_filter_combo.addItem(f"#{tag}")

        index = self.tag_filter_combo.findText(current_selection)
        if index >= 0: self.tag_filter_combo.setCurrentIndex(index)
        else: self.tag_filter_combo.setCurrentIndex(0)
        self.tag_filter_combo.blockSignals(False)

if __name__ == '__main__':
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu --disable-gpu-compositing --disable-logging --log-level=3"
    try:
        sys.excepthook = global_exception_handler
        app = QApplication(sys.argv)
        default_font = QFont("Arial", 10)
        app.setFont(default_font)

        # ========================================================
        # ★ 작동이 확인된 경로(APP_PATH) 기반 + 여백 확장 및 빨간 글씨 적용
        from PyQt6.QtWidgets import QSplashScreen
        from PyQt6.QtGui import QPixmap, QColor, QPainter
        from PyQt6.QtCore import Qt
        
# 🔥 단일 파일(--onefile) 압축 풀림 경로와 일반 경로 하이브리드 탐색!
        if hasattr(sys, '_MEIPASS'):
            base_path = sys._MEIPASS
        else:
            base_path = APP_PATH
            
        logo_path = os.path.join(base_path, "assets", "logo.png") 
        if not os.path.exists(logo_path):
            logo_path = os.path.join(base_path, "assets", "icon.ico")
            
        if os.path.exists(logo_path):
            # 1. 글자가 들어갈 하단 여백을 위해 넉넉한 도화지 생성 (가로 350, 세로 200)
            canvas_w, canvas_h = 350, 200
            splash_pix = QPixmap(canvas_w, canvas_h)
            splash_pix.fill(Qt.GlobalColor.transparent) # 배경은 투명하게
            
            # 2. 로고를 128x128로 줄여서 도화지 중앙 위쪽에 얹기
            original_pix = QPixmap(logo_path)
            painter = QPainter(splash_pix)
            scaled_logo = original_pix.scaled(128, 128, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            logo_x = (canvas_w - scaled_logo.width()) // 2
            painter.drawPixmap(logo_x, 10, scaled_logo) # 위에서 10px 띄워서 그림
            painter.end()
            
            splash = QSplashScreen(splash_pix, Qt.WindowType.WindowStaysOnTopHint)
            splash.show()
            
            # 3. 글자 폰트 강렬하게 세팅 (굵게, 크기 살짝 키움)
            font = splash.font()
            font.setBold(True)
            font.setPointSize(11)
            splash.setFont(font)
            
            # 4. 글자색: 눈에 띄는 빨간색(#ff3333), 위치: 도화지의 맨 아랫부분 중앙
            show_msg = lambda text: splash.showMessage(
                text, 
                Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter, 
                QColor("#ff3333")
            )
            
            show_msg("프로그램을 실행 중입니다 . . .")
            app.processEvents()
        # ========================================================

        # 2단계: DB 인프라 초기화 작업 진행
        if 'splash' in locals(): 
            show_msg("데이터베이스(DB) 확인 중 . . .")
            app.processEvents()
        init_db()
        
        # 3단계: 메인 창 구성 및 크롬 웹뷰 엔진 로드 진행
        if 'splash' in locals(): 
            show_msg("메인 UI 및 에디터 코어 엔진 빌드 중 . . .")
            app.processEvents()
        ex = WebEngineMemoApp()
        
        # 4단계: 표출 준비 완료
        if 'splash' in locals(): 
            show_msg("실행 준비 완료!")
            app.processEvents()
        ex.show()

        # 메인 창이 뜨면 로딩 창 닫기
        if 'splash' in locals():
            splash.finish(ex)

        sys.exit(app.exec())
    except Exception as e:
        print("\n❌ 프로그램 구동 도중 치명적인 오류가 발생했습니다:")
        traceback.print_exc()
        input("\n터미널 로그를 확인한 후 종료하려면 [Enter] 키를 누르세요...")
