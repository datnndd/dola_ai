"""Seedance AI Studio — giao diện Qt6 (PySide6), giao diện duy nhất của app.

Backend (Dola/Auto/Sheet/ffmpeg/proxy/tài khoản) nằm trong studio_core.py, không
nạp tkinter. Đọc cùng workspace.json (thư mục DATA) nên tài khoản/task/cài đặt
giữ nguyên. Bảng task dùng QTableView model/view để mượt dù có hàng nghìn task.

Chạy:  python studio_qt.py   (hoặc dùng venv đã cài PySide6)
"""
from __future__ import annotations

from studio_core import *  # noqa: F401,F403  (render_dola, AutoJobs, defaults, DATA, …)
from studio_core import (defaults, fresh_task, batch_blockers, list_gallery_videos, HOME, DATA,
                         build_facebook_cookie_accounts, encode_dola_accounts, encode_google_accounts,
                         safe_login_error, task_output_path, load_keys, proxy_source,
                         rotation_status, rotator_for, run_rotation, stop_rotation,
                         credit_until, unlock_text, lock_for_credits,
                         WorkLimitReached, RateLimitExceeded, is_work_limit_error, is_rate_limit_error,
                         work_locked, rate_limited, lock_for_work, lock_for_rate_limit,
                         release_account_locks, account_status_badge, is_account_available,
                         rate_limit_text, work_lock_text,
                         sort_accounts_by_lru, select_best_account, get_earliest_recovery_time)
from task_logs import MAX_LINES_IN_TASK, TaskLogFiles, stamp
from workspace_store import WorkspaceWriter, workspace_snapshot
from studio_theme import (BG, PANEL, CARD, FIELD, BORDER, HOVER, TEXT, MUTED, DISABLED,
                          ACCENT, ACCENT_HOVER, CYAN, GREEN, RED, WARNING,
                          SUCCESS_BG, ERROR_BG, WARNING_BG, ACTIVE_BG, SCROLL)

import copy, os, queue, re, shutil, subprocess, sys, threading, time, uuid, webbrowser
from auto_jobs import MACHINE_ID, JobsClient, JobsError
from pathlib import Path

from PySide6.QtCore import (QAbstractTableModel, QModelIndex, Qt, QRect, QTimer,
                            QItemSelectionModel, QItemSelection, QEvent, QObject)
from PySide6.QtGui import QColor, QFont, QPainter, QAction
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton, QHBoxLayout,
    QVBoxLayout, QGridLayout, QTableView, QHeaderView, QStyledItemDelegate,
    QDockWidget, QPlainTextEdit, QLineEdit, QCheckBox, QComboBox, QDialog,
    QFileDialog, QMessageBox, QListWidget, QListWidgetItem, QMenu, QFrame,
    QSizePolicy, QScrollArea, QProgressBar, QStackedWidget, QDialogButtonBox, QSplitter,
    QAbstractScrollArea, QAbstractSpinBox, QInputDialog,
)

ROLE_STATUS = Qt.UserRole + 1
ROLE_PROGRESS = Qt.UserRole + 2
COLS = ['STT', 'Trạng thái', 'Thời lượng', 'Model', 'Nick tạo', 'Proxy', 'Tệp', 'Prompt', 'Tiến trình']
FAILED_BADGES = {'Lỗi', 'Lỗi 3 lần', 'Lỗi tải video', 'Vi phạm chính sách', 'Bị chặn', 'Thiếu điểm', 'Hết giờ', 'Đã dừng'}


def badge_for(status):
    """(nhãn, màu chữ, màu nền) của trạng thái task — gần với task_status.status_badge()."""
    v = (status or '').casefold()
    if 'không đủ điểm' in v or 'thiếu điểm' in v: return 'Thiếu điểm', WARNING, WARNING_BG
    if 'hết thời gian task' in v or v.startswith('hết giờ'): return 'Hết giờ', WARNING, WARNING_BG
    if v.startswith('vi phạm'): return 'Vi phạm chính sách', RED, ERROR_BG
    if v.startswith('lỗi 3 lần'): return 'Lỗi 3 lần', RED, ERROR_BG
    if v.startswith('lỗi tải'): return 'Lỗi tải video', WARNING, WARNING_BG
    if 'bị chặn' in v: return 'Bị chặn', RED, ERROR_BG
    if 'lỗi' in v: return 'Lỗi', RED, ERROR_BG
    if 'hoàn thành' in v: return 'Xong', GREEN, SUCCESS_BG
    if 'hủy' in v or 'gián đoạn' in v or v.startswith('đã dừng'): return 'Đã dừng', WARNING, WARNING_BG
    if v.startswith(('chờ xử lý', 'chờ chạy', 'chưa')): return 'Chờ chạy', MUTED, BORDER
    if 'ffmpeg' in v or 'xuất video' in v: return 'Xuất video', CYAN, ACTIVE_BG
    return 'Đang chạy', CYAN, ACTIVE_BG


# --------------------------------------------------------------------------- model
def _role_int(role):
    return int(getattr(role, 'value', role))


_R_DISPLAY = _role_int(Qt.DisplayRole)
_R_FOREGROUND = _role_int(Qt.ForegroundRole)
_R_ALIGN = _role_int(Qt.TextAlignmentRole)
_R_STATUS = _role_int(ROLE_STATUS)
_R_PROGRESS = _role_int(ROLE_PROGRESS)
_USED_ROLES = frozenset({_R_DISPLAY, _R_FOREGROUND, _R_ALIGN, _R_STATUS, _R_PROGRESS})
_CENTER = _role_int(Qt.AlignCenter | Qt.AlignVCenter)


class TaskModel(QAbstractTableModel):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.tasks = app.tasks  # cùng tham chiếu danh sách với controller
        self._rows = {}         # id task -> giá trị hiển thị đã tính (tính lại khi task đổi)
        self._index = {}        # id task -> số dòng
        self._fg = {4: QColor(GREEN), 5: QColor(CYAN)}
        self._text = QColor(TEXT)

    def rowCount(self, parent=QModelIndex()): return 0 if parent.isValid() else len(self.tasks)
    def columnCount(self, parent=QModelIndex()): return len(COLS)

    def headerData(self, section, orient, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orient == Qt.Horizontal:
            return COLS[section]
        return None

    def refresh(self):
        self.beginResetModel()
        self.tasks = self.app.tasks
        self._rows.clear(); self._index.clear()
        self.endResetModel()

    def _row_of(self, tid):
        row = self._index.get(tid)
        if row is None or row >= len(self.tasks) or self.tasks[row].get('id') != tid:
            self._index = {t.get('id'): i for i, t in enumerate(self.tasks)}
            row = self._index.get(tid)
        return row

    def touch(self, tid):
        self._rows.pop(tid, None)
        row = self._row_of(tid)
        if row is not None:
            self.dataChanged.emit(self.index(row, 0), self.index(row, len(COLS) - 1))

    def _values(self, task, row):
        """Chữ của 9 cột, tính một lần cho mỗi lần task đổi (kể cả kiểm tra file video trên ổ đĩa)."""
        cached = self._rows.get(task.get('id'))
        if cached is not None:
            return cached
        cfg = task_settings(self.app.settings, task)
        ex = task.get('execution', {}) or {}
        account_id = ex.get('account_id') or task.get('conversation_account')
        acc = next((a for a in self.app.accounts if a.get('id') == account_id), {}) if account_id else {}
        name = task.get('name', '')
        path = task.get('output_path', '')
        proxy = ex.get('proxy_server', '')
        if not proxy and ex and acc:
            from proxyvn import resolve_proxy_server
            proxy = resolve_proxy_server(self.app.settings, acc)
        values = (
            str(task.get('stt') or (name.split('#')[-1] if '#' in name else row + 1)),
            '',
            str(cfg.get('duration', '—')) + ' giây',
            cfg.get('model', '—'),
            ex.get('account_name') or acc.get('name') or 'Chưa gán',
            (proxy or 'Không proxy') if ex else '—',
            Path(path).name if path and Path(path).is_file() else '—',
            ' '.join(str(task.get('prompt') or 'Chưa có prompt').split()),
            '',
        )
        self._rows[task.get('id')] = values
        return values

    def data(self, index, role=Qt.DisplayRole):
        # Qt gọi hàm này nhiều lần cho mỗi ô mỗi lần vẽ: so sánh số nguyên, trả về sớm các vai trò không dùng.
        role = role if type(role) is int else _role_int(role)
        if role not in _USED_ROLES:
            return None
        row = index.row()
        if row < 0 or row >= len(self.tasks):
            return None
        task = self.tasks[row]
        if role == _R_DISPLAY:
            return self._values(task, row)[index.column()]
        if role == _R_FOREGROUND:
            return self._fg.get(index.column(), self._text)
        if role == _R_ALIGN:
            return _CENTER if index.column() in (0, 2) else None
        if role == _R_STATUS:
            return task.get('status', 'Chờ xử lý')
        label = badge_for(task.get('status', ''))[0]
        return 'done' if label == 'Xong' else ('active' if label in ('Đang chạy', 'Xuất video') else 'idle')


class BadgeDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        text, fg, bg = badge_for(index.data(ROLE_STATUS))
        painter.save(); painter.setRenderHint(QPainter.Antialiasing)
        r = option.rect.adjusted(6, 5, -6, -5)
        w = min(r.width(), option.fontMetrics.horizontalAdvance(text) + 20)
        pill = QRect(r.left(), r.top(), w, r.height())
        painter.setPen(Qt.NoPen); painter.setBrush(QColor(bg))
        painter.drawRoundedRect(pill, pill.height() / 2, pill.height() / 2)
        painter.setPen(QColor(fg)); painter.drawText(pill, Qt.AlignCenter, text)
        painter.restore()


class ProgressDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        state = index.data(ROLE_PROGRESS) or 'idle'
        painter.save(); painter.setRenderHint(QPainter.Antialiasing)
        r = option.rect.adjusted(8, 0, -8, 0)
        track = QRect(r.left(), r.center().y() - 2, r.width(), 5)
        painter.setPen(Qt.NoPen); painter.setBrush(QColor(BORDER))
        painter.drawRoundedRect(track, 2.5, 2.5)
        frac = 1.0 if state == 'done' else (0.5 if state == 'active' else 0.0)
        if frac:
            fill = QRect(track.left(), track.top(), int(track.width() * frac), track.height())
            painter.setBrush(QColor(GREEN if state == 'done' else CYAN))
            painter.drawRoundedRect(fill, 2.5, 2.5)
        painter.restore()


# --------------------------------------------------------------------------- app
class ComboWheelGuard(QObject):
    """Ô sổ xuống (và ô số) chỉ đổi giá trị khi bấm mở rồi chọn: lăn chuột lên ô đang đóng
    không đổi lựa chọn mà cuộn trang/hộp thoại chứa nó, tránh chọn nhầm khi lướt chuột."""
    def eventFilter(self, obj, event):
        if event.type() != QEvent.Wheel or not isinstance(obj, (QComboBox, QAbstractSpinBox)):
            return False
        area = obj.parentWidget()
        while area is not None and not isinstance(area, QAbstractScrollArea):
            area = area.parentWidget()
        if area is not None:
            bar = area.verticalScrollBar()
            bar.setValue(bar.value() - int(event.angleDelta().y() / 120 * bar.singleStep() * 3))
        return True


class Studio(QMainWindow):
    def __init__(self):
        super().__init__()
        self._alive = True
        self._timers = set()
        self.save_timer = None
        self.workspace_writer = WorkspaceWriter(
            DATA / 'workspace.json', on_error=lambda exc: self.events.put(('log', f'Không lưu được cấu hình: {exc}')))
        # Ô sổ xuống chỉ đổi khi bấm chọn; lăn chuột trên ô không đổi lựa chọn (tránh thao tác nhầm).
        self._wheel_guard = ComboWheelGuard(self)
        QApplication.instance().installEventFilter(self._wheel_guard)
        self.setWindowTitle('Seedance Auto')
        self.resize(1280, 860)
        self.settings = defaults()
        self.tasks, self.accounts, self.hidden = [fresh_task()], [], []
        self.load_error = ''
        self.load()
        if not self.accounts and self.settings.get('import_on_start', True):
            self.accounts = discover_accounts()
        self.settings['import_on_start'] = False
        for account in self.accounts:
            if not account.get('profile'):
                original_profile = ORIGINAL / 'accounts' / account['name']
                account['profile'] = str(original_profile if original_profile.is_dir() else DATA / 'profiles' / account['id'])
            account.setdefault('connection', 'Chưa kiểm tra')
            account.setdefault('session_verified_at', 0)
        self.settings['manual_proxy_slots'] = assign_manual_proxies(self.settings, self.accounts)
        self.account_jobs = {}
        self.account_check_executor = ThreadPoolExecutor(max_workers=3)
        self.active = self.tasks[0]['id'] if self.tasks else None
        self.events = queue.Queue()
        self.cancel_event = threading.Event()
        self.running = False
        self.task_cancels = {}
        self.running_task_ids = set()
        self.fetching_ids = set()
        self.pending_task_ids = []
        self.live_intake = None
        self.live_open = False
        self.live_lock = threading.Lock()
        self.batch_cfg = None
        self.batch_totals = None
        self.batch_lock = threading.Lock()
        self.save_timer = None
        self._busy = set()
        self.gallery_generation = 0
        self._accounts_paint = None
        self._batch_preparing = False
        self.auto_runner = None
        self._auto_retry_job = None
        self.account_redraw = None
        self.view_mode = 'tasks'
        self.visible_videos = []
        self.result_view = _Var('Tác vụ')
        self.task_log_files = TaskLogFiles(DATA / 'task_logs')
        self.log_task_id = None
        self.proxy_keys = []
        self.build()
        self.refresh_task_table()
        self.draw_accounts()
        # Ghi file nhật ký task ở luồng nền mỗi giây (không chặn giao diện).
        self._task_log_stop = self.task_log_files.start_flusher(1.0, lambda: self.events.put(
            ('log', 'Không ghi được file nhật ký task vào thư mục data\\task_logs (kiểm tra quyền ghi).')))
        self._proxy_timer = QTimer(self); self._proxy_timer.timeout.connect(self.draw_proxy_status)
        self._proxy_timer.start(1000)
        self.apply_proxy_rotation()
        self.check_license()
        self._license_timer = QTimer(self); self._license_timer.timeout.connect(self.check_license)
        self._license_timer.start(300000)
        self.log('Chào mừng tới Seedance AI Studio • giao diện Qt6 (mượt cho bảng lớn).')
        self.log('Chọn tài khoản và nhập prompt để chạy.')
        if self.load_error:
            self.log(self.load_error)
        self.after(100, self.poll_events)
        self.release_credit_locks()
        self.later_save()

    @property
    def account_by_id(self):
        return {a.get('id'): a for a in self.accounts}

    # ---- lớp tương thích với Tk API để chép logic điều phối gần như nguyên vẹn
    def after(self, ms, fn):
        t = QTimer(self); t.setSingleShot(True)
        def run():
            self._timers.discard(t)
            fn()
        t.timeout.connect(run); self._timers.add(t); t.start(int(ms))
        return t

    def after_cancel(self, t):
        try:
            t.stop(); self._timers.discard(t)
        except Exception:
            pass

    def winfo_exists(self):
        return self._alive

    def info(self, title, msg): QMessageBox.information(self, title, msg)
    def error(self, title, msg): QMessageBox.critical(self, title, msg)
    def ask(self, title, msg):
        return QMessageBox.question(self, title, msg) == QMessageBox.Yes

    # ---- lưu / nạp ----------------------------------------
    def load(self):
        path = DATA / 'workspace.json'
        if not path.exists():
            return
        try:
            import json
            data = json.loads(path.read_text(encoding='utf-8'))
            stored = data.get('settings', {})
            self.settings.update(stored)
            if stored.get('extension_bundle_version', 0) < 2:
                self.settings['extension_bundle_version'] = 0
            self.settings.setdefault('proxyno1_keys_protected', '')
            # Nhà cung cấp cũ (ProxyVN) → ProxyNo1. Key ProxyVN cũ để nguyên, không dùng nữa.
            self.settings['proxy_source'] = proxy_source(self.settings)
            from seedance_browser import migrate_settings
            migrate_settings(self.settings, data.get('accounts', []), DATA)
            self.settings.pop('video_extension', None)
            self.tasks = data.get('tasks', [fresh_task()])
            if not isinstance(self.tasks, list):
                raise ValueError('Danh sách task không hợp lệ.')
            self.accounts = data.get('accounts', [])
            self.hidden = data.get('hidden', [])
            for task in self.tasks:
                if task.get('status', '').startswith(('Đang', 'Chờ chạy')):
                    task['status'] = 'Gián đoạn • lấy kết quả cũ hoặc chạy mới'
        except (ValueError, KeyError, OSError, TypeError):
            backup = path.with_name(f'workspace_error_{int(time.time())}.json')
            try: shutil.copy2(path, backup)
            except OSError: pass
            self.load_error = 'Cấu hình cũ bị lỗi; đã giữ bản sao trước khi tạo cấu hình mới.'
            self.settings, self.tasks, self.accounts, self.hidden = defaults(), [fresh_task()], [], []

    def save(self):
        """Chụp nhanh dữ liệu (không kèm nhật ký task) rồi để luồng nền ghi workspace.json."""
        self.settings['manual_proxy_slots'] = assign_manual_proxies(self.settings, self.accounts)
        if self.save_timer:
            self.after_cancel(self.save_timer)
        self.save_timer = None
        try:
            text = workspace_snapshot(self.settings, self.tasks, self.accounts, self.hidden)
        except (TypeError, ValueError) as exc:
            self.log(f'Không lưu được cấu hình: {exc}'); return
        self.workspace_writer.submit(text)

    def later_save(self, *_):
        # Gom thay đổi: lưu tối đa ~0,6 giây một lần; sự kiện dồn dập không làm lùi lịch lưu mãi.
        if self.save_timer:
            return
        self.save_timer = self.after(600, self.save)

    def post_ui(self, callback):
        self.events.put(('call', callback))

    def run_background(self, work):
        threading.Thread(target=work, daemon=True).start()

    def release_credit_locks(self):
        res = release_account_locks(self.accounts)
        changed = False
        if res.get('credits'):
            names = ', '.join(a.get('name', '') for a in res['credits'])
            self.log(f'Qua 0h • mở lại tài khoản hết điểm: {names}')
            changed = True
        if res.get('work'):
            names = ', '.join(a.get('name', '') for a in res['work'])
            self.log(f'Mở lại chế độ Work: {names}')
            changed = True
        if res.get('rate_limits'):
            names = ', '.join(a.get('name', '') for a in res['rate_limits'])
            self.log(f'Hết Rate Limit • mở lại tài khoản: {names}')
            changed = True
        if res.get('cooldowns'):
            names = ', '.join(a.get('name', '') for a in res['cooldowns'])
            self.log(f'Hết 1 tuần nghỉ • mở lại Dùng chạy cho: {names}')
            changed = True
        if changed:
            self.later_save(); self.request_accounts_paint()
            recovered = []
            if res.get('rate_limits'):
                recovered.append(f"Rate Limit ({len(res['rate_limits'])})")
            if res.get('work'):
                recovered.append(f"Work ({len(res['work'])})")
            if res.get('credits'):
                recovered.append(f"Điểm ({len(res['credits'])})")
            if recovered and hasattr(self, 'progress_label') and not self.running:
                self.progress_label.setText(f"✓ Đã hồi phục: {', '.join(recovered)} • sẵn sàng")
        self.after(15000, self.release_credit_locks)

    def open_dola_chat(self, target_account=None):
        if self.running:
            self.info('Đang chạy', 'Hệ thống đang chạy batch task. Vui lòng đợi batch kết thúc hoặc mở sau.')
            return
        if target_account is not None:
            account = target_account
        else:
            account, _ = select_best_account(self.accounts, mode='chat')
            if not account:
                earliest = get_earliest_recovery_time(self.accounts, mode='chat')
                msg = 'Hiện không có tài khoản Dola nào khả dụng để Chat.'
                if earliest:
                    msg += f'\nTài khoản có thể hồi phục sớm nhất vào lúc: {unlock_text(earliest)}'
                else:
                    msg += '\nVui lòng thêm tài khoản mới hoặc kiểm tra lại danh sách tài khoản.'
                self.info('Không có tài khoản khả dụng', msg)
                return
        self.log(f"💬 Mở Chat Dola bằng tài khoản: {account.get('name', 'profile')}")
        self.run_account_action(account, login=True, url='https://www.dola.com/chat')

    def _report_auto_job_done(self, task, path):
        def work():
            try:
                from auto_jobs import JobsClient, ReportJournal
                from cookie_accounts import protect
                cfg = self.settings
                p_key = cfg.get('auto_render_key_protected')
                if not p_key:
                    return
                client = JobsClient(protect(p_key, decrypt=True), machine_name=cfg.get('auto_machine_code', ''))
                final_path = Path(path).resolve()
                item = {
                    'job_id': task['task_code'],
                    'status': 'done',
                    'folder': final_path.parent.as_posix(),
                    'path': final_path.as_posix()
                }
                reported = False
                if task.get('lease'):
                    item['lease'] = task['lease']
                    try:
                        res = client.report([item])
                        if res and res[0].get('ok'):
                            reported = True
                    except Exception:
                        pass
                if not reported:
                    legacy_item = {'task_code': task['task_code'], 'status': 'done',
                                   'folder': final_path.parent.as_posix(), 'path': final_path.as_posix()}
                    res = client.legacy_report(legacy_item)
                    if res and res.get('ok'):
                        reported = True
                if reported:
                    journal_file = DATA / 'auto_jobs_v2.json'
                    if journal_file.exists():
                        j = ReportJournal(journal_file)
                        if task['id'] in j.rows:
                            j.finish(task['id'], 'done', folder=final_path.parent.as_posix(), path=final_path.as_posix())
                            j.acknowledge(task['id'])
                    self.events.put(('auto_report', task['id'], 'done'))
                    self.events.put(('status', task['id'], '✓ Hoàn thành • đã báo API'))
                    self.events.put(('log', f"STT {task.get('stt')} • {task.get('task_code')}: đã báo hoàn thành lên web."))
            except Exception as exc:
                self.events.put(('log', f"Báo kết quả web: {exc}"))
        self.run_background(work)

    def request_accounts_paint(self):
        if self._accounts_paint is not None:
            return
        self._accounts_paint = self.after(400, self._paint_accounts)

    def _paint_accounts(self):
        self._accounts_paint = None
        if not self._alive:
            return
        self.draw_accounts()
        if self.account_redraw:
            try: self.account_redraw()
            except Exception: self.account_redraw = None

    # ---- xây giao diện (NHẬP PROMPT RA VIDEO + QUẢN LÝ TÀI KHOẢN + AUTO) -------
    def _hidden(self, widget):
        """Giữ một widget chỉ để các phần code khác tham chiếu (vd nút cũ) nhưng không hiện ra."""
        widget.hide()
        self._hidden_widgets.append(widget)   # giữ tham chiếu để Qt không thu hồi
        return widget

    def build(self):
        self.setStyleSheet(STYLE)
        self._hidden_widgets = []
        self.nav_buttons = {}
        central = QWidget(); self.setCentralWidget(central)
        root = QVBoxLayout(central); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)

        # Thanh tiêu đề
        top = QFrame(); top.setObjectName('topbar')
        tl = QHBoxLayout(top); tl.setContentsMargins(20, 12, 20, 12); tl.setSpacing(12)
        logo = QLabel('▣'); logo.setObjectName('logo'); tl.addWidget(logo)
        titles = QVBoxLayout(); titles.setSpacing(2)
        line = QHBoxLayout(); line.setSpacing(10)
        title = QLabel('Seedance AI Studio'); title.setObjectName('apptitle'); line.addWidget(title)
        line.addWidget(_pill('PROMPT → VIDEO • QUẢN LÝ TÀI KHOẢN', TEXT, ACCENT), 0, Qt.AlignVCenter); line.addStretch(1)
        titles.addLayout(line)
        titles.addWidget(_muted('Nhập prompt (1 hoặc nhiều dòng) để tạo video Dola AI • quản lý tài khoản, cookie & proxy'))
        tl.addLayout(titles, 1)
        self.header_status = _pill('● Sẵn sàng', GREEN, SUCCESS_BG); tl.addWidget(self.header_status, 0, Qt.AlignVCenter)
        root.addWidget(top)
        outer = QHBoxLayout(); outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(0)
        root.addLayout(outer, 1)

        # Sidebar
        side = QWidget(); side.setObjectName('side'); side.setFixedWidth(236)
        sl = QVBoxLayout(side); sl.setContentsMargins(14, 18, 14, 14); sl.setSpacing(4)
        brand = QLabel('✦  SEEDANCE'); brand.setObjectName('brand'); sl.addWidget(brand)
        sub = QLabel('AI STUDIO'); sub.setObjectName('brandsub'); sl.addWidget(sub)
        def nav(section, items):
            sl.addSpacing(10)
            head = QLabel(section); head.setObjectName('navsection'); sl.addWidget(head)
            for key, text, fn in items:
                b = QPushButton(text); b.setObjectName('nav'); b.setCursor(Qt.PointingHandCursor)
                b.clicked.connect(fn); sl.addWidget(b); self.nav_buttons[key] = b
        nav('QUẢN LÝ', [('accounts', '♟   Tài khoản', self.account_dialog),
                        ('chat_dola', '💬   Mở Chat Dola', self.open_dola_chat),
                        ('logs', '◒   Nhật ký', self.toggle_logs)])
        self.account_card = AccountSummary(self.account_dialog); sl.addWidget(self.account_card)
        self.proxy_card = ProxySummary(self.settings_dialog); sl.addWidget(self.proxy_card)
        nav('TIỆN ÍCH', [('settings', '⚙   Cài đặt', self.settings_dialog),
                         ('folder', '▱   Thư mục video', self.open_output)])
        sl.addStretch(1)
        outer.addWidget(side)

        # Main
        main = QWidget(); main.setObjectName('main')
        ml = QVBoxLayout(main); ml.setContentsMargins(18, 14, 18, 10); ml.setSpacing(8)

        # Khung nhập prompt nhanh + thông số mặc định
        prompt_card = QFrame(); prompt_card.setObjectName('tablecard')
        pl = QVBoxLayout(prompt_card); pl.setContentsMargins(12, 10, 12, 10); pl.setSpacing(8)
        phead = QHBoxLayout(); phead.setSpacing(8)
        t = QLabel('NHẬP PROMPT TẠO VIDEO'); t.setObjectName('h1'); phead.addWidget(t)
        phead.addStretch(1)
        phead.addWidget(_muted('Model:'))
        self.quick_model = QComboBox(); self.quick_model.addItems(['Seedance 2.0', 'Seedance 2.5'])
        phead.addWidget(self.quick_model)
        phead.addWidget(_muted('Thời lượng:'))
        self.quick_duration = QComboBox()
        self.quick_duration_values = {
            '5s': '5',
            '10s': '10',
            '15s · một video': '15',
            '30s · ghép 2 × 15s': '30',
        }
        self.quick_duration.addItems(list(self.quick_duration_values))
        phead.addWidget(self.quick_duration)
        phead.addWidget(_muted('Tỉ lệ:'))
        self.quick_ratio = QComboBox(); self.quick_ratio.addItems(['9:16', '16:9', '1:1', '4:3', '3:4'])
        phead.addWidget(self.quick_ratio)
        phead.addWidget(_muted('Chế độ:'))
        self.quick_mode = QComboBox()
        self.quick_mode_values = {'Pro': 'pro', 'Fast': 'fast', 'Skill 30s': 'skill30'}
        self.quick_mode.addItems(list(self.quick_mode_values))
        phead.addWidget(self.quick_mode)
        self.sync_quick_settings_ui()
        self.quick_model.currentTextChanged.connect(self.save_quick_settings)
        self.quick_duration.currentTextChanged.connect(self.save_quick_settings)
        self.quick_ratio.currentTextChanged.connect(self.save_quick_settings)
        self.quick_mode.currentTextChanged.connect(self.save_quick_settings)
        pl.addLayout(phead)

        pbody = QHBoxLayout(); pbody.setSpacing(8)
        self.quick_prompt = QPlainTextEdit()
        self.quick_prompt.setPlaceholderText(
            'Nhập prompt mô tả video tại đây… (Gõ 1 prompt hoặc dán nhiều dòng — mỗi dòng tạo 1 task riêng; '
            'đúp chuột vào dòng trong bảng để sửa prompt hoặc đính kèm ảnh tham chiếu)')
        self.quick_prompt.setFixedHeight(74)
        pbody.addWidget(self.quick_prompt, 1)
        pbtns = QVBoxLayout(); pbtns.setSpacing(4)
        self.quick_run_button = _btn('▶ Tạo video ngay', self.add_and_run_quick_task, primary=True)
        self.add_lines_button = _btn('+ Thêm vào bảng', self.add_quick_task)
        self.add_with_img_button = _btn('+ Prompt kèm ảnh…', self.add_task_with_dialog)
        for b in (self.quick_run_button, self.add_lines_button, self.add_with_img_button):
            pbtns.addWidget(b)
        pbody.addLayout(pbtns)
        pl.addLayout(pbody)
        ml.addWidget(prompt_card)

        # Thanh nút điều khiển task & batch
        head = QHBoxLayout(); head.setSpacing(6)
        self.run_all = _btn('▶ Chạy tất cả', self.run_all_tasks, primary=True); head.addWidget(self.run_all)
        self.run_selected_button = _btn('▶ Chạy đã chọn', self.run_selected_tasks); head.addWidget(self.run_selected_button)
        self.stop_button = _btn('■ Dừng', self.cancel_batch, color=RED); self.stop_button.setEnabled(False); head.addWidget(self.stop_button)
        self.bulk_config_button = _btn('Cấu hình đã chọn', self.configure_selected_tasks); head.addWidget(self.bulk_config_button)
        self.select_failed_button = _btn('Chọn task lỗi', self.select_failed_tasks, color=RED); head.addWidget(self.select_failed_button)
        head.addWidget(_btn('Xóa đã xong', self.delete_done_tasks))
        self.delete_selected_button = _btn('Xóa đã chọn', self.delete_selected_tasks, color=RED); head.addWidget(self.delete_selected_button)
        head.addStretch(1)
        self.auto_button = _btn('Chạy Auto', self.toggle_auto); head.addWidget(self.auto_button)
        ml.addLayout(head)

        checks = QHBoxLayout()
        self.cb_show_browser = _check('Hiện cửa sổ Chrome khi chạy', self.settings.get('show_browser', True), self.save_browser_visibility)
        self.cb_extension = _check('Nạp extension Seedance Studio Pro khi mở Chrome', self.settings.get('load_extension', True), self.save_extension_choice)
        self.cb_auto_retry = _check('Tự chạy lại task lỗi', self.settings.get('auto_retry', False), self.save_auto_retry)
        for c in (self.cb_show_browser, self.cb_extension, self.cb_auto_retry): checks.addWidget(c)
        checks.addStretch(1)
        checks.addWidget(_muted('Thời lượng Auto:'))
        self.auto_duration = QComboBox()
        self.auto_duration_values = {
            'Theo job web (15s và 30s)': 'web',
            'Luôn 15s · một video': '15',
            'Luôn 30s · ghép 2 × 15s': '30',
        }
        self.auto_duration.addItems(self.auto_duration_values)
        selected_duration = str(self.settings.get('auto_duration_policy', 'web'))
        self.auto_duration.setCurrentText(next((label for label, value in self.auto_duration_values.items()
                                                if value == selected_duration), next(iter(self.auto_duration_values))))
        self.auto_duration.currentTextChanged.connect(self.save_auto_duration)
        checks.addWidget(self.auto_duration)
        ml.addLayout(checks)

        # Bảng task + nhật ký của task đang chọn
        self.model = TaskModel(self)
        self.view = QTableView(); self.view.setModel(self.model)
        self.view.setItemDelegateForColumn(1, BadgeDelegate(self.view))
        self.view.setItemDelegateForColumn(8, ProgressDelegate(self.view))
        self.view.verticalHeader().setVisible(False)
        self.view.verticalHeader().setDefaultSectionSize(34)
        self.view.setShowGrid(False)
        self.view.setAlternatingRowColors(True)
        self.view.setSelectionBehavior(QTableView.SelectRows)
        self.view.setSelectionMode(QTableView.ExtendedSelection)
        self.view.setEditTriggers(QTableView.NoEditTriggers)
        self.view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._row_menu)
        self.view.doubleClicked.connect(self._on_row_double_clicked)
        for col, w in {0: 54, 1: 150, 2: 78, 3: 130, 4: 170, 5: 140, 6: 90, 8: 150}.items():
            self.view.setColumnWidth(col, w)
        self.view.horizontalHeader().setSectionResizeMode(7, QHeaderView.Stretch)
        self.view.selectionModel().currentRowChanged.connect(self._follow_selected_task)
        log_panel = QWidget(); lp = QVBoxLayout(log_panel); lp.setContentsMargins(0, 6, 0, 0); lp.setSpacing(4)
        log_head = QHBoxLayout()
        self.task_log_title = QLabel('NHẬT KÝ TASK • chọn một dòng trong bảng để xem • đúp chuột vào dòng để sửa prompt'); self.task_log_title.setObjectName('navsection')
        log_head.addWidget(self.task_log_title, 1)
        log_head.addWidget(_btn('Mở file log', lambda: self.open_task_log_file()))
        log_head.addWidget(_btn('Thư mục log', self.open_task_log_folder))
        lp.addLayout(log_head)
        self.task_log_box = QPlainTextEdit(); self.task_log_box.setReadOnly(True); self.task_log_box.setObjectName('logbox')
        self.task_log_box.setMaximumBlockCount(2000)
        lp.addWidget(self.task_log_box)
        split = QSplitter(Qt.Vertical); split.setChildrenCollapsible(False)
        split.addWidget(self.view); split.addWidget(log_panel); split.setSizes([560, 160])
        card = QFrame(); card.setObjectName('tablecard')
        cl = QVBoxLayout(card); cl.setContentsMargins(10, 10, 10, 10); cl.addWidget(split)
        ml.addWidget(card, 1)

        self.progress = QProgressBar(); self.progress.setMaximum(1000); self.progress.setValue(0)
        self.progress.setTextVisible(False); self.progress.setFixedHeight(5); ml.addWidget(self.progress)
        bottom = QHBoxLayout()
        self.progress_label = _muted('Sẵn sàng'); bottom.addWidget(self.progress_label)
        bottom.addStretch(1)
        self.video_count = QLabel('0 task'); self.video_count.setObjectName('ok'); bottom.addWidget(self.video_count)
        self.status_label = QLabel('● Sẵn sàng'); self.status_label.setObjectName('ok'); bottom.addWidget(self.status_label)
        ml.addLayout(bottom)
        outer.addWidget(main, 1)

        # Nhật ký tiến trình (dock ẩn, mở bằng nav Nhật ký)
        self.log_box = QPlainTextEdit(); self.log_box.setReadOnly(True); self.log_box.setObjectName('logbox')
        self.log_box.setMaximumBlockCount(3000)
        self.log_dock = QDockWidget('Nhật ký tiến trình', self)
        self.log_dock.setWidget(self.log_box)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.log_dock)
        self.log_dock.hide()

        # Các widget tương thích ngược
        self.sheet_auto_button = self._hidden(QPushButton())
        self.sheet_fetch_button = self._hidden(QPushButton())
        self.gallery = self._hidden(QListWidget())
        self.view_combo = self._hidden(QComboBox()); self.view_combo.addItem('Tác vụ')
        self.stack = self._hidden(QStackedWidget())

    # ---- đồng bộ thông số tạo video nhanh -------------------------------------
    def sync_quick_settings_ui(self):
        if not hasattr(self, 'quick_model'):
            return
        s = self.settings
        for w in (self.quick_model, self.quick_duration, self.quick_ratio, self.quick_mode):
            w.blockSignals(True)
        model = str(s.get('model', 'Seedance 2.0'))
        if model in ('Seedance 2.0', 'Seedance 2.5'):
            self.quick_model.setCurrentText(model)
        dur = str(s.get('duration', '15')).rstrip('s')
        dur_label = next((k for k, v in self.quick_duration_values.items() if v == dur), '15s · một video')
        self.quick_duration.setCurrentText(dur_label)
        ratio = str(s.get('ratio', '9:16'))
        self.quick_ratio.setCurrentText(ratio)
        mode = str(s.get('dola_mode', 'pro')).lower()
        mode_label = next((k for k, v in self.quick_mode_values.items() if v == mode), 'Pro')
        self.quick_mode.setCurrentText(mode_label)
        for w in (self.quick_model, self.quick_duration, self.quick_ratio, self.quick_mode):
            w.blockSignals(False)

    def save_quick_settings(self):
        self.settings['model'] = self.quick_model.currentText()
        self.settings['duration'] = self.quick_duration_values.get(self.quick_duration.currentText(), '15')
        self.settings['ratio'] = self.quick_ratio.currentText()
        self.settings['dola_mode'] = self.quick_mode_values.get(self.quick_mode.currentText(), 'pro')
        self.refresh_task_table()
        self.later_save()

    # ---- điều hướng view ------------------------------------------------------
    def set_view(self, mode):
        pass

    def _view_changed(self, text):
        pass

    def toggle_logs(self):
        self.log_dock.setVisible(not self.log_dock.isVisible())

    def show_task_monitor(self):
        self.refresh_task_table()

    # ---- bảng task ------------------------------------------------------------
    def selected_ids(self):
        rows = sorted({i.row() for i in self.view.selectionModel().selectedRows()})
        return [self.tasks[r]['id'] for r in rows if r < len(self.tasks)]

    def refresh_task_table(self):
        self.model.refresh()
        if self.result_view.get() == 'Tác vụ':
            done = sum('hoàn thành' in t.get('status', '').casefold() for t in self.tasks)
            self.video_count.setText(f'{len(self.tasks)} task • {done} xong')

    def update_task_row(self, task):
        self.model.touch(task['id'])
        # Đếm lại "N task • M xong" tối đa 2 lần/giây thay vì mỗi cập nhật (đếm cả bảng rất tốn khi bảng lớn).
        if getattr(self, '_count_timer', None) is None:
            self._count_timer = self.after(500, self._update_task_count)

    def _update_task_count(self):
        self._count_timer = None
        if self.result_view.get() == 'Tác vụ':
            done = sum('hoàn thành' in t.get('status', '').casefold() for t in self.tasks)
            self.video_count.setText(f'{len(self.tasks)} task • {done} xong')

    def set_busy(self, ids):
        self._busy = set(ids)

    def select_failed_tasks(self):
        sm = self.view.selectionModel(); sm.clearSelection()
        sel = QItemSelection()
        for r, t in enumerate(self.tasks):
            if badge_for(t.get('status', ''))[0] in FAILED_BADGES:
                sel.select(self.model.index(r, 0), self.model.index(r, len(COLS) - 1))
        sm.select(sel, QItemSelectionModel.Select)
        self.log(f'Đã chọn {len(self.selected_ids())} task lỗi.')

    def _on_row_double_clicked(self, index):
        if not index.isValid() or index.row() >= len(self.tasks):
            return
        task = self.tasks[index.row()]
        tid = task['id']
        col = index.column()
        if col == 6 and task.get('output_path') and Path(task['output_path']).is_file():
            self._open_task_video(tid)
        elif col in (2, 3):
            self.configure_table_task(tid)
        else:
            self.edit_table_task(tid)

    def _row_menu(self, pos):
        idx = self.view.indexAt(pos)
        if not idx.isValid() or idx.row() >= len(self.tasks):
            return
        task = self.tasks[idx.row()]
        tid = task['id']
        busy = tid in self.busy_table_ids()
        has_video = bool(task.get('output_path') and Path(task['output_path']).is_file())
        has_conv = bool(task.get('conversation_url'))
        m = QMenu(self)
        if not busy:
            m.addAction('▶ Chạy task này', lambda: self.run_table_task(tid))
        else:
            m.addAction('■ Dừng task này', lambda: self.stop_table_task(tid))
        m.addAction('✎ Sửa prompt / ảnh tham chiếu…', lambda: self.edit_table_task(tid))
        m.addAction('⚙ Cấu hình thông số task…', lambda: self.configure_table_task(tid))
        if has_conv:
            m.addSeparator()
            m.addAction('↻ Lấy video từ hội thoại đã lưu', lambda: self.fetch_table_video(tid))
            m.addAction('↗ Mở hội thoại Dola', lambda: self.open_task_conversation(tid))
        if has_video:
            m.addSeparator()
            m.addAction('▶ Mở video', lambda: self._open_task_video(tid))
            m.addAction('📂 Hiện trong thư mục', lambda: self._reveal_task_video(tid))
            m.addAction('⇄ Chuyển video sang thư mục khác…', lambda: self.move_task_video_dialog(tid))
        m.addSeparator()
        m.addAction('Xem nhật ký', lambda: self.show_task_log(tid))
        m.addAction('Mở file nhật ký', lambda: self.open_task_log_file(tid))
        m.addSeparator()
        if tid not in self.selected_ids():
            self.view.selectRow(idx.row())
        m.addAction('Xóa khỏi bảng', self.delete_selected_tasks)
        m.exec(self.view.viewport().mapToGlobal(pos))

    def _task(self, tid):
        model = getattr(self, 'model', None)
        if model is not None and model.tasks is self.tasks:
            row = model._row_of(tid)   # tra thẳng theo id, không duyệt cả bảng
            return self.tasks[row] if row is not None else None
        return next((t for t in self.tasks if t['id'] == tid), None)

    def open_task_conversation(self, tid):
        task = self._task(tid)
        if not task:
            return
        url = task.get('conversation_url', '')
        if re.fullmatch(r'https://www\.dola\.com/chat/\d+', url):
            account = next((a for a in self.accounts if a['id'] == task.get('conversation_account')), None)
            if account:
                self.run_account_action(account, True, url)
            else:
                self.info('Thiếu tài khoản', 'Tài khoản đã tạo hội thoại này không còn trong danh sách.')
        else:
            self.info('Chưa có hội thoại', 'Hội thoại sẽ được lưu sau khi gửi prompt lên Dola.')

    def open_video(self, path):
        try:
            os.startfile(Path(path))
        except OSError as exc:
            self.error('Không mở được video', str(exc))

    def reveal_video(self, path):
        path = Path(path).resolve()
        try:
            subprocess.Popen(['explorer', f'/select,{path}'])
        except OSError:
            try:
                os.startfile(path.parent)
            except OSError as exc:
                self.error('Không mở được thư mục', str(exc))

    def _open_task_video(self, tid):
        t = self._task(tid); p = (t or {}).get('output_path', '')
        if p and Path(p).is_file(): self.open_video(Path(p))

    def _reveal_task_video(self, tid):
        t = self._task(tid); p = (t or {}).get('output_path', '')
        if p and Path(p).is_file(): self.reveal_video(Path(p))

    # ---- thao tác task cơ bản -------------------------------------------------
    def task(self):
        return next((t for t in self.tasks if t['id'] == self.active), None)

    def editable(self):
        if self.running:
            self.info('Đang chạy', 'Hãy chờ hoàn tất hoặc hủy batch trước khi thay đổi danh sách task.')
            return False
        return True

    def _is_untouched_placeholder(self, task):
        return (not str(task.get('prompt', '')).strip()
                and not (task.get('images') or [])
                and not task.get('output_path')
                and not task.get('conversation_url')
                and not task.get('auto_job')
                and task.get('id') not in self.busy_table_ids()
                and task.get('status', 'Chờ xử lý') in ('Chờ xử lý', ''))

    def add_task(self, prompt='', options=None):
        if prompt.strip() and len(self.tasks) == 1 and self._is_untouched_placeholder(self.tasks[0]):
            task = self.tasks[0]
            task['prompt'] = prompt
            if options:
                task['options'] = dict(options)
            self.active = task['id']
            self.refresh_task_table(); self.later_save()
            return task
        numbers = [int(m.group(1)) for t in self.tasks if (m := re.fullmatch(r'Task #(\d+)', t['name']))]
        task = fresh_task(max(numbers + [0]) + 1)
        task['prompt'] = prompt
        if options:
            task['options'] = dict(options)
        self.tasks.append(task)
        self.active = task['id']
        self.refresh_task_table(); self.later_save()
        return task

    def _consume_quick_prompts(self):
        value = self.quick_prompt.toPlainText()
        lines = [line.strip() for line in value.splitlines() if line.strip()]
        if not lines:
            return []
        added_ids = []
        for line in lines:
            task = self.add_task(prompt=line)
            added_ids.append(task['id'])
        self.show_task_monitor()
        self.quick_prompt.clear()
        self.view.scrollToBottom()
        self.log(f'Đã thêm {len(added_ids)} task từ khung nhập prompt.')
        return added_ids

    def add_quick_task(self):
        ids = self._consume_quick_prompts()
        self.quick_prompt.setFocus()
        return ids

    def add_and_run_quick_task(self):
        ids = self._consume_quick_prompts()
        if ids:
            self.request_task_runs(ids)
            return
        runnable = [t['id'] for t in self.tasks if str(t.get('prompt', '')).strip() or self.settings.get('engine') == 'local']
        if runnable:
            selected = [tid for tid in self.selected_ids() if tid in runnable]
            self.request_task_runs(selected or runnable)
            return
        self.quick_prompt.setFocus()
        self.log('Nhập prompt vào ô tạo video trước khi bấm Tạo video ngay.')

    def add_task_with_dialog(self):
        initial = self.quick_prompt.toPlainText().strip()
        if len(self.tasks) == 1 and self._is_untouched_placeholder(self.tasks[0]):
            task = self.tasks[0]
            if initial:
                task['prompt'] = initial
        else:
            task = self.add_task(prompt=initial)
        if initial:
            self.quick_prompt.clear()
        self.active = task['id']
        if PromptDialog(self, task).exec() == QDialog.Accepted:
            self.refresh_task_table(); self.later_save()
            self.view.scrollToBottom()

    def delete_selected_tasks(self):
        self._clear_tasks(set(self.selected_ids()), 'đã chọn')

    def delete_done_tasks(self):
        self._clear_tasks({t['id'] for t in self.tasks if badge_for(t.get('status', ''))[0] == 'Xong'}, 'đã xong')

    def _clear_tasks(self, ids, what):
        # Xóa được cả khi Auto đang chạy; bỏ qua job đang chạy/chờ để không làm hỏng lượt đang xử lý.
        busy = self.running_task_ids | set(self.pending_task_ids) | self.fetching_ids
        remove = {tid for tid in ids if tid not in busy}
        skipped = len(ids) - len(remove)
        if not remove:
            self.log('Không có job nào để xóa.' + (f' Bỏ qua {skipped} job đang chạy.' if skipped else '')); return
        self.tasks[:] = [t for t in self.tasks if t['id'] not in remove]
        if self.active in remove:
            self.active = self.tasks[0]['id'] if self.tasks else None
        self.refresh_task_table(); self.save()
        self.log(f'Đã xóa {len(remove)} job {what} khỏi bảng.' +
                 (f' Bỏ qua {skipped} job đang chạy.' if skipped else ' Video đã lưu vẫn giữ trong thư mục.'))

    def drop_auto_task(self, tid):
        dropped = self._task(tid)
        if dropped is not None:
            path = self.task_log_files.path_for(dropped)
            self.record_task_log(dropped, f'Đã xóa khỏi bảng; nhật ký vẫn lưu tại {path}')
            self.flush_task_logs()
            if self.log_task_id == tid:
                self.task_log_title.setText(self.task_log_title.text() + ' (đã xóa khỏi bảng)')
        self.tasks[:] = [t for t in self.tasks if t['id'] != tid]
        self.running_task_ids.discard(tid)
        if self.active == tid:
            self.active = self.tasks[0]['id'] if self.tasks else None
        self.refresh_task_table(); self.later_save()

    def edit_table_task(self, tid):
        if not self._task(tid):
            return
        if tid in self.running_task_ids or tid in self.pending_task_ids:
            self.log('Task đang chạy/chờ chạy; dừng task trước khi sửa prompt.'); return
        self.active = tid
        PromptDialog(self, self._task(tid)).exec()
        self.refresh_task_table(); self.later_save()

    def configure_table_task(self, tid): self.configure_tasks([tid])

    def configure_selected_tasks(self):
        ids = self.selected_ids()
        if not ids:
            self.log('Chọn task cần áp dụng trước khi bấm Cấu hình đã chọn.'); return
        self.configure_tasks(ids, shared=True)

    def configure_tasks(self, ids, shared=False):
        tasks = [t for t in self.tasks if t['id'] in ids]
        if not tasks:
            return
        busy = self.running_task_ids | set(self.pending_task_ids)
        if any(t['id'] in busy for t in tasks):
            self.log('Bỏ chọn task đang chạy/chờ chạy hoặc dừng task trước khi đổi cấu hình.'); return
        ConfigDialog(self, tasks, shared).exec()

    def record_task_log(self, task, message):
        line = stamp(message)
        entries = task.setdefault('task_logs', [])
        entries.append(line)
        task['task_logs'] = entries[-MAX_LINES_IN_TASK:]
        self.task_log_files.add(task, line)
        if task.get('id') == self.log_task_id:
            self.task_log_box.appendPlainText(line)

    # ---- khung nhật ký của task đang chọn ------------------------------------
    def _follow_selected_task(self, current, _previous=None):
        if current.isValid() and current.row() < len(self.tasks):
            self.show_task_log(self.tasks[current.row()]['id'])

    def show_task_log(self, tid):
        task = self._task(tid)
        if task is None:
            return
        self.log_task_id = tid
        self.task_log_title.setText('NHẬT KÝ TASK • ' + str(task.get('name') or ''))
        lines = task.get('task_logs') or self.task_log_files.tail(task) or ['(chưa có nhật ký)']
        self.task_log_box.setPlainText('\n'.join(lines))
        self.task_log_box.verticalScrollBar().setValue(self.task_log_box.verticalScrollBar().maximum())

    def flush_task_logs(self):
        if self.task_log_files.flush() and not getattr(self, '_task_log_warned', False):
            self._task_log_warned = True
            self.log('Không ghi được file nhật ký task vào thư mục data\\task_logs (kiểm tra quyền ghi).')

    def open_task_log_file(self, tid=None):
        task = self._task(tid or self.log_task_id) if (tid or self.log_task_id) else None
        if task is None:
            self.log('Chọn một task trong bảng để mở file nhật ký.'); return
        self.flush_task_logs()
        path = self.task_log_files.path_for(task)
        if not path.is_file():
            self.log(task['name'] + ': task chưa có nhật ký.'); return
        try: os.startfile(path)
        except OSError as exc: self.error('Không mở được file nhật ký', str(exc))

    def open_task_log_folder(self):
        folder = DATA / 'task_logs'
        try:
            folder.mkdir(parents=True, exist_ok=True); os.startfile(folder)
        except OSError as exc:
            self.error('Không mở được thư mục nhật ký', str(exc))

    # ---- checkbox lưu cài đặt -------------------------------------------------
    def save_browser_visibility(self):
        self.settings['show_browser'] = bool(self.cb_show_browser.isChecked()); self.save()
        state = 'hiện' if self.settings['show_browser'] else 'ẩn'
        self.log(f'Đã chọn {state} cửa sổ Chrome khi chạy.' + (' Áp dụng từ lượt chạy tiếp theo.' if self.running else ''))

    def save_extension_choice(self):
        self.settings['load_extension'] = bool(self.cb_extension.isChecked()); self.save()
        self.log(('Có' if self.settings['load_extension'] else 'Không') + ' nạp extension Seedance Studio Pro khi mở Chrome.')

    def save_auto_retry(self):
        self.settings['auto_retry'] = bool(self.cb_auto_retry.isChecked()); self.save()
        if self.auto_runner is not None:
            self.auto_runner.set_auto_retry(self.settings['auto_retry'])
        self.log('Bật tự chạy lại task lỗi đến khi xong.' if self.settings['auto_retry'] else 'Tắt tự chạy lại task lỗi.')

    def save_auto_duration(self):
        value = self.auto_duration_values[self.auto_duration.currentText()]
        self.settings['auto_duration_policy'] = value
        self.save()
        label = self.auto_duration.currentText()
        self.log(f'Đã chọn thời lượng Auto: {label}. Áp dụng cho job nhận tiếp theo.')

    def open_output(self):
        try:
            p = Path(self.settings['output']); p.mkdir(parents=True, exist_ok=True); os.startfile(p)
        except OSError as exc:
            self.error('Không mở được thư mục', str(exc))

    def choose_output(self):
        folder = QFileDialog.getExistingDirectory(self, 'Chọn thư mục lưu video')
        if folder:
            self.settings['output'] = folder; self.refresh_gallery(); self.later_save()

    # ---- auto retry --------------------------------------
    def schedule_auto_retry(self):
        failed = [t['id'] for t in self.tasks if not t.get('auto_job') and is_retryable_status(t.get('status', ''))]
        if not failed or self.running or self._batch_preparing:
            return
        self.log(f'Tự chạy lại {len(failed)} task lỗi sau 8 giây… (bấm Dừng tất cả để ngừng)')
        self._auto_retry_job = self.after(8000, lambda ids=failed: self._run_auto_retry(ids))

    def _run_auto_retry(self, ids):
        self._auto_retry_job = None
        if self.running or self._batch_preparing or self.auto_runner is not None:
            return
        if not self.settings.get('auto_retry'):
            return
        ids = [t['id'] for t in self.tasks if t['id'] in ids and is_retryable_status(t.get('status', ''))]
        if ids:
            self.request_task_runs(ids)

    # ---- chạy / auto / sheet / fetch  -----------
    def run_table_task(self, tid): self.request_task_runs([tid])

    def run_all_tasks(self):
        if hasattr(self, 'quick_prompt') and self.quick_prompt.toPlainText().strip():
            self._consume_quick_prompts()
        runnable = [t['id'] for t in self.tasks if str(t.get('prompt', '')).strip() or self.settings.get('engine') == 'local']
        self.request_task_runs(runnable or [t['id'] for t in self.tasks])

    def run_selected_tasks(self):
        selected = self.selected_ids()
        if selected:
            self.request_task_runs(selected)
        else:
            self.run_all_tasks()

    def fetch_sheet_tasks(self):
        pass  # bản Auto: không có lấy nhiệm vụ từ Sheet

    def toggle_auto(self, sheet=False):
        sheet = False  # bản Auto: chỉ chạy Auto từ web, không có Auto Sheet
        if getattr(self, '_sheet_fetching', False):
            self.log('Chờ lấy nhiệm vụ từ Sheet xong trước khi bật Auto.'); return
        if self.auto_runner is not None:
            self.auto_runner.pause()
            self.auto_button.setText('Đang dừng Auto'); self.auto_button.setEnabled(False)
            self.sheet_auto_button.setText('Đang dừng'); self.sheet_auto_button.setEnabled(False)
            self.log('Auto ngừng nhận job mới; tiếp tục xử lý và báo kết quả các job đã nhận.')
            return
        if self.running or self._batch_preparing:
            self.log('Chờ lượt hiện tại kết thúc trước khi bật Auto.'); return
        if getattr(self, 'license_ok', None) is False:
            self.log('Chưa bật Auto: ' + getattr(self, 'license_msg', 'API key/máy chưa hợp lệ.') + ' Sửa trong Cài đặt rồi thử lại.')
            self.check_license(); return
        cfg = copy.deepcopy(self.settings)
        cfg['engine'] = 'dola'  # bản Auto luôn chạy Dola
        accounts = copy.deepcopy([a for a in self.accounts if a.get('selected') and a.get('live_state') != 'die' and a['id'] not in self.account_jobs])
        try:
            runner = AutoJobs(cfg, accounts, DATA, self.events, self.cancel_event, self.task_cancels, poll_seconds=20)
        except (ValueError, OSError) as exc:
            self.log('Chưa bật Auto: ' + str(exc)); return
        self._batch_preparing = True
        self.auto_button.setText('Đang chuẩn bị'); self.auto_button.setEnabled(False); self.sheet_auto_button.setEnabled(False)
        all_accounts = copy.deepcopy(self.accounts)
        busy_ids = set(self.account_jobs)
        def prepare():
            error = ''
            try:
                problems, _, _ = batch_blockers(cfg, [], accounts, all_accounts, busy_ids, False)
                if problems: error = '; '.join(problems)
                else: Path(cfg['output']).mkdir(parents=True, exist_ok=True)
            except Exception as exc:
                error = str(exc)
            def begin():
                self._batch_preparing = False
                if error:
                    self.auto_button.setText('Chạy Auto'); self.auto_button.setEnabled(True); self.sheet_auto_button.setEnabled(True)
                    self.log('Chưa bật Auto: ' + error); return
                self.auto_runner = runner
                self.running = True
                self.batch_cfg = cfg
                self.running_task_ids.clear(); self.cancel_event.clear()
                self.run_all.setEnabled(False); self.stop_button.setEnabled(True)
                self.auto_button.setText('Chạy Auto' if sheet else 'Tắt Auto'); self.auto_button.setEnabled(not sheet)
                self.sheet_auto_button.setText('Tắt Auto Sheet' if sheet else 'Chạy Auto Sheet'); self.sheet_auto_button.setEnabled(sheet)
                self._set_status('● Auto đang bật', GREEN)
                policy = ' • mỗi tài khoản đúng 1 job' if runner.one_job_per_account else ''
                self.log(f'Auto đã bật • {len(runner.slots)} Chrome × {runner.capacity} tab{policy}. Chỉ GET khi còn chỗ chạy.')
                self.run_background(runner.run)
            self.post_ui(begin)
        self.run_background(prepare)

    def activate_task_run(self, task, cfg, resume=False):
        if not resume and cfg.get('engine') == 'dola':
            if task.get('conversation_url'):
                history = task.setdefault('conversation_history', [])
                history.append({'url': task['conversation_url'], 'account': task.get('conversation_account')})
                task['conversation_history'] = history[-20:]
            task.pop('conversation_url', None); task.pop('conversation_account', None)
        task['status'] = 'Chờ chạy'
        task['task_logs'] = []
        self.record_task_log(task, 'Chờ chạy • lấy kết quả cũ' if resume else 'Chờ chạy • lượt mới')
        if not resume:
            task.pop('output_path', None)
            options = task_settings(cfg, task)
            task['execution'] = {'model': options['model'], 'duration': options['duration']}

    def join_running_tasks(self, ids):
        cfg = self.batch_cfg or self.settings
        prepared = []
        for task in self.tasks:
            if task['id'] not in ids:
                continue
            if cfg.get('engine') != 'local' and not str(task.get('prompt', '')).strip():
                self.log(task['name'] + ': nhập prompt trước khi chạy.'); continue
            images = task.get('images') or []
            if len(images) > 10 or any(not Path(p).is_file() for p in images):
                self.log(task['name'] + ': kiểm tra lại ảnh tham chiếu.'); continue
            self.activate_task_run(task, cfg, resume=False)
            self.task_cancels[task['id']] = threading.Event()
            self.running_task_ids.add(task['id'])
            snapshot = copy.deepcopy(task); snapshot['run_action'] = 'new'; snapshot['_attempt'] = uuid.uuid4().hex
            prepared.append(snapshot)
        if not prepared:
            self.refresh_task_table(); return True
        with self.live_lock:
            if not self.live_open or self.live_intake is None:
                for snapshot in prepared:
                    self.running_task_ids.discard(snapshot['id'])
                return False
            for snapshot in prepared:
                self.live_intake.put(snapshot)
            if self.batch_totals is not None:
                with self.batch_lock:
                    self.batch_totals['total'] += len(prepared)
        self.log('Đã thêm vào lượt đang chạy: ' + ', '.join(t['name'] for t in prepared))
        self.refresh_task_table(); self.save()
        return True

    def request_task_runs(self, ids):
        if self.auto_runner is not None:
            self.log('Đang chạy Auto; tắt Auto trước khi chạy task thủ công.'); return
        wanted = []
        busy = self.busy_table_ids()
        cfg = self.batch_cfg or self.settings
        for tid in ids:
            if tid in busy or tid in wanted:
                continue
            task = self._task(tid)
            if task is None:
                continue
            if cfg.get('engine') != 'local' and not str(task.get('prompt', '')).strip():
                self.log(task['name'] + ': nhập prompt trước khi chạy.'); continue
            images = task.get('images') or []
            if len(images) > 10 or any(not Path(p).is_file() for p in images):
                self.log(task['name'] + ': kiểm tra lại ảnh tham chiếu.'); continue
            wanted.append(tid)
        if not wanted:
            return
        if self.running:
            if self.join_running_tasks(wanted):
                self.set_busy(self.busy_table_ids())
                return
            for tid in wanted:
                if tid not in self.pending_task_ids:
                    self.pending_task_ids.append(tid)
                    t = self._task(tid)
                    if t:
                        t['status'] = 'Chờ chạy • hàng đợi lượt sau'
                        self.record_task_log(t, t['status'])
                        self.update_task_row(t)
            self.set_busy(self.busy_table_ids())
            self.log(f'Đã xếp {len(wanted)} task vào hàng đợi lượt sau.')
            return
        if self._batch_preparing:
            for tid in wanted:
                if tid not in self.pending_task_ids:
                    self.pending_task_ids.append(tid)
            return
        self.start_batch(False, task_ids=wanted)

    def busy_table_ids(self):
        return self.running_task_ids | set(self.pending_task_ids) | self.fetching_ids

    def fetch_table_video(self, tid):
        task = self._task(tid)
        if task is None:
            return
        if tid in self.busy_table_ids():
            self.log(task['name'] + ': task đang chạy/chờ; chờ xong rồi Lấy video.'); return
        url = task.get('conversation_url', '')
        owner = next((a for a in self.accounts if a['id'] == task.get('conversation_account')), None)
        if not url or owner is None:
            self.log(task['name'] + ': chưa lưu hội thoại Dola hoặc tài khoản tạo hội thoại đã bị xóa.'); return
        cfg = copy.deepcopy(self.batch_cfg or self.settings)
        if cfg.get('engine') != 'dola':
            self.log('Chọn bộ xử lý Dola trong Cài đặt trước khi Lấy video.'); return
        job = copy.deepcopy(task); job['run_action'] = 'resume'; job.pop('_auto_deadline', None)
        owner = copy.deepcopy(owner)
        cancel = threading.Event(); self.task_cancels[tid] = cancel
        self.fetching_ids.add(tid); self.set_busy(self.busy_table_ids())
        self.events.put(('status', tid, f"Đang lấy video từ hội thoại đã lưu • {owner.get('name', '')}"))
        self.log(f"{task['name']}: Lấy video từ {url} bằng {owner.get('name', '')}; không gửi lại prompt.")
        def report(message):
            if message.startswith(('[Tài khoản]', '[Phiên hợp lệ]', '[Hội thoại]', '[Phiên hết hạn]')):
                return
            self.events.put(('status', tid, message))
        def work():
            try:
                path = Path(render_dola(job, task_settings(cfg, job), owner, cancel, report))
                if job.get('stt'):
                    if job.get('sheet_output'):
                        final = Path(job['sheet_output'])
                    elif job.get('auto_job'):
                        final = task_output_path(cfg['output'], job)  # cùng tên 1.mp4, 2.mp4… như lúc chạy Auto
                    else:
                        final = output_for_job(cfg['output'], job.get('stt'), job.get('task_code') or job['id'])
                    final.parent.mkdir(parents=True, exist_ok=True)
                    if final.exists() and final != path.resolve():
                        archive = final.parent / '_previous'; archive.mkdir(exist_ok=True)
                        final.replace(archive / (final.stem + '_' + uuid.uuid4().hex[:8] + '.mp4'))
                    path.replace(final); path = final
                self.events.put(('task_result', tid, str(path.resolve())))
                self.events.put(('status', tid, '✓ Hoàn thành • đã lấy video từ hội thoại'))
                self.events.put(('log', f"{task['name']}: Đã lấy video {path.name}"))
                if job.get('auto_job') and job.get('task_code'):
                    self._report_auto_job_done(job, path)
            except Cancelled:
                self.events.put(('status', tid, 'Đã hủy'))
            except Exception as exc:
                self.events.put(('status', tid, task_failure_status(exc)))
                self.events.put(('log', f"{task['name']}: Lấy video chưa được • {exc}"))
            finally:
                self.events.put(('fetch_done', tid))
        self.run_background(work)

    def stop_table_task(self, tid):
        if tid in self.fetching_ids:
            self.task_cancels[tid].set(); self.events.put(('status', tid, 'Đang dừng lấy video…'))
        elif tid in self.pending_task_ids:
            self.pending_task_ids.remove(tid); self.events.put(('status', tid, 'Đã hủy • bỏ khỏi hàng đợi'))
        elif tid in self.running_task_ids:
            self.task_cancels[tid].set(); self.events.put(('status', tid, 'Đang dừng task…'))
        self.refresh_task_table()

    def start_batch(self, one, resume=False, task_ids=None):
        if self.auto_runner is not None: return
        if self.running or self._batch_preparing: return
        if not self.tasks: return
        jobs = copy.deepcopy([t for t in self.tasks if t['id'] in task_ids] if task_ids is not None
                             else [self.task()] if one or resume else self.tasks)
        jobs = [j for j in jobs if j]
        if not jobs: return
        cfg = copy.deepcopy(self.settings)
        accounts = copy.deepcopy([a for a in self.accounts if a.get('selected') and a.get('live_state') != 'die'])
        if resume:
            if cfg['engine'] != 'dola':
                self.info('Lấy kết quả Dola', 'Chọn bộ xử lý Dola để lấy kết quả hội thoại cũ.'); return
            owner = next((a for a in self.accounts if a['id'] == jobs[0].get('conversation_account')), None)
            if not jobs[0].get('conversation_url') or not owner:
                self.info('Chưa có kết quả cũ', 'Task chưa lưu hội thoại hoặc tài khoản gốc đã bị bỏ.'); return
            accounts = [copy.deepcopy(owner)]
        for job in jobs:
            job['run_action'] = 'resume' if resume else 'new'
            if not resume:
                job.pop('conversation_url', None); job.pop('conversation_account', None)
        self._batch_preparing = True
        self._set_status('● Đang chuẩn bị', CYAN)
        busy_ids = set(self.account_jobs); all_accounts = copy.deepcopy(self.accounts)
        def work():
            problems, title, info = [], '', False
            try:
                problems, title, info = batch_blockers(cfg, jobs, accounts, all_accounts, busy_ids, resume)
                if not problems:
                    Path(cfg['output']).mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                problems, title, info = [str(exc)], 'Thư mục xuất không hợp lệ', False
            except Exception:
                problems, title, info = ['Không chuẩn bị được lượt chạy. Kiểm tra profile và thư mục xuất.'], 'Chưa thể chạy', False
            def finish():
                self._batch_preparing = False
                if not self._alive or self.running:
                    return
                if problems:
                    self._set_status('● Sẵn sàng', GREEN)
                    (self.info if info else self.error)(title, '\n'.join(problems)); return
                self._begin_batch(jobs, cfg, accounts, resume)
            self.post_ui(finish)
        self.run_background(work)

    def _begin_batch(self, jobs, cfg, accounts, resume):
        self.running = True
        self.running_task_ids = {t['id'] for t in jobs}
        self.task_cancels = {tid: threading.Event() for tid in self.running_task_ids}
        self.batch_cfg = cfg
        self.batch_totals = {'total': len(jobs), 'done': 0, 'ok': 0}
        for job in jobs:
            job['_attempt'] = uuid.uuid4().hex
        with self.live_lock:
            if cfg['engine'] == 'dola':
                self.live_intake = WorkQueue(); self.live_open = True
            else:
                self.live_intake = None; self.live_open = False
        self.cancel_event.clear()
        self.stop_button.setEnabled(True); self.run_all.setEnabled(False)
        self._set_status('● Đang chạy', CYAN)
        self.progress.setValue(0)
        for t in self.tasks:
            if any(j['id'] == t['id'] for j in jobs):
                self.activate_task_run(t, cfg, resume=resume)
        self.refresh_task_table()
        self.log(f"Bắt đầu {len(jobs)} task • Dola AI")
        if cfg['engine'] == 'dola' and self.settings.get('auto_retry'):
            self.log('Chạy gối liên tục: luồng rảnh nhận task tiếp theo; task lỗi xuống cuối lượt, không chờ cả batch.')
        self.save()
        threading.Thread(target=self.batch_worker, args=(jobs, cfg, accounts), daemon=True).start()

    def batch_worker(self, jobs, cfg, accounts):
        available = queue.Queue()
        session_cache = {a['id']: {'name': a.get('name', ''), 'at': a.get('session_verified_at', 0), 'valid': True}
                         for a in accounts if a.get('session_verified_at')}
        session_cache_lock = threading.Lock()
        account_order = sort_accounts_by_lru(accounts)
        for a in account_order: available.put(a)
        credit_locked_ids = {a['id'] for a in accounts if credit_locked(a)}; work_locked_ids = {a['id'] for a in accounts if work_locked(a)}; rate_limited_ids = {a['id'] for a in accounts if rate_limited(a)}
        stopped_ids = set(); credit_lock = threading.Lock()
        def assignment(task, account):
            task['last_account_id'] = (account or {}).get('id')
            options = task_settings(cfg, task)
            from proxyvn import resolve_proxy_server
            proxy_server = resolve_proxy_server(cfg, account)
            self.events.put(('task_execution', task['id'], {
                'model': options['model'], 'duration': options['duration'],
                'account_id': (account or {}).get('id'),
                'account_name': (account or {}).get('name', 'Cục bộ'),
                'proxy_server': proxy_server}))
        def execute(task):
            account = None
            task_cfg = task_settings(cfg, task)
            cancel = TaskCancel(self.cancel_event, self.task_cancels[task['id']])
            try:
                if cancel.is_set(): raise Cancelled()
                if cfg['engine'] != 'local':
                    while account is None:
                        if cancel.is_set(): raise Cancelled()
                        try: account = available.get(timeout=.2)
                        except queue.Empty: pass
                def report(msg):
                    if msg.startswith('[Khóa Work]') and account:
                        val = msg[len('[Khóa Work]'):].strip()
                        try: until = float(val) if val else None
                        except ValueError: until = None
                        from account_health import lock_for_work
                        reopen = lock_for_work(account, until=until)
                        with credit_lock:
                            work_locked_ids.add(account['id'])
                        self.events.put(('work_locked', account['id'], reopen))
                    elif msg.startswith('[Tài khoản] ') and account:
                        self.events.put(('identity', account['id'], msg[len('[Tài khoản] '):]))
                    elif msg.startswith('[Phiên hợp lệ]') and account:
                        self.events.put(('session_verified', account['id'], msg[len('[Phiên hợp lệ]'):].strip()))
                    elif msg.startswith('[Hội thoại] ') and account:
                        self.events.put(('conversation', task['id'], msg[len('[Hội thoại] '):], account['id']))
                    elif msg.startswith(('ProxyNo1', 'ProxyVN', 'Proxy tay', 'Proxy sẵn', 'Chrome Seedance')):
                        self.events.put(('log', f"{task['name']}: {msg}"))
                        if account and account.get('proxy'):
                            assignment(task, account)
                    else:
                        self.events.put(('status', task['id'], msg))
                assignment(task, account); report('Đang xử lý')
                if account:
                    self.events.put(('log', f"{task['name']} → tài khoản {account['name']}"))
                if cfg['engine'] == 'local':
                    path = render_local(task, task_cfg, cancel, report)
                elif cfg['engine'] == 'dola':
                    path = render_dola(task, task_cfg, account, cancel, report, session_cache, session_cache_lock)
                else:
                    path = render_adapter(task, task_cfg, account, cancel, report)
                self.events.put(('task_result', task['id'], str(Path(path).resolve())))
                self.events.put(('status', task['id'], '✓ Hoàn thành'))
                self.events.put(('log', f"{task['name']}: Đã lưu {Path(path).name}"))
                return True
            except Cancelled:
                self.events.put(('status', task['id'], 'Đã hủy'))
            except Exception as exc:
                self.events.put(('status', task['id'], task_failure_status(exc)))
                self.events.put(('log', f"{task['name']}: {exc}"))
            finally:
                if account is not None: available.put(account)
                self.events.put(('task_done', task['id']))
            return False
        count = 0; video_executor = None
        try:
            if cfg['engine'] == 'dola':
                from dola_tabs import run_profile_tabs, dispatch_profiles, AccountIntake
                tabs = max(1, min(5, int(cfg.get('tabs_per_profile', 3))))
                video_executor = ThreadPoolExecutor(max_workers=max(1, min(2, len(account_order), int(cfg['concurrency']))))
                def submit_video(source, settings, task, url, cancel, report, provenance):
                    return video_executor.submit(finish_video, source, settings, task, url, cancel, report, provenance)
                def report_for(task, account):
                    assignment(task, account)
                    def report(msg):
                        if msg.startswith('[Tài khoản] '):
                            self.events.put(('identity', account['id'], msg[len('[Tài khoản] '):]))
                        elif msg.startswith('[Phiên hợp lệ]'):
                            self.events.put(('session_verified', account['id'], msg[len('[Phiên hợp lệ]'):].strip()))
                        elif msg.startswith('[Hội thoại] '):
                            self.events.put(('conversation', task['id'], msg[len('[Hội thoại] '):], account['id']))
                        elif msg.startswith(('ProxyNo1', 'ProxyVN', 'Proxy tay', 'Proxy sẵn', 'Chrome Seedance')):
                            self.events.put(('log', f"{task['name']}: {msg}"))
                            if account and account.get('proxy'):
                                assignment(task, account)
                        else:
                            self.events.put(('status', task['id'], msg))
                    return report
                def settle(task, path, error):
                    if error is None:
                        if task.get('auto_job') and task.get('stt'):
                            try:
                                final = task_output_path(cfg['output'], task)
                                final.parent.mkdir(parents=True, exist_ok=True)
                                src_path = Path(path).resolve()
                                if src_path != final:
                                    if final.exists():
                                        arch = final.parent / '_previous'; arch.mkdir(exist_ok=True)
                                        final.replace(arch / (final.stem + '_' + uuid.uuid4().hex[:8] + '.mp4'))
                                    src_path.replace(final)
                                path = final
                            except Exception as e:
                                self.events.put(('log', f"Lưu file Auto: {e}"))
                        self.events.put(('task_result', task['id'], str(Path(path).resolve())))
                        self.events.put(('status', task['id'], '✓ Hoàn thành'))
                        self.events.put(('log', f"{task['name']}: Đã lưu {Path(path).name}"))
                        if task.get('auto_job') and task.get('task_code'):
                            self._report_auto_job_done(task, Path(path))
                    elif isinstance(error, Cancelled):
                        self.events.put(('status', task['id'], 'Đã hủy'))
                    else:
                        self.events.put(('status', task['id'], task_failure_status(error)))
                        self.events.put(('log', f"{task['name']}: {error}"))
                    retry = (error is not None and not isinstance(error, Cancelled)
                             and self.settings.get('auto_retry') and not self.cancel_event.is_set()
                             and not self.task_cancels[task['id']].is_set()
                             and any(usable(i) for i in range(len(account_order))))
                    if retry and self.live_intake is not None:
                        again = next_attempt(task)
                        self.events.put(('task_requeued', task['id'], again['_retry_round']))
                        with self.batch_lock:
                            self.batch_totals['total'] += 1
                        self.live_intake.put(again)
                    else:
                        self.events.put(('task_done', task['id']))
                    with self.batch_lock:
                        self.batch_totals['done'] += 1
                        self.batch_totals['ok'] += error is None
                        self.events.put(('progress', self.batch_totals['done'], self.batch_totals['total']))
                def usable(index):
                    return account_order[index]['id'] not in credit_locked_ids | rate_limited_ids | stopped_ids
                def settle_for(account):
                    def settle_account(task, path, error):
                        from dola_browser import ProfileStopped, SessionDead
                        task['last_account_id'] = account['id']
                        if isinstance(error, ProfileStopped):
                            first = account['id'] not in stopped_ids
                            stopped_ids.add(account['id'])
                            if first and isinstance(error, SessionDead):
                                self.events.put(('account_die', account['id'], str(error)))
                        if error is None:
                            self.events.put(('account_used', account['id']))
                            if cfg.get('cooldown_after_manual_run', False):
                                self.events.put(('run_success', account['id']))
                        if is_work_limit_error(error):
                            until = credit_until(error)
                            with credit_lock:
                                first = account['id'] not in work_locked_ids
                                work_locked_ids.add(account['id'])
                                has_work_available = any(usable(i) and account_order[i]['id'] not in work_locked_ids for i in range(len(account_order)))
                            if first:
                                self.events.put(('work_locked', account['id'], until))
                            intake = self.live_intake
                            if intake is not None and not self.cancel_event.is_set():
                                again = next_attempt(task)
                                if not has_work_available:
                                    if 'options' not in again:
                                        again['options'] = {}
                                    again['options']['dola_mode'] = 'fast'
                                    self.events.put(('log', f"⚠️ {account['name']} đạt giới hạn Work. Hết tài khoản Work khả dụng -> Tự động chuyển {task.get('name', 'task')} sang Chat"))
                                    self.events.put(('status', task['id'], "Chuyển sang chế độ Chat do hết quota Work"))
                                else:
                                    self.events.put(('status', task['id'], f"Chờ chạy • {account['name']} giới hạn Work, chuyển tài khoản khác"))
                                intake.put(again)
                                return
                            return settle(task, path, error)
                        if is_rate_limit_error(error):
                            until = credit_until(error)
                            with credit_lock:
                                first = account['id'] not in rate_limited_ids
                                rate_limited_ids.add(account['id'])
                                retry = not self.cancel_event.is_set() and any(usable(i) for i in range(len(account_order)))
                            if first:
                                self.events.put(('rate_limited', account['id'], until))
                            intake = self.live_intake
                            if retry and intake is not None:
                                again = next_attempt(task)
                                self.events.put(('status', task['id'], f"Chờ chạy • {account['name']} bị Rate Limit, chuyển tài khoản kế tiếp"))
                                intake.put(again)
                                return
                            return settle(task, path, error)
                        if is_credit_error(error):
                            until = credit_until(error)
                            with credit_lock:
                                first = account['id'] not in credit_locked_ids
                                credit_locked_ids.add(account['id'])
                                retry = not self.cancel_event.is_set() and any(usable(i) for i in range(len(account_order)))
                            if first or until:
                                self.events.put(('credit_locked', account['id'], until))
                            intake = self.live_intake
                            if retry and intake is not None:
                                again = next_attempt(task)
                                self.events.put(('status', task['id'], f"Chờ chạy • {account['name']} hết điểm, chuyển tài khoản kế tiếp"))
                                intake.put(again)
                                return
                            return settle(task, path, error)
                        return settle(task, path, error)
                    return settle_account
                def drain_live():
                    found = []; intake = self.live_intake
                    if intake is None:
                        return found
                    while True:
                        try: found.append(intake.get_nowait())
                        except queue.Empty: return found
                def run_round(initial):
                    def allowed(index, task):
                        acc_id = account_order[index]['id']
                        opts = task_settings(cfg, task)
                        is_pro = opts.get('dola_mode') == 'pro'
                        if is_pro and acc_id in work_locked_ids:
                            if any(usable(i) and account_order[i]['id'] not in work_locked_ids for i in range(len(account_order))):
                                return False
                            if 'options' not in task:
                                task['options'] = {}
                            task['options']['dola_mode'] = 'fast'
                            self.events.put(('log', f"⚡ Tất cả tài khoản hết giới hạn Work • Tự động chuyển {task.get('name', 'task')} sang chế độ Chat"))

                        eligible = [a for i, a in enumerate(account_order) if usable(i)]
                        previous = task.get('last_account_id')
                        return (task.get('run_action') == 'resume' or len(eligible) < 2
                                or previous != acc_id)
                    def run(index, group):
                        account = account_order[index]
                        def browser_report(message):
                            self.events.put(('log', f"[Chrome {account['name']}] {message}"))
                        return run_profile_tabs(group, account, cfg, self.cancel_event, report_for, settle_for(account),
                                    session_cache, session_cache_lock, submit_video, self.task_cancels,
                                    AccountIntake(self.live_intake, lambda task: allowed(index, task)),
                                    browser_report=browser_report, accepting=lambda: usable(index))
                    left = dispatch_profiles(initial, min(len(account_order), int(cfg['concurrency'])), tabs,
                                             self.live_intake, self.cancel_event, run,
                                             account_count=len(account_order), usable=usable, allowed=allowed)
                    if left:
                        self.events.put(('log', 'Không còn tài khoản khả dụng; giữ các task còn lại để chạy sau.'))
                    for task in left:
                        settle(task, None, RuntimeError(CREDIT_ERROR))
                def cancel_unstarted(pending):
                    for task in pending:
                        self.events.put(('status', task['id'], 'Đã hủy')); self.events.put(('task_done', task['id']))
                round_jobs = jobs
                while round_jobs:
                    run_round(round_jobs)
                    with self.live_lock: extra = drain_live()
                    if self.cancel_event.is_set():
                        cancel_unstarted(extra); break
                    if extra:
                        round_jobs = extra; continue
                    with self.live_lock:
                        self.live_open = False; extra = drain_live()
                    if self.cancel_event.is_set():
                        cancel_unstarted(extra); break
                    if extra:
                        with self.live_lock: self.live_open = True
                        round_jobs = extra; continue
                    break
                with self.batch_lock:
                    count = self.batch_totals['ok']
                return
            workers = min(int(cfg['concurrency']), len(jobs), len(accounts) if cfg['engine'] != 'local' else 8)
            with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
                futures = [executor.submit(execute, task) for task in jobs]
                for done, future in enumerate(as_completed(futures), 1):
                    count += bool(future.result())
                    self.events.put(('progress', done, len(jobs)))
        except Exception as exc:
            self.events.put(('log', f'Lỗi bộ điều phối: {exc}'))
        finally:
            with self.live_lock:
                self.live_open = False; intake = self.live_intake; self.live_intake = None
            if intake is not None:
                while True:
                    try: task = intake.get_nowait()
                    except queue.Empty: break
                    self.events.put(('status', task['id'], 'Đã hủy')); self.events.put(('task_done', task['id']))
            if video_executor is not None:
                video_executor.shutdown(wait=True, cancel_futures=False)
            total = len(jobs)
            if cfg['engine'] == 'dola' and self.batch_totals is not None:
                with self.batch_lock:
                    count = self.batch_totals['ok']; total = max(1, self.batch_totals['total'])
            self.batch_cfg = None
            self.events.put(('finished', count, total))

    def cancel_batch(self):
        if self._auto_retry_job is not None:
            self.after_cancel(self._auto_retry_job); self._auto_retry_job = None
            self.log('Đã ngừng tự chạy lại task lỗi.')
        if self.auto_runner is not None:
            self.auto_runner.pause()
            self.auto_button.setText('Đang dừng Auto'); self.auto_button.setEnabled(False)
            self.sheet_auto_button.setText('Đang dừng'); self.sheet_auto_button.setEnabled(False)
        for tid in self.pending_task_ids:
            self.events.put(('status', tid, 'Đã hủy • bỏ khỏi hàng đợi'))
        self.pending_task_ids.clear()
        if self.running:
            self.cancel_event.set(); self.stop_button.setEnabled(False)
            self.log('Đang dừng các tác vụ và tiến trình con…')

    def _set_status(self, text, color):
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f'color: {color};')
        bg = {GREEN: SUCCESS_BG, RED: ERROR_BG, WARNING: WARNING_BG}.get(color, ACTIVE_BG)
        _style_pill(self.header_status, text, color, bg)

    # ---- vòng lặp sự kiện -----------------------
    def poll_events(self):
        """Xử lý sự kiện từ các luồng nền theo từng đợt ngắn (~12 ms) rồi trả quyền cho giao diện.
        Một sự kiện lỗi chỉ bị bỏ qua và ghi nhật ký, vòng cập nhật luôn chạy tiếp."""
        try:
            self._drain_events()
        except Exception as exc:
            try: self.log('Bỏ qua một cập nhật giao diện bị lỗi: ' + repr(exc)[:200])
            except Exception: pass
        finally:
            if self._alive:
                self.after(15 if not self.events.empty() else 100, self.poll_events)

    def _drain_events(self):
        deadline = time.monotonic() + .012
        try:
            for _ in range(60):
                if time.monotonic() >= deadline:
                    break
                event = self.events.get_nowait()
                kind = event[0]
                if kind == 'call':
                    try: event[1]()
                    except Exception as exc: self.log('Cập nhật giao diện chưa xong: ' + str(exc)[:180])
                elif kind == 'log': self.log(event[1])
                elif kind == 'browser_profile':
                    account = self.account_by_id.get(event[1])
                    if account:
                        account.update(event[2]); self.save(); self.request_accounts_paint()
                elif kind == 'auto_task':
                    task = event[1]
                    previous = next((t for t in self.tasks if t['id'] == task['id']), None)
                    if previous is None: self.tasks.append(task)
                    else: previous.update(task); task = previous
                    self.running_task_ids.add(task['id'])
                    if previous is None and not task.get('sheet_job'):
                        try:
                            target = task_output_path(self.settings['output'], task)
                            where = f' • lưu thành {target.parent.name}/{target.name}'
                        except (ValueError, KeyError, TypeError):
                            where = ''
                        self.record_task_log(task, f"Nhận job từ web: {task.get('task_code', '')}{where}")
                    self.show_task_monitor(); self.later_save()
                elif kind in ('auto_report', 'sheet_report'):
                    task = next((t for t in self.tasks if t['id'] == event[1]), None)
                    if task:
                        task['auto_report_status'] = event[2]
                        if event[2] == 'done': task['status'] = '✓ Hoàn thành • đã báo API'
                        self.record_task_log(task, 'Đã báo ' + event[2] + (' lên Google Sheets.' if kind == 'sheet_report' else ' lên web.'))
                        self.update_task_row(task); self.later_save()
                        if kind == 'auto_report' and event[2] != 'done':
                            task['status'] = {'release': 'Đã dừng • đã trả job về web',
                                              'fail': 'Lỗi • đã báo web',
                                              'rejected': 'Đã dừng • web không còn giao job này'}.get(event[2], task['status'])
                            self.update_task_row(task)
                elif kind == 'auto_finished':
                    self.auto_runner = None
                    self.auto_button.setText('Chạy Auto'); self.auto_button.setEnabled(True)
                    self.sheet_auto_button.setText('Chạy Auto Sheet'); self.sheet_auto_button.setEnabled(True)
                    self.events.put(('finished', event[1], event[2]))
                elif kind == 'account_job_done':
                    self.account_jobs.pop(event[1], None); self.request_accounts_paint()
                elif kind == 'registered_account':
                    _, aid, account, err = event
                    self.account_jobs.pop(aid, None)
                    if account:
                        self.accounts.append(account); self.save()
                        self.log('Đã đăng nhập, lưu tài khoản ' + account['name'] + ' và đóng trình duyệt.')
                    elif err: self.log(err)
                    self.request_accounts_paint()
                elif kind == 'gpm_result':
                    pass
                elif kind == 'conversation':
                    for task in self.tasks:
                        if task['id'] == event[1]:
                            task['conversation_url'] = event[2]; task['conversation_account'] = event[3]
                            self.record_task_log(task, 'Đã lưu hội thoại Dola để lấy lại kết quả khi cần.')
                            self.update_task_row(task)
                    self.save()
                elif kind == 'account':
                    self.account_jobs.pop(event[1], None)
                    for account in self.accounts:
                        if account['id'] == event[1]:
                            account['connection'] = event[2]
                            if len(event) > 3 and event[3]: account['name'] = event[3]
                            if len(event) > 4 and event[4]: account['session_verified_at'] = time.time()
                            account['live_state'] = 'live' if len(event) > 4 and event[4] else ('die' if event[2] == 'Die • Cần đăng nhập' else 'unknown')
                            account['live_checked_at'] = time.time()
                            if account['live_state'] != 'live': account['session_verified_at'] = 0
                    self.later_save(); self.request_accounts_paint()
                elif kind == 'credit_locked':
                    for account in self.accounts:
                        if account['id'] == event[1]:
                            until = event[2] if len(event) > 2 else None
                            reopen = lock_for_credits(account, until=until)
                            reason = 'hết lượt dùng (Dola báo giờ mở lại)' if until else 'hết điểm'
                            self.log(f"{account.get('name', '')}: {reason} • bỏ chọn Dùng chạy, "
                                     f"tự mở lại lúc {unlock_text(reopen)}.")
                    self.later_save(); self.request_accounts_paint()
                elif kind == 'work_locked':
                    for account in self.accounts:
                        if account['id'] == event[1]:
                            until = event[2] if len(event) > 2 else None
                            reopen = lock_for_work(account, until=until)
                            self.log(f"{account.get('name', '')}: giới hạn chế độ Work • "
                                     f"vẫn dùng được Chat, tự mở lại Work lúc {unlock_text(reopen)}.")
                    self.later_save(); self.request_accounts_paint()
                elif kind == 'rate_limited':
                    for account in self.accounts:
                        if account['id'] == event[1]:
                            until = event[2] if len(event) > 2 else None
                            reopen = lock_for_rate_limit(account, until=until)
                            self.log(f"{account.get('name', '')}: Rate Limit • "
                                     f"tạm khóa, tự mở lại lúc {unlock_text(reopen)}.")
                    self.later_save(); self.request_accounts_paint()
                elif kind == 'account_used':
                    for account in self.accounts:
                        if account['id'] == event[1]:
                            account['last_used_at'] = time.time()
                    self.later_save()
                elif kind == 'run_success':
                    for account in self.accounts:
                        if account['id'] == event[1]:
                            already = run_cooldown_locked(account)
                            until = lock_for_run_cooldown(account)
                            if not already:
                                self.log(f"{account.get('name', '')}: chạy xong 1 video • bỏ chọn Dùng chạy, "
                                         f"tự mở lại lúc {unlock_text(until)}.")
                    self.later_save(); self.request_accounts_paint()
                elif kind == 'account_die':
                    for account in self.accounts:
                        if account['id'] == event[1]:
                            account.update(live_state='die', selected=False, session_verified_at=0,
                                           live_checked_at=time.time(), connection='Die • Không khôi phục được phiên Dola')
                            account.pop('credit_locked_until', None)
                            account.pop('credit_restore_selected', None)
                            account.pop('work_locked_until', None)
                            account.pop('rate_limited_until', None)
                            account.pop('run_cooldown_until', None)
                            self.log(f"{account.get('name', '')}: đã đánh dấu die, bỏ khỏi các lượt chạy. {event[2]}")
                    self.later_save(); self.request_accounts_paint()
                elif kind == 'account_deleted':
                    _, aid, removed, err = event
                    self.account_jobs.pop(aid, None)
                    if err:
                        self.log('Chưa xóa phiên; giữ lại để thử lại: ' + err)
                    else:
                        self.accounts = [a for a in self.accounts if a['id'] != aid]; self.save()
                        self.log('Đã xử lý profile và bỏ tài khoản khỏi danh sách.' if removed else 'Đã bỏ tài khoản khỏi danh sách.')
                    self.request_accounts_paint()
                elif kind == 'session_verified':
                    for account in self.accounts:
                        if account['id'] == event[1] and account.get('live_state') != 'die':
                            account['session_verified_at'] = time.time(); account['connection'] = 'Phiên hợp lệ'; account['live_state'] = 'live'
                            if len(event) > 2 and event[2]: account['name'] = event[2]
                    self.later_save(); self.request_accounts_paint()
                elif kind == 'identity':
                    for account in self.accounts:
                        if account['id'] == event[1] and account.get('live_state') != 'die':
                            account.update(name=event[2], connection='Phiên hợp lệ', live_state='live')
                    self.later_save(); self.request_accounts_paint()
                elif kind in ('task_execution', 'task_result'):
                    for task in self.tasks:
                        if task['id'] == event[1]:
                            if kind == 'task_result':
                                task['output_path'] = event[2]; self.record_task_log(task, 'Đã lưu video: ' + event[2])
                            else:
                                task['execution'] = event[2]
                                task['last_account_id'] = event[2].get('account_id')
                                ex = event[2]
                                self.record_task_log(task, f"Chạy bằng tài khoản {ex.get('account_name') or '?'} • "
                                                           f"{ex.get('model') or '?'} • {ex.get('duration') or '?'} giây")
                            self.update_task_row(task)
                    self.later_save()
                elif kind == 'task_requeued':
                    self.running_task_ids.add(event[1])
                    task = self._task(event[1])
                    if task:
                        task['status'] = f'Chờ chạy • cuối hàng đợi, lần thử {event[2] + 1}'
                        task.pop('conversation_url', None); task.pop('conversation_account', None)
                        self.record_task_log(task, task['status'])
                        self.update_task_row(task)
                    self.later_save()
                elif kind == 'task_done':
                    self.running_task_ids.discard(event[1]); self.set_busy(self.busy_table_ids())
                elif kind == 'fetch_done':
                    self.fetching_ids.discard(event[1]); self.set_busy(self.busy_table_ids()); self.later_save()
                elif kind == 'status':
                    matched = self._task(event[1])
                    if matched is not None:
                        matched['status'] = event[2]
                        self.record_task_log(matched, event[2]); self.update_task_row(matched)
                    self.log(event[2])
                    # Task Auto Sheet lỗi: xóa khỏi bảng (runner tự chạy lại dưới id mới nếu cần).
                    if matched is not None and matched.get('sheet_job') and is_failed_status(event[2]):
                        self.drop_auto_task(matched['id'])
                    else:
                        self.later_save()
                elif kind == 'progress':
                    self.progress.setValue(int(event[1] / max(1, event[2]) * 1000))
                    self.progress_label.setText(f'Đã xử lý {event[1]}/{event[2]} tác vụ')
                elif kind == 'finished':
                    self.running = False; self.running_task_ids.clear()
                    self.run_all.setEnabled(True); self.stop_button.setEnabled(False)
                    self._set_status('● Đã dừng' if self.cancel_event.is_set() else '● Sẵn sàng', GREEN)
                    self.log(f'Kết thúc batch: {event[1]}/{event[2]} tác vụ thành công.')
                    self.save(); self.refresh_gallery()
                    if getattr(self, '_close_when_finished', False):
                        self.after(0, self.close)
                    elif self.pending_task_ids:
                        pending = self.pending_task_ids[:]; self.pending_task_ids.clear()
                        self.start_batch(False, task_ids=pending)
                    elif (self.settings.get('auto_retry') and not self.cancel_event.is_set() and self.auto_runner is None):
                        self.schedule_auto_retry()
        except queue.Empty:
            pass

    # ---- thư viện video đã lưu ------------------------------------------------
    def refresh_gallery(self):
        self.refresh_task_table()   # bản Auto: chỉ có bảng job, không có thư viện video

    def move_task_video_dialog(self, tid):
        task = self._task(tid)
        raw = (task or {}).get('output_path', '')
        src = Path(raw) if raw else None
        if not src or not src.is_file():
            self.log((task or {}).get('name', '') + ': chưa có video để chuyển.'); return
        folder = QFileDialog.getExistingDirectory(self, 'Chuyển video vào thư mục')
        if not folder:
            return
        try:
            folder = Path(folder)
            if folder.resolve() == src.parent.resolve():
                self.log(task['name'] + ': video đã ở trong thư mục này.'); return
            dest = folder / src.name
            if dest.exists():
                dest = folder / f'{src.stem}_{uuid.uuid4().hex[:6]}{src.suffix}'
            shutil.move(str(src), str(dest))
        except Exception as exc:
            self.log(f"{task['name']}: chuyển video chưa được • {exc}"); return
        task['output_path'] = str(dest.resolve())
        self.update_task_row(task); self.save()
        self.log(f"{task['name']}: đã chuyển video vào {folder} và cập nhật đường dẫn.")

    # ---- tài khoản ------------------------------------------------------------
    def draw_accounts(self):
        self.account_card.update_from(self.accounts, self.account_jobs)

    # ---- ProxyNo1: luồng riêng đổi IP 15 phút/lần ------------------------------
    def check_license(self):
        """Hỏi web key/máy có hợp lệ không; nếu bị thu hồi / máy chưa duyệt thì chặn bật Auto."""
        protected = self.settings.get('auto_render_key_protected', '')
        machine = str(self.settings.get('auto_machine_code') or '')
        if not protected:
            self.license_ok = False
            self.license_msg = 'Chưa nhập API key của web (vào Cài đặt nếu cần chạy Auto Web).'
            if not self.running:
                _style_pill(self.header_status, '● Sẵn sàng', GREEN, SUCCESS_BG)
            return
        def work():
            try:
                info = JobsClient(protect(protected, decrypt=True), machine).me()
                user = (info.get('user') or {}) if isinstance(info, dict) else {}
                name = user.get('username') or ''
                msg = 'Key hợp lệ' + (f' • {name}' if name else '')
                self.post_ui(lambda: self._apply_license(True, msg))
            except ValueError:
                self.post_ui(lambda: self._apply_license(False, 'Không đọc được API key đã lưu; nhập lại trong Cài đặt.', soft=True))
            except JobsError as exc:
                hard = not exc.temporary
                self.post_ui(lambda e=exc: self._apply_license(False if hard else None, str(e), soft=not hard))
            except Exception as exc:
                self.post_ui(lambda e=exc: self._apply_license(None, 'Chưa kiểm tra được key: ' + str(e)[:150], soft=True))
        self.run_background(work)

    def _apply_license(self, ok, message, soft=False):
        self.license_ok = ok            # True hợp lệ · False chặn · None chưa rõ (mạng)
        self.license_msg = message
        if not self.running:            # không đè trạng thái "Auto đang bật"
            if ok:
                _style_pill(self.header_status, '● Key hợp lệ', GREEN, SUCCESS_BG)
            elif ok is False and not soft:
                _style_pill(self.header_status, '● Key/máy bị chặn', RED, ERROR_BG)
            else:
                _style_pill(self.header_status, '● Chưa rõ key', MUTED, BORDER)
        self.log('Kiểm tra key: ' + message)

    def apply_proxy_rotation(self):
        """Nạp key của nguồn đang chọn; ProxyNo1 có xoay nền, ProxyVN xoay khi mở profile."""
        import proxy_pool
        src = proxy_source(self.settings)
        try:
            tagged = proxy_pool.source_keys(self.settings) if src != 'manual' else []
        except Exception:
            tagged = []
        keys = [k for _p, k in tagged]
        self.proxy_keys = keys
        mode = str(self.settings.get('proxy_rotate_mode', 'auto'))
        # Chỉ ProxyNo1 xoay nền được (endpoint cố định); ProxyVN đổi endpoint nên xoay khi mở profile.
        if src == 'proxyno1' and keys and mode == 'interval':
            minutes = max(1, int(self.settings.get('proxy_rotate_minutes', 15) or 15))
            run_rotation(keys, sink=lambda text: self.events.put(('log', text)), interval=minutes * 60)
            self.log(f'ProxyNo1: xoay IP mỗi {minutes} phút cho {len(keys)} key (luồng riêng, task đang chạy không bị dừng).')
        else:
            stop_rotation()
            if keys:
                name = 'ProxyVN' if src == 'pvn' else 'ProxyNo1'
                detail = 'xoay IP mỗi khi mở profile' if mode == 'auto' else 'xoay theo thời gian'
                self.log(f'{name}: {len(keys)} key • {detail}.')
        self.draw_proxy_status()

    def draw_proxy_status(self):
        if not self._alive:
            return
        source = proxy_source(self.settings)
        manual = len(self.settings.get('default_proxies') or ([self.settings['default_proxy']] if self.settings.get('default_proxy') else []))
        rows = rotation_status(self.proxy_keys) if source == 'proxyno1' and self.proxy_keys else []
        self.proxy_card.update_from(source, len(self.proxy_keys), manual, rows)

    def account_dialog(self):
        AccountsDialog(self).exec()

    def remove_account(self, account): self.remove_accounts([account])

    def check_selected_accounts(self, chosen):
        if not chosen:
            self.log('Chọn tài khoản trước khi Check live.'); return
        if self.running:
            self.log('Chrome đang chạy task; chờ batch kết thúc trước khi Check live riêng.'); return
        for account in chosen:
            self.run_account_action(account, False)

    def delete_dead_profiles(self):
        accounts = dead_accounts(self.accounts)
        if not accounts:
            self.log('Chưa có profile được xác nhận die. Dùng Check live trước.'); return
        self.remove_accounts(accounts, purge=True)

    def remove_accounts(self, accounts, purge=False):
        if not self.editable():
            return
        jobs = [copy.deepcopy(a) for a in accounts if a['id'] not in self.account_jobs]
        if not jobs:
            self.log('Chọn ít nhất một phiên rảnh để xóa.'); return
        removing = {a['id'] for a in jobs}
        for account in self.accounts:
            if account['id'] in removing:
                account['connection'] = 'Đang xóa…'
        for account in jobs:
            self.account_jobs[account['id']] = threading.Event()
        cfg = copy.deepcopy(self.settings); all_accounts = copy.deepcopy(self.accounts)
        self.request_accounts_paint()
        def worker():
            ok = 0
            for snapshot in jobs:
                self.events.put(('log', 'Đang xử lý tài khoản + profile: ' + snapshot['name']))
                try:
                    with ProfileLease(snapshot['profile'], cfg['lock_root']):
                        removed = False
                        # Chỉ chạy GPM: xoá profile trên GPM qua API (cả khi Xóa phiên lẫn Xóa profile die).
                        if cfg.get('use_gpm') or snapshot.get('gpm_profile_id'):
                            removed = delete_account_profile(cfg, snapshot)
                        elif purge:
                            removed = purge_managed_profile(cfg, snapshot, all_accounts)
                            if not removed:
                                raise RuntimeError('Chưa xác nhận được profile đã xóa; giữ lại tài khoản.')
                        # Dọn thêm thư mục profile cục bộ cũ (tài khoản tạo thời còn dùng Chrome), nếu có.
                        try: purge_managed_profile(cfg, snapshot, all_accounts)
                        except (ValueError, OSError): pass
                        session_snapshot_path(cfg, snapshot).unlink(missing_ok=True)
                    self.events.put(('account_deleted', snapshot['id'], removed, None)); ok += 1
                except Exception as exc:
                    self.events.put(('account_deleted', snapshot['id'], False, snapshot['name'] + ': ' + str(exc)))
            self.events.put(('log', f'Xoá phiên hoàn tất: {ok}/{len(jobs)} xong; phiên lỗi được giữ lại.'))
        self.run_background(worker)

    def run_account_action(self, account, login, url=None):
        if self.running:
            self.info('Đang chạy', 'Chờ batch kết thúc trước khi mở hoặc kiểm tra profile.'); return
        if account['id'] in self.account_jobs:
            self.log('Profile này đang mở hoặc đang kiểm tra.'); return
        cancel = threading.Event(); self.account_jobs[account['id']] = cancel
        if login:
            account['session_verified_at'] = 0; self.save()
        account['connection'] = 'Đang mở' if login else 'Đang kiểm tra'
        account['live_state'] = 'unknown'; account['session_verified_at'] = 0
        self.log(('Đang mở ' if login else 'Đang kiểm tra ') + account.get('name', 'profile') + '…')
        self.request_accounts_paint()
        cfg, snapshot = copy.deepcopy(self.settings), copy.deepcopy(account)
        if url: cfg['open_url'] = url
        def worker():
            ok = False
            try:
                if cancel.is_set(): raise Cancelled('Đã dừng kiểm tra tài khoản.')
                ok = account_browser(cfg, snapshot, cancel, lambda s: self.events.put(('log', s)), login=login)
                verified = bool(ok and not login)
                status = 'Live • Phiên hợp lệ' if verified else ('Chưa kiểm tra' if login else 'Die • Cần đăng nhập')
            except Cancelled:
                status = 'Chưa kiểm tra'; self.events.put(('log', 'Đã dừng thao tác tài khoản.'))
            except Exception as exc:
                status = 'Lỗi kết nối'; self.events.put(('log', str(exc)))
            if snapshot.get('hidemium_profile_id'):
                self.events.put(('browser_profile', account['id'], {k: snapshot[k] for k in
                                 ('browser_backend', 'hidemium_profile_id', 'hidemium_owned') if k in snapshot}))
            self.events.put(('account', account['id'], status, snapshot.get('name', '') if ok and not login else '', bool(ok and not login)))
        if login:
            self.run_background(worker)
        else:
            self.account_check_executor.submit(worker)

    def cancel_account_actions(self):
        for cancel in self.account_jobs.values():
            cancel.set()
        self.log('Đang đóng các profile GPM đang đăng nhập/kiểm tra…')

    # ---- thêm tài khoản / proxy / cookie --------------------------------------
    def login_account_dialog(self, facebook=False):
        if self.running:
            self.log('Chờ batch kết thúc trước khi đăng nhập tài khoản mới.'); return
        LoginDialog(self, facebook).exec()

    def facebook_cookie_batch_dialog(self):
        if self.running:
            self.log('Chờ batch kết thúc trước khi đăng nhập tài khoản mới.'); return
        if self.account_jobs:
            self.log('Đang có cửa sổ đăng nhập/kiểm tra mở; đóng chúng trước khi nhập cookie hàng loạt.'); return
        FacebookBatchDialog(self).exec()

    def proxy_dialog(self, account):
        if account is None:
            return
        if self.running or account['id'] in self.account_jobs:
            self.info('Đang dùng tài khoản', 'Dừng tác vụ/đăng nhập của tài khoản trước khi đổi proxy.'); return
        ProxyDialog(self, account).exec()

    def rename_account_dialog(self, account):
        if account is None:
            return
        current = str(account.get('name') or '').strip()
        new_name, ok = QInputDialog.getText(self, 'Đổi tên tài khoản', 'Nhập tên hiển thị mới cho tài khoản:', QLineEdit.Normal, current)
        if not ok:
            return
        new_name = new_name.strip()
        if not new_name:
            self.error('Tên không hợp lệ', 'Tên tài khoản không được để trống.'); return
        account['name'] = new_name
        self.save()
        self.request_accounts_paint()
        self.refresh_task_table()
        self.log(f'Đã đổi tên tài khoản thành "{new_name}".')

    def hidemium_profile_dialog(self, account):
        from account_browser_backend import account_backend
        if account in self.accounts and account_backend(account) != 'hidemium':
            self.error('Tài khoản Chrome', 'Tài khoản này thuộc Chrome. Thêm tài khoản Hidemium riêng để dùng profile Hidemium.'); return
        if self.running or account['id'] in self.account_jobs:
            self.info('Đang dùng tài khoản', 'Dừng tác vụ/đăng nhập của tài khoản trước khi đổi profile Hidemium.'); return
        current = str(account.get('hidemium_profile_id') or '')
        items = []
        try:
            from hidemium_browser import HidemiumClient
            client = HidemiumClient(self.settings.get('hidemium_api', 'http://127.0.0.1:2222'))
            rows = client.profiles(local=True) + client.profiles(local=False)
            seen = set()
            for row in rows:
                profile_id = str(row.get('uuid') or '').strip()
                if not profile_id or profile_id in seen:
                    continue
                seen.add(profile_id)
                items.append(f"{str(row.get('name') or '(không tên)').strip()}  •  {profile_id}")
            items.sort(key=str.casefold)
        except Exception as exc:
            self.log('Chưa đọc được danh sách Hidemium: ' + str(exc))
        if items:
            index = next((i for i, text in enumerate(items) if text.endswith('•  ' + current)), 0)
            choice, ok = QInputDialog.getItem(
                self, 'Liên kết Hidemium',
                'Chọn profile Hidemium (có thể gõ/dán UUID):', items, index, True)
            value = choice.rsplit('•', 1)[-1].strip() if ok else ''
        else:
            value, ok = QInputDialog.getText(
                self, 'Liên kết Hidemium',
                'Hidemium chưa mở hoặc chưa có profile. Dán UUID profile cần liên kết:',
                QLineEdit.Normal, current)
        if not ok:
            return
        value = value.strip()
        if not value:
            self.error('Thiếu UUID', 'Cần chọn hoặc nhập UUID Hidemium. Chưa thay đổi liên kết.'); return
        if value and not re.fullmatch(r'[A-Za-z0-9_-]{4,160}', value):
            self.error('UUID không hợp lệ', 'UUID Hidemium chỉ gồm chữ, số, dấu gạch ngang hoặc gạch dưới.'); return
        if value and any(a is not account and str(a.get('hidemium_profile_id') or '') == value for a in self.accounts):
            self.error('Profile đã được dùng', 'Một UUID Hidemium chỉ được liên kết với một tài khoản.'); return
        if value:
            account['browser_backend'] = 'hidemium'
            account['hidemium_profile_id'] = value
            account['connection'] = 'Hidemium • Chưa kiểm tra'
        else:
            account.pop('hidemium_profile_id', None)
            account['connection'] = 'Hidemium • Tự tìm theo tên'
        account['session_verified_at'] = 0
        self.save(); self.request_accounts_paint()
        self.log(('Đã liên kết Hidemium UUID cho ' if value else 'Đã bật tự tìm Hidemium theo tên cho ')
                 + account.get('name', 'tài khoản') + '.')

    def dola_cookie_import_dialog(self):
        if self.running:
            self.log('Chờ lượt chạy kết thúc trước khi nhập tài khoản Dola.'); return
        DolaCookieDialog(self).exec()

    def cookie_import_dialog(self):
        if self.running:
            self.info('Đang chạy', 'Chờ batch kết thúc trước khi thêm tài khoản.'); return
        GoogleCookieDialog(self).exec()

    def register_account(self, account, initial_cookies=None, closed_message='Đã hủy đăng nhập; chưa lưu tài khoản.'):
        """Mở Chrome đăng nhập Dola cho một tài khoản mới; tự nhận tên, lưu khi xong."""
        account.setdefault('browser_backend', 'hidemium' if self.settings.get('use_hidemium') else 'chrome')
        if account['browser_backend'] == 'hidemium':
            if account.get('hidemium_profile_id') and any(a.get('hidemium_profile_id') == account['hidemium_profile_id'] for a in self.accounts):
                self.error('Profile đã liên kết', 'Dùng Mở/Đăng nhập ở tài khoản Hidemium đã có.'); return
            account.setdefault('name', 'Hidemium mới')
        aid = account['id']
        cfg = copy.deepcopy(self.settings)
        cancel = threading.Event(); self.account_jobs[aid] = cancel
        def worker():
            saved = None; message = ''
            try:
                saved = register_dola_account(cfg, account, cancel, lambda s: self.events.put(('log', s)),
                                              DATA / 'profiles', initial_cookies=initial_cookies)
                if saved and initial_cookies:
                    try:
                        saved['cookie_file'] = save_cookies(DATA / 'cookie_vault', aid, initial_cookies)
                        self.events.put(('log', 'Đã lưu bản cookie Facebook mã hóa cho tài khoản; dùng Cookie ↻ để nạp lại khi cần.'))
                    except Exception:
                        self.events.put(('log', 'Đã đăng nhập nhưng chưa lưu được bản cookie Facebook riêng. Phiên trong profile vẫn được giữ.'))
            except Cancelled:
                message = closed_message
            except Exception as exc:
                message = safe_login_error(exc)
            self.events.put(('registered_account', aid, saved, message))
        self.run_background(worker)
        self.request_accounts_paint()

    def start_facebook_cookie_login(self, cookies, proxy, mode, backend='chrome'):
        aid = uuid.uuid4().hex
        self.register_account({'id': aid, 'profile': str(DATA / 'profiles' / aid), 'proxy': proxy,
                               'proxy_mode': mode, 'browser_backend': backend},
                              cookies, 'Đã hủy hoặc đóng cửa sổ; chưa lưu tài khoản này.')

    def start_facebook_cookie_batch(self, built, backend='chrome'):
        if not built:
            return
        concurrency = max(1, min(int(self.settings.get('concurrency', 2)), 2))
        self.log(f'Bắt đầu nạp {len(built)} tài khoản Facebook cookie (mở tối đa {concurrency} cửa sổ đồng thời)…')
        def run_batch():
            def process_one(item):
                cookies, proxy, mode = item
                aid = uuid.uuid4().hex
                account = {
                    'id': aid,
                    'profile': str(DATA / 'profiles' / aid),
                    'proxy': proxy,
                    'proxy_mode': mode,
                    'browser_backend': backend,
                }
                cfg = copy.deepcopy(self.settings)
                cancel = threading.Event()
                self.account_jobs[aid] = cancel
                self.request_accounts_paint()
                saved = None
                message = ''
                try:
                    saved = register_dola_account(
                        cfg, account, cancel,
                        lambda s: self.events.put(('log', s)),
                        DATA / 'profiles',
                        initial_cookies=cookies
                    )
                    if saved and cookies:
                        try:
                            saved['cookie_file'] = save_cookies(DATA / 'cookie_vault', aid, cookies)
                            self.events.put(('log', 'Đã lưu bản cookie Facebook mã hóa cho tài khoản; dùng Cookie ↻ để nạp lại khi cần.'))
                        except Exception:
                            self.events.put(('log', 'Đã đăng nhập nhưng chưa lưu được bản cookie Facebook riêng. Phiên trong profile vẫn được giữ.'))
                except Cancelled:
                    message = 'Đã hủy hoặc đóng cửa sổ; chưa lưu tài khoản này.'
                except Exception as exc:
                    message = safe_login_error(exc)
                self.events.put(('registered_account', aid, saved, message))

            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                for future in as_completed([executor.submit(process_one, item) for item in built]):
                    try:
                        future.result()
                    except Exception as e:
                        self.events.put(('log', f'Lỗi khi xử lý tài khoản Facebook cookie: {e}'))
        self.run_background(run_batch)

    def commit_accounts(self, added, message):
        for account in added:
            account.setdefault('browser_backend', 'hidemium' if self.settings.get('use_hidemium') else 'chrome')
        self.accounts.extend(added)
        self.save(); self.request_accounts_paint(); self.log(message)
        return added

    def start_cookie_accounts(self, accounts):
        if self.running:
            self.info('Đang chạy', 'Chờ batch kết thúc trước khi đăng nhập cookie.'); return
        cfg = copy.deepcopy(self.settings); jobs = []
        for account in accounts:
            if account['id'] in self.account_jobs: continue
            cancel = threading.Event(); self.account_jobs[account['id']] = cancel
            account['connection'] = 'Chờ đăng nhập cookie'; jobs.append((copy.deepcopy(account), cancel))
        self.request_accounts_paint()
        def execute(account, cancel):
            name = ''
            try:
                if cancel.is_set(): raise RuntimeError('cancelled')
                name = cookie_account_browser(cfg, account, cancel, lambda s: self.events.put(('log', f"{account['name']}: {s}")))
                status = 'Phiên hợp lệ'
            except Exception as exc:
                from dola_browser import SessionDead
                status = 'Đã dừng' if cancel.is_set() else ('Cần đăng nhập/xác minh' if isinstance(exc, SessionDead) else 'Lỗi đăng nhập cookie')
                self.events.put(('log', f"{account['name']}: {status} • {safe_login_error(exc)}"))
            if account.get('hidemium_profile_id'):
                self.events.put(('browser_profile', account['id'], {k: account[k] for k in
                                 ('browser_backend', 'hidemium_profile_id', 'hidemium_owned') if k in account}))
            self.events.put(('account', account['id'], status, name, status == 'Phiên hợp lệ'))
        def run():
            with ThreadPoolExecutor(max_workers=max(1, min(int(cfg['concurrency']), len(jobs)))) as executor:
                for future in as_completed([executor.submit(execute, a, c) for a, c in jobs]):
                    future.result()
        if jobs: self.run_background(run)

    def import_original_accounts(self):
        self.log('Đang tìm profile từ bộ cài gốc…')
        def work():
            discovered = discover_accounts()
            def apply():
                if not discovered:
                    self.info('Không tìm thấy', 'Không tìm thấy thư mục tài khoản của bộ cài gốc.'); return
                for found in discovered:
                    previous = next((a for a in self.accounts if a['name'] == found['name']), None)
                    if previous: previous['profile'] = found['profile']
                    else: self.accounts.append(found)
                self.later_save(); self.request_accounts_paint()
                self.log('Đã liên kết profile từ bộ cài gốc. Bấm Kiểm tra để xác thực phiên.')
            self.post_ui(apply)
        self.run_background(work)

    # ---- cài đặt --------------------------------------------------------------
    def settings_dialog(self): SettingsDialog(self).exec()
    def sheet_settings_dialog(self): SheetSettingsDialog(self).exec()

    # ---- log ------------------------------------------------------------------
    def log(self, text):
        if not hasattr(self, 'log_box'):
            return
        self.log_box.appendPlainText(f'[{time.strftime("%H:%M:%S")}] {text}')

    def closeEvent(self, ev):
        if self.account_jobs:
            for cancel in self.account_jobs.values(): cancel.set()
        if self.running:
            if getattr(self, '_close_when_finished', False):
                ev.ignore(); return
            if not self.ask('Đang chạy tác vụ', 'Dừng batch, báo trả job chưa xong về web rồi đóng ứng dụng?'):
                ev.ignore(); return
            self._close_when_finished = True
            self.cancel_batch()
            self.log('Đang dừng và gửi báo cáo trước khi đóng. Báo cáo lỗi mạng được lưu để gửi lại khi bật Auto.')
            ev.ignore(); return
        self._alive = False
        self.flush_task_logs()
        self.save()
        self.workspace_writer.flush()  # ghi xong cấu hình trước khi thoát
        try: self.account_check_executor.shutdown(wait=False, cancel_futures=True)
        except Exception: pass
        ev.accept()


# --------------------------------------------------------------------------- helpers
class _Var:
    def __init__(self, value): self._v = value
    def get(self): return self._v
    def set(self, v): self._v = v


def _btn(text, fn, primary=False, color=None):
    b = QPushButton(text.replace('&', '&&')); b.clicked.connect(fn)  # '&' là chữ thật, không phải phím tắt
    b.setObjectName('primary' if primary else 'tool')
    if color: b.setStyleSheet(f'color: {color};')
    return b


def _muted(text):
    lab = QLabel(text); lab.setObjectName('muted'); lab.setWordWrap(True); return lab


def _section(text):
    lab = QLabel(text); lab.setObjectName('section'); return lab


def _error(text):
    lab = QLabel(text); lab.setWordWrap(True); lab.setStyleSheet(f'color: {RED};'); return lab


def _check(text, value, fn):
    c = QCheckBox(text); c.setChecked(bool(value)); c.stateChanged.connect(lambda _=0: fn())
    return c


def _style_pill(label, text, fg, bg):
    label.setText(text)
    label.setStyleSheet(f'color: {fg}; background: {bg}; border-radius: 10px; padding: 3px 10px;'
                        ' font-size: 11px; font-weight: 600;')


def _pill(text, fg, bg):
    lab = QLabel(); _style_pill(lab, text, fg, bg); lab.setAlignment(Qt.AlignCenter)
    lab.setFixedHeight(24); return lab


# Trạng thái tài khoản: (khóa, nhãn, màu chữ, màu nền)
ACCOUNT_STATES = {
    'busy': ('Đang xử lý', CYAN, ACTIVE_BG),
    'live': ('Live', GREEN, SUCCESS_BG),
    'credit': ('Hết điểm', WARNING, WARNING_BG),
    'work_limit': ('Giới hạn Work (chỉ Chat)', CYAN, ACTIVE_BG),
    'rate_limit': ('Rate Limit', WARNING, WARNING_BG),
    'rest': ('Nghỉ 7 ngày', CYAN, ACTIVE_BG),
    'die': ('Die', RED, ERROR_BG),
    'error': ('Lỗi kết nối', WARNING, WARNING_BG),
    'unknown': ('Chưa kiểm tra', MUTED, BORDER),
}
AVATAR_COLORS = ['#167D79', '#2F6F8F', '#6B5A99', '#8F6A2F', '#2F8F5B', '#8F3F5A', '#3F5F8F']


def account_state(account, busy=False):
    conn = str(account.get('connection') or '')
    low = conn.casefold()
    if busy or low.startswith('đang'): return 'busy'
    if credit_locked(account): return 'credit'
    if rate_limited(account): return 'rate_limit'
    if account.get('live_state') == 'die' or low.startswith('die'): return 'die'
    if run_cooldown_locked(account): return 'rest'
    if work_locked(account): return 'work_limit'
    if account.get('live_state') == 'live' or 'hợp lệ' in low or low.startswith('live'): return 'live'
    if 'lỗi' in low: return 'error'
    return 'unknown'


def account_detail(account):
    """Dòng phụ: chi tiết trạng thái (bỏ phần đã có trên nhãn)."""
    if credit_locked(account):
        return credit_lock_text(account)
    if rate_limited(account):
        return rate_limit_text(account)
    if work_locked(account):
        return work_lock_text(account)
    if run_cooldown_locked(account):
        return run_cooldown_text(account)
    conn = str(account.get('connection') or '')
    for prefix in ('Die • ', 'Live • '):
        if conn.startswith(prefix):
            return conn[len(prefix):]
    return '' if conn in ('Phiên hợp lệ', 'Chưa kiểm tra') else conn


class StateBar(QWidget):
    """Thanh tỉ lệ Live / Hết điểm / Die / khác."""
    def __init__(self):
        super().__init__(); self.setFixedHeight(6); self.parts = []

    def set_parts(self, parts):
        self.parts = [(n, c) for n, c in parts if n]; self.update()

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing); p.setPen(Qt.NoPen)
        r = self.rect(); p.setBrush(QColor(BORDER)); p.drawRoundedRect(r, 3, 3)
        total = sum(n for n, _ in self.parts); x = r.left()
        for i, (n, color) in enumerate(self.parts):
            w = r.right() - x + 1 if i == len(self.parts) - 1 else round(r.width() * n / total)
            p.setBrush(QColor(color)); p.drawRoundedRect(QRect(x, r.top(), w, r.height()), 3, 3); x += w
        p.end()


class AccountSummary(QFrame):
    """Thẻ tài khoản ở thanh bên: số lượng theo trạng thái, bấm để mở Quản lý tài khoản."""
    def __init__(self, on_click):
        super().__init__(); self.setObjectName('acccard'); self.setCursor(Qt.PointingHandCursor)
        self.on_click = on_click
        lay = QVBoxLayout(self); lay.setContentsMargins(12, 10, 12, 10); lay.setSpacing(6)
        head = QHBoxLayout()
        self.total = QLabel('0'); self.total.setObjectName('acctotal')
        cap = QLabel('tài khoản'); cap.setObjectName('muted')
        head.addWidget(self.total); head.addWidget(cap, 0, Qt.AlignBottom); head.addStretch(1)
        self.running = _pill('0 dùng chạy', CYAN, ACTIVE_BG); head.addWidget(self.running)
        lay.addLayout(head)
        self.bar = StateBar(); lay.addWidget(self.bar)
        grid = QGridLayout(); grid.setHorizontalSpacing(8); grid.setVerticalSpacing(2)
        self.counts = {}
        for i, key in enumerate(('live', 'die', 'credit', 'unknown')):
            label, fg, _bg = ACCOUNT_STATES[key]
            cell = QLabel(); cell.setObjectName('statcell'); self.counts[key] = (cell, label, fg)
            grid.addWidget(cell, i // 2, i % 2)
        lay.addLayout(grid)

    def update_from(self, accounts, busy_ids=()):
        states = [account_state(a, a.get('id') in busy_ids) for a in accounts]
        n = {key: states.count(key) for key in ACCOUNT_STATES}
        n['unknown'] += n['busy'] + n['error']
        self.total.setText(str(len(accounts)))
        _style_pill(self.running, f"{sum(bool(a.get('selected', True)) for a in accounts)} dùng chạy", CYAN, ACTIVE_BG)
        for key, (cell, label, fg) in self.counts.items():
            cell.setText(f'<span style="color:{fg}">●</span> {label} <b>{n[key]}</b>')
        self.bar.set_parts([(n['live'], GREEN), (n['credit'], WARNING), (n['die'], RED), (n['unknown'], SCROLL)])

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton: self.on_click()
        super().mousePressEvent(ev)


def _clock_text(seconds):
    seconds = int(max(0, seconds))
    return f'{seconds // 60:02d}:{seconds % 60:02d}'


class ProxySummary(QFrame):
    """Thẻ proxy ở thanh bên: ProxyNo1 tự đổi IP (đếm ngược, kết quả lần gần nhất) hoặc proxy tay."""
    def __init__(self, on_click):
        super().__init__(); self.setObjectName('acccard'); self.setCursor(Qt.PointingHandCursor)
        self.on_click = on_click
        lay = QVBoxLayout(self); lay.setContentsMargins(12, 8, 12, 8); lay.setSpacing(3)
        head = QHBoxLayout()
        title = QLabel('PROXY'); title.setObjectName('navsection'); head.addWidget(title); head.addStretch(1)
        self.badge = _pill('', MUTED, BORDER); head.addWidget(self.badge)
        lay.addLayout(head)
        self.line1 = QLabel(''); self.line1.setObjectName('statcell')
        self.line2 = QLabel(''); self.line2.setObjectName('statcell'); self.line2.setWordWrap(True)
        lay.addWidget(self.line1); lay.addWidget(self.line2)
        self.setToolTip('Bấm để mở Cài đặt proxy')

    def update_from(self, source, key_count, manual_count, rows):
        if source == 'manual':
            _style_pill(self.badge, 'Proxy tay', CYAN, ACTIVE_BG)
            self.line1.setText(f'{manual_count} proxy • gán lần lượt cho profile')
            self.line2.setText('')
            return
        name = 'ProxyVN' if source == 'pvn' else 'ProxyNo1'
        if not key_count:
            _style_pill(self.badge, name, WARNING, WARNING_BG)
            self.line1.setText('Chưa có key • nhập trong Cài đặt')
            self.line2.setText('')
            return
        _style_pill(self.badge, name, GREEN, SUCCESS_BG)
        if source == 'pvn':
            self.line1.setText(f'{key_count} key • mỗi luồng 1 key • xoay khi mở profile')
            self.line2.setText('')
            return
        waits = [r['next_in'] for r in rows if r['next_in'] is not None]
        chrome = sum(r['chrome'] for r in rows)
        soon = f'đổi IP sau {_clock_text(min(waits))}' if waits else 'đang chờ'
        self.line1.setText(f'{key_count} key • {chrome} Chrome • {soon}')
        last = max((r for r in rows if r['last_wall']), key=lambda r: r['last_wall'], default=None)
        if last is None:
            self.line2.setText('Chưa đổi IP lần nào (15 phút/lần)')
        else:
            mark, color = ('✓', GREEN) if last['last_ok'] else ('✗', RED)
            when = time.strftime('%H:%M', time.localtime(last['last_wall']))
            self.line2.setText(f'<span style="color:{color}">{mark}</span> {when} key {last["index"]}: {last["last_text"][:60]}')

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton: self.on_click()
        super().mousePressEvent(ev)


# --------------------------------------------------------------------------- dialogs
class PromptDialog(QDialog):
    def __init__(self, app, task):
        super().__init__(app)
        self.app, self.task = app, task
        self.setWindowTitle('Nhập prompt • ' + task['name']); self.resize(720, 620)
        lay = QVBoxLayout(self)
        lay.addWidget(_muted(f"Prompt của {task['name']}:"))
        self.edit = QPlainTextEdit(); self.edit.setPlainText(task.get('prompt', '')); lay.addWidget(self.edit, 1)
        self.count = _muted('0 ký tự'); lay.addWidget(self.count)
        self.edit.textChanged.connect(lambda: self.count.setText(f'{len(self.edit.toPlainText())} ký tự'))
        self.count.setText(f"{len(task.get('prompt',''))} ký tự")
        imgrow = QHBoxLayout()
        self.img_label = _muted(f"Ảnh tham chiếu ({len(task.get('images') or [])}/10)"); imgrow.addWidget(self.img_label)
        imgrow.addStretch(1)
        imgrow.addWidget(_btn('+ Thêm ảnh', self.add_images))
        imgrow.addWidget(_btn('Xóa ảnh', self.clear_images))
        lay.addLayout(imgrow)
        bar = QHBoxLayout()
        bar.addWidget(_btn('Mở hội thoại', self.open_conversation))
        bar.addWidget(_btn('Lấy kết quả cũ', self.resume))
        bar.addStretch(1)
        save = _btn('Lưu prompt', self.accept_save, primary=True); bar.addWidget(save)
        lay.addLayout(bar)

    def add_images(self):
        paths, _ = QFileDialog.getOpenFileNames(self, 'Chọn ảnh tham chiếu (tối đa 10)', '', 'Hình ảnh (*.png *.jpg *.jpeg *.webp *.bmp)')
        if not paths: return
        images = self.task.setdefault('images', [])
        for p in paths:
            if p not in images and len(images) < 10:
                images.append(p)
        self.img_label.setText(f'Ảnh tham chiếu ({len(images)}/10)')

    def clear_images(self):
        self.task['images'] = []; self.img_label.setText('Ảnh tham chiếu (0/10)')

    def open_conversation(self):
        url = self.task.get('conversation_url', '')
        if re.fullmatch(r'https://www\.dola\.com/chat/\d+', url):
            account = next((a for a in self.app.accounts if a['id'] == self.task.get('conversation_account')), None)
            if account: self.app.run_account_action(account, True, url)
            else: self.app.info('Thiếu tài khoản', 'Liên kết lại tài khoản đã tạo hội thoại này.')
        else:
            self.app.info('Chưa có hội thoại', 'Hội thoại sẽ được lưu sau khi gửi prompt lên Dola.')

    def _commit(self):
        value = self.edit.toPlainText()
        if value != self.task.get('prompt', ''):
            self.task.pop('conversation_url', None); self.task.pop('conversation_account', None)
            self.task['prompt'] = value

    def resume(self):
        self._commit(); self.app.save(); self.accept(); self.app.start_batch(True, resume=True)

    def accept_save(self):
        self._commit(); self.app.save(); self.accept()


class ConfigDialog(QDialog):
    MODE_LABELS = {'pro': 'Pro', 'fast': 'Fast', 'skill30': 'Skill 30s'}
    DURATION_LABELS = {
        '5': '5 giây',
        '10': '10 giây',
        '15': '15 giây · một video',
        '30': '30 giây · ghép 2 × 15s',
    }

    def __init__(self, app, tasks, shared):
        super().__init__(app)
        self.app, self.tasks, self.shared = app, tasks, shared
        self.setWindowTitle(f'Cấu hình chung • {len(tasks)} task' if shared else 'Cấu hình • ' + tasks[0]['name'])
        self.resize(440, 440)
        lay = QVBoxLayout(self)
        configs = [task_settings(app.settings, t) for t in tasks]
        self.unchanged = 'Giữ riêng từng task'
        self.mode_values = {v: k for k, v in self.MODE_LABELS.items()}
        self.duration_values = {v: k for k, v in self.DURATION_LABELS.items()}
        self.combos = {}
        for key, title, choices in (('duration', 'Thời lượng video', list(self.DURATION_LABELS.values())),
                                    ('model', 'Model', ['Seedance 2.5', 'Seedance 2.0']),
                                    ('dola_mode', 'Chế độ Dola', list(self.MODE_LABELS.values())),
                                    ('ratio', 'Tỷ lệ khung hình', ['9:16', '16:9', '1:1', '4:3', '3:4'])):
            lay.addWidget(_muted(title))
            if key == 'dola_mode':
                values = [self.MODE_LABELS.get(str(c[key]).lower(), 'Pro') for c in configs]
            elif key == 'duration':
                values = [self.DURATION_LABELS.get(str(c[key]).rstrip('s'), str(c[key])) for c in configs]
            else:
                values = [str(c[key]) for c in configs]
            value = values[0] if len(set(values)) == 1 else self.unchanged
            items = ([self.unchanged] + choices) if shared else choices
            combo = QComboBox(); combo.addItems(items)
            combo.setCurrentText(value if value in items else items[0])
            lay.addWidget(combo); self.combos[key] = combo
        lay.addWidget(_muted('Timeout task (giây) — để trống dùng Cài đặt'))
        tvals = [str(c.get('timeout', 900)) for c in configs]
        self.timeout = QLineEdit(tvals[0] if len(set(tvals)) == 1 else ''); lay.addWidget(self.timeout)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.save); bb.rejected.connect(self.reject); lay.addWidget(bb)

    def save(self):
        updates = {}
        for key, combo in self.combos.items():
            val = combo.currentText()
            if val == self.unchanged:
                continue
            if key == 'dola_mode':
                updates[key] = self.mode_values.get(val, 'pro')
            elif key == 'duration':
                updates[key] = self.duration_values[val]
            else:
                updates[key] = val
        raw = self.timeout.text().strip()
        current = {t['id']: t for t in self.app.tasks}
        if raw:
            try:
                seconds = int(raw)
                if not 30 <= seconds <= 7200: raise ValueError
            except ValueError:
                self.app.error('Timeout không hợp lệ', 'Nhập số giây từ 30 đến 7200.'); return
            updates['timeout'] = seconds
        else:
            for t in self.tasks:
                opts = current.get(t['id'], {}).get('options') or {}
                opts.pop('timeout', None)
                if opts: current[t['id']]['options'] = opts
                elif t['id'] in current: current[t['id']].pop('options', None)
        for t in self.tasks:
            if t['id'] in current:
                if updates:
                    current[t['id']].setdefault('options', {}).update(updates)
                self.app.update_task_row(current[t['id']])
        self.app.save(); self.app.log(f'Đã lưu cấu hình cho {len(self.tasks)} task.'); self.accept()


class SheetSettingsDialog(QDialog):
    def __init__(self, app):
        super().__init__(app); self.app = app
        self.setWindowTitle('Cài đặt Auto Sheet'); self.resize(640, 440)
        lay = QVBoxLayout(self)
        lay.addWidget(_muted('A: STT • B: Prompt • F: số video 30s • G: số video 15s • L: thư mục 30s • M: thư mục 15s'))
        self.entries = {}
        for key, label, default in (('sheet_video_url', 'Link hoặc ID Google Sheet', DEFAULT_URL),
                                    ('sheet_video_tab', 'Tên tab', 'Sheet1'),
                                    ('sheet_video_credentials', 'File JSON service account', '')):
            lay.addWidget(_muted(label))
            e = QLineEdit(str(app.settings.get(key, default))); lay.addWidget(e); self.entries[key] = e
        lay.addWidget(_btn('Chọn file JSON', self.choose))
        self.return_errors = QCheckBox('Trả lại số lượng khi video kết thúc lỗi')
        self.return_errors.setChecked(app.settings.get('sheet_return_errors', True)); lay.addWidget(self.return_errors)
        lay.addWidget(_muted('Chia sẻ Sheet cho client_email trong JSON với quyền Editor. Chỉ chạy một máy nhận job.'))
        self.status = QLabel(''); self.status.setObjectName('cyan'); lay.addWidget(self.status)
        bar = QHBoxLayout()
        bar.addWidget(_btn('Kiểm tra kết nối', self.test))
        bar.addStretch(1)
        bar.addWidget(_btn('Lưu', self.save, primary=True)); lay.addLayout(bar)

    def values(self):
        return {k: e.text().strip() for k, e in self.entries.items()}

    def choose(self):
        value, _ = QFileDialog.getOpenFileName(self, 'Chọn JSON', '', 'Service account JSON (*.json)')
        if value: self.entries['sheet_video_credentials'].setText(value)

    def test(self):
        from sheet_video import SheetAPI, config, scan_video_rows
        try: cfg = config(self.values())
        except ValueError as exc: self.status.setText(str(exc)); return
        self.status.setText('Đang kiểm tra quyền đọc Sheet…')
        def work():
            try:
                rows = SheetAPI(cfg).rows()
                eligible, issues, _ = scan_video_rows(rows)
                total = sum(n30 + n15 for _, _, _, n30, n15 in eligible)
                message = f'Đọc {len(rows)} dòng • {len(eligible)} dòng hợp lệ • {total} video; chưa nhận job.'
                if issues:
                    message += '\n' + issues[0]
            except Exception as exc:
                message = str(exc)[:200]
            self.app.post_ui(lambda: self.status.setText(message))
        self.app.run_background(work)

    def save(self):
        from sheet_video import config
        settings = self.values()
        try: config(settings)
        except ValueError as exc: self.status.setText(str(exc)); return
        self.app.settings.update(settings, sheet_return_errors=self.return_errors.isChecked())
        self.app.save(); self.app.log('Đã lưu cấu hình Auto Sheet. Áp dụng từ lần bật tiếp theo.'); self.accept()


class SettingsDialog(QDialog):
    SOURCE_LABELS = {'API hội thoại · ưu tiên không logo, luôn dự phòng link Dola': 'api_preview',
                     'Tự động (ưu tiên không logo)': 'auto',
                     'Ưu tiên không logo · vẫn dự phòng link Dola': 'no_logo',
                     'Tải thông thường (có thể có logo)': 'standard',
                     'Ghi video preview': 'record_element'}
    ENGINES = {'dola': 'Dola AI — Tự động trình duyệt', 'local': 'Xuất video từ ảnh (cục bộ)', 'adapter': 'AI qua adapter Python'}
    PROXY_SOURCES = {'ProxyNo1': 'proxyno1', 'ProxyVN': 'pvn', 'Proxy nhập tay': 'manual'}
    ROTATE_MODES = {'Tự động (mở profile thì xoay)': 'auto', 'Theo thời gian (phút)': 'interval'}
    BROWSER_BACKENDS = {'Chrome Seedance (đi kèm)': 'chrome', 'Hidemium 4': 'hidemium'}

    def __init__(self, app):
        super().__init__(app); self.app = app; s = app.settings
        self.setWindowTitle('Cài đặt hệ thống'); self.resize(720, 760)
        outer = QVBoxLayout(self)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); inner = QWidget(); form = QVBoxLayout(inner)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(inner); outer.addWidget(scroll, 1)
        self.fields = {}
        form.addWidget(_section('THƯ MỤC DỮ LIỆU'))
        form.addWidget(_muted('Nơi lưu tài khoản, cấu hình, nhật ký. Đổi xong khởi động lại tool để áp dụng.'))
        drow = QHBoxLayout()
        self.data_dir_field = QLineEdit(str(DATA)); self.data_dir_field.setReadOnly(True); drow.addWidget(self.data_dir_field, 1)
        drow.addWidget(_btn('Đổi…', self._choose_data_dir))
        dwrap = QWidget(); dwrap.setLayout(drow); form.addWidget(dwrap)
        form.addSpacing(8)
        form.addWidget(_section('AUTO • WEB'))
        form.addWidget(_muted('Chỉ nhận job khi bật Chạy Auto và còn tab trống. Dùng cấu hình video bên dưới.\n'
                              'Job lỗi được giữ trên bảng. Tick Tự chạy lại để thử tiếp cùng job; Dừng sẽ trả job chưa xong về web.'))
        form.addWidget(_muted('API key của web (ss_…, do admin cấp)'))
        self.saved_auto_key = s.get('auto_render_key_protected', '')
        self.auto_key_readable = True
        self.auto_key = QLineEdit(); self.auto_key.setEchoMode(QLineEdit.Password)
        if self.saved_auto_key:
            try: self.auto_key.setText(protect(self.saved_auto_key, decrypt=True))
            except ValueError: self.auto_key_readable = False
        form.addWidget(self.auto_key)
        form.addWidget(_muted('Tên máy hiện trên web (ví dụ: cuong)'))
        self.auto_machine = QLineEdit(s.get('auto_machine_code', '')); form.addWidget(self.auto_machine)
        # Mã máy phần cứng: key chỉ chạy trên máy admin đã duyệt (Quản trị web → Máy chờ gắn API key).
        machine_label = _muted(f'Mã máy của PC này: {MACHINE_ID} — gửi admin duyệt / điền khi tạo key')
        machine_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        form.addWidget(machine_label)
        form.addWidget(_muted('API: https://view.botlc.pro/api/v1/jobs (claim • heartbeat • report)\n'
                              'Kết quả: path = D:/videos/38/v1042-1-….mp4; folder = D:/videos/38'))
        form.addSpacing(8)
        form.addWidget(_muted('Chế độ không logo'))
        self.source = QComboBox(); self.source.addItems(list(self.SOURCE_LABELS))
        cur_src = s.get('video_source', 'api_preview')
        self.source.setCurrentText(next((k for k, v in self.SOURCE_LABELS.items() if v == cur_src), next(iter(self.SOURCE_LABELS))))
        form.addWidget(self.source)
        form.addWidget(_muted('Bộ xử lý'))
        self.engine = QComboBox(); self.engine.addItems(list(self.ENGINES.values()))
        self.engine.setCurrentText(self.ENGINES[s.get('engine', 'dola')]); form.addWidget(self.engine)
        self.upscale = QCheckBox('Nâng cấp video 1080p'); self.upscale.setChecked(bool(s.get('upscale', True))); form.addWidget(self.upscale)
        for key, title in [('concurrency', 'Số luồng Chrome / tài khoản đồng thời (1–100)'),
                           ('tabs_per_profile', 'Số tab Dola mỗi tài khoản (1–5)'),
                           ('timeout', 'Timeout mỗi task (30–7200 giây, mặc định 900)'),
                           ('action_delay', 'Nhịp nghỉ cơ sở (0.2–10s)'),
                           ('browser', 'Trình duyệt Chromium/Chrome (chrome.exe)'),
                           ('ffmpeg', 'Đường dẫn ffmpeg.exe'),
                           ('output', 'Thư mục lưu video')]:
            form.addWidget(_muted(title))
            row = QHBoxLayout(); e = QLineEdit(str(s.get(key, ''))); row.addWidget(e, 1); self.fields[key] = e
            if key in ('browser', 'ffmpeg'):
                row.addWidget(_btn('Chọn…', lambda _=0, ee=e: self._pick_file(ee)))
            if key == 'output':
                row.addWidget(_btn('Chọn…', lambda _=0, ee=e: self._pick_dir(ee)))
            w = QWidget(); w.setLayout(row); form.addWidget(w)
        form.addSpacing(8)
        form.addWidget(_section('TRÌNH DUYỆT'))
        form.addWidget(_muted('Trình duyệt mặc định cho tài khoản mới. Tài khoản đã lưu luôn dùng đúng loại trình duyệt của nó.'))
        self.browser_backend = QComboBox(); self.browser_backend.addItems(list(self.BROWSER_BACKENDS))
        current_backend = 'hidemium' if s.get('use_hidemium') else s.get('browser_backend', 'chrome')
        self.browser_backend.setCurrentText(next((label for label, value in self.BROWSER_BACKENDS.items()
                                                  if value == current_backend), next(iter(self.BROWSER_BACKENDS))))
        form.addWidget(self.browser_backend)
        form.addWidget(_muted('Hidemium Local API (mặc định Hidemium 4: http://127.0.0.1:2222)'))
        self.hidemium_api = QLineEdit(str(s.get('hidemium_api', 'http://127.0.0.1:2222')))
        form.addWidget(self.hidemium_api)
        form.addWidget(_muted('Khi dùng Hidemium, mở Quản lý tài khoản → Hidemium UUID để liên kết mỗi tài khoản với một profile riêng.'))
        from seedance_ui import FLOW_LABELS
        self.seedance_flow_values = FLOW_LABELS
        self.seedance_flow = QComboBox(); self.seedance_flow.addItems(list(FLOW_LABELS))
        self.seedance_flow.setCurrentText(next((label for label, value in FLOW_LABELS.items()
                                              if value == s.get('seedance_flow', 'auto')), next(iter(FLOW_LABELS))))
        form.addWidget(self.seedance_flow)
        form.addWidget(_muted('Cơ bản chọn thông số trên form. Pro gửi trong chat thường.\nTự động: Pro 5/10/15s dùng chat; Fast dùng form; 30s ghép hai phần 15s.'))
        self.gpm_hidden = QCheckBox('Chạy ẩn (đưa Chrome ra ngoài màn hình khi chạy video)')
        self.gpm_hidden.setChecked(not bool(s.get('show_browser', True)))
        form.addWidget(self.gpm_hidden)
        form.addSpacing(8)
        form.addWidget(_section('PROXY'))
        form.addWidget(_muted('Chọn nguồn proxy: ProxyNo1, ProxyVN, hoặc Proxy nhập tay. '
                              'Chọn nguồn nào thì điền key/proxy của nguồn đó bên dưới.\n'
                              'Với ProxyNo1/ProxyVN: mỗi luồng 1 key riêng; số luồng = số key còn sống.'))
        self.proxy_source = QComboBox(); self.proxy_source.addItems(list(self.PROXY_SOURCES))
        _cur_src = proxy_source(s)
        self.proxy_source.setCurrentText(next(k for k, v in self.PROXY_SOURCES.items() if v == _cur_src))
        form.addWidget(self.proxy_source)
        form.addWidget(_muted('Proxy tay mặc định — mỗi dòng một proxy: host:port hoặc host:port:user:pass'))
        self.manual_proxy = QPlainTextEdit(); self.manual_proxy.setFixedHeight(110)
        self.manual_proxy.setLineWrapMode(QPlainTextEdit.NoWrap)
        try:
            self.manual_proxy.setPlainText(manual_proxy_text(s))
        except ValueError:
            form.addWidget(_error('Không đọc được proxy tay đã lưu.'))
        form.addWidget(self.manual_proxy)
        form.addWidget(_muted('Nhập 1 hoặc nhiều dòng. Gán lần lượt cho profile dùng mặc định; hết danh sách thì quay về đầu. Mỗi Chrome giữ một proxy cho mọi tab.'))
        form.addWidget(_muted('API key ProxyNo1 — mỗi dòng một key (xem ở Dashboard, cột APIKey)'))
        self.key_box = QPlainTextEdit(); self.key_box.setFixedHeight(90)
        self.keys_ok = True
        try:
            self.key_box.setPlainText(keys_text(s))
        except ValueError:
            self.keys_ok = False
            form.addWidget(_error('Không giải mã được key đã lưu. Nhập lại để lưu key mới.'))
        form.addWidget(self.key_box)
        form.addWidget(_muted('API key ProxyVN (proxy.vn) — mỗi dòng một key xoay'))
        self.vn_key_box = QPlainTextEdit(); self.vn_key_box.setFixedHeight(90)
        self.vn_keys_ok = True
        try:
            self.vn_key_box.setPlainText(protect(s.get('proxyvn_keys_protected', ''), decrypt=True)
                                         if s.get('proxyvn_keys_protected') else '')
        except ValueError:
            self.vn_keys_ok = False
            form.addWidget(_error('Không giải mã được key ProxyVN đã lưu. Nhập lại để lưu key mới.'))
        form.addWidget(self.vn_key_box)
        form.addWidget(_muted('Chrome: mỗi luồng dùng MỘT key riêng; số luồng chạy = số key còn sống '
                              '(cắt theo số tài khoản). Key die thì bỏ, giảm 1 luồng.'))
        form.addWidget(_muted('Chế độ xoay IP'))
        self.rotate_mode = QComboBox(); self.rotate_mode.addItems(list(self.ROTATE_MODES))
        cur_mode = 'interval' if str(s.get('proxy_rotate_mode', 'auto')) == 'interval' else 'auto'
        self.rotate_mode.setCurrentText(next(k for k, v in self.ROTATE_MODES.items() if v == cur_mode))
        form.addWidget(self.rotate_mode)
        form.addWidget(_muted('Thời gian xoay (phút) — chỉ dùng khi chọn "Theo thời gian"'))
        self.rotate_minutes = QLineEdit(str(s.get('proxy_rotate_minutes', 15))); form.addWidget(self.rotate_minutes)
        self.proxy_status = QLabel(''); self.proxy_status.setWordWrap(True); form.addWidget(self.proxy_status)
        self.proxy_busy = False
        try_row = QHBoxLayout(); try_row.addWidget(_btn('Kiểm tra các key', self.try_proxy_keys))
        try_row.addWidget(_btn('Đổi IP ngay', self.rotate_proxy_now)); try_row.addStretch(1)
        form.addLayout(try_row)
        form.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.save); bb.rejected.connect(self.reject); outer.addWidget(bb)

    def _proxy_status(self, text, color):
        self.proxy_status.setText(text); self.proxy_status.setStyleSheet(f'color: {color};')

    def _choose_data_dir(self):
        folder = QFileDialog.getExistingDirectory(self, 'Chọn thư mục lưu dữ liệu', str(DATA))
        if not folder:
            return
        new = Path(folder)
        old = Path(DATA)
        if new.resolve() == old.resolve():
            return
        move = old.exists() and any(old.iterdir()) and self.app.ask(
            'Chép dữ liệu', 'Chép dữ liệu hiện tại (tài khoản, cấu hình, nhật ký) sang thư mục mới để giữ tài khoản đã đăng nhập?')
        try:
            set_data_dir(new)
        except OSError as exc:
            self.app.error('Không lưu được lựa chọn', str(exc)); return
        self.data_dir_field.setText(str(new))
        if not move:
            self.app.info('Đã đổi thư mục dữ liệu', f'Dữ liệu sẽ lưu tại:\n{new}\nKhởi động lại tool để áp dụng.')
            return
        self.app.log('Đang chép dữ liệu sang thư mục mới…')
        def work():
            error = ''
            try:
                new.mkdir(parents=True, exist_ok=True)
                for item in old.iterdir():
                    dest = new / item.name
                    if dest.exists():
                        continue
                    if item.is_dir():
                        shutil.copytree(item, dest)
                    else:
                        shutil.copy2(item, dest)
            except OSError as exc:
                error = str(exc)
            self.app.post_ui(lambda: self.app.info('Đã đổi thư mục dữ liệu',
                (f'Đã chép dữ liệu sang:\n{new}\nKhởi động lại tool để áp dụng.' if not error
                 else f'Đã đặt thư mục mới nhưng chép dữ liệu lỗi: {error}\nBạn có thể tự chép thủ công từ {old}.')))
        self.app.run_background(work)

    def rotate_proxy_now(self):
        """Đổi IP ngay cho key của NGUỒN đang chọn (ProxyVN gọi get.php, ProxyNo1 đổi IP)."""
        if self.proxy_busy:
            return
        src, keys = self._current_source_keys()
        if src == 'manual':
            self._proxy_status('Nguồn "Proxy nhập tay" không xoay IP.', WARNING); return
        if not keys:
            self._proxy_status('Chưa nhập key để đổi IP.', WARNING); return
        self.proxy_busy = True
        self._proxy_status(f'Đang đổi IP {len(keys)} key {"ProxyVN" if src == "pvn" else "ProxyNo1"}…', CYAN)
        def work():
            lines, bad = [], 0
            for number, key in enumerate(keys, 1):
                if src == 'pvn':
                    import proxyvn_api
                    try:
                        proxy, info = proxyvn_api.get_proxy(key)
                        lines.append(f"Key {number}: đã đổi IP • {proxyvn_api.endpoint_text(proxy)} • IP {info.get('ip') or '?'}")
                    except proxyvn_api.ProxyVNCooldown as cd:
                        lines.append(f"Key {number}: chưa tới lúc đổi • chờ {cd.seconds}s")
                    except proxyvn_api.ProxyVNError as exc:
                        bad += 1
                        lines.append(f"Key {number}: LỖI • {proxyvn_api.mask(exc, key)}")
                    continue
                rotator = rotator_for(key)
                ok = rotator.rotate()
                bad += not ok
                lines.append(f"Key {number}: {'đã đổi IP' if ok else 'chưa đổi được'} • {rotator.last_text}")
            def done():
                self.proxy_busy = False
                if self.isVisible():
                    self._proxy_status('\n'.join(lines), RED if bad else GREEN)
                self.app.draw_proxy_status()
            self.app.post_ui(done)
        self.app.run_background(work)

    def _current_source_keys(self):
        """(source, [key]) theo nguồn đang chọn trong combo."""
        src = self.PROXY_SOURCES[self.proxy_source.currentText()]
        box = self.vn_key_box if src == 'pvn' else self.key_box
        keys = [k.strip() for k in box.toPlainText().splitlines() if k.strip()]
        return src, keys

    def try_proxy_keys(self):
        """Kiểm tra từng key của NGUỒN đang chọn: ProxyVN gọi get.php, ProxyNo1 đọc key-status."""
        if self.proxy_busy:
            return
        src, keys = self._current_source_keys()
        if src == 'manual':
            self._proxy_status('Nguồn "Proxy nhập tay" không có key để kiểm tra.', WARNING); return
        if not keys:
            self._proxy_status('Chưa nhập key để kiểm tra (điền ô key của nguồn đang chọn).', WARNING); return
        self.proxy_busy = True
        self._proxy_status(f'Đang kiểm tra {len(keys)} key {"ProxyVN" if src == "pvn" else "ProxyNo1"}…', CYAN)
        def work():
            lines, bad = [], 0
            for number, key in enumerate(keys, 1):
                if src == 'pvn':
                    import proxyvn_api
                    try:
                        proxy, info = proxyvn_api.get_proxy(key)
                        lines.append(f"Key {number}: HỢP LỆ • {proxyvn_api.endpoint_text(proxy)} • "
                                     f"IP {info.get('ip') or '?'} • {info.get('vi_tri') or ''} • hạn {info.get('expired_at') or '?'}")
                    except proxyvn_api.ProxyVNCooldown as cd:
                        lines.append(f"Key {number}: HỢP LỆ (đang chờ {cd.seconds}s mới đổi được IP)")
                    except proxyvn_api.ProxyVNError as exc:
                        bad += 1
                        lines.append(f'Key {number}: LỖI/DIE • {proxyvn_api.mask(exc, key)}')
                else:
                    try:
                        proxy, info = key_status(key)
                        auth = 'có user/pass' if proxy.get('username') else 'không user/pass'
                        lines.append(f"Key {number}: HỢP LỆ • {endpoint(proxy)} • {auth} • gói {info.get('plan') or '?'} • "
                                     f"hạn {info.get('expired_at') or '?'}")
                    except ProxyNo1Error as exc:
                        bad += 1
                        lines.append(f'Key {number}: {exc}')
            def done():
                self.proxy_busy = False
                if self.isVisible():
                    self._proxy_status('\n'.join(lines) + '\nKey không ghi vào nhật ký.', RED if bad else GREEN)
            self.app.post_ui(done)
        self.app.run_background(work)

    def _pick_file(self, e):
        p, _ = QFileDialog.getOpenFileName(self, 'Chọn file', '', 'Executable (*.exe)')
        if p: e.setText(p)

    def _pick_dir(self, e):
        p = QFileDialog.getExistingDirectory(self, 'Chọn thư mục')
        if p: e.setText(p)

    def save(self):
        try:
            concurrency = int(self.fields['concurrency'].text()); timeout = int(self.fields['timeout'].text())
            tabs = int(self.fields['tabs_per_profile'].text()); delay = float(self.fields['action_delay'].text())
            if not 1 <= tabs <= 5 or not 1 <= concurrency <= 100 or not 30 <= timeout <= 7200 or not .2 <= delay <= 10:
                raise ValueError
        except ValueError:
            self.app.error('Giá trị không hợp lệ', 'Luồng 1–100 • tab 1–5 • timeout 30–7200 • nghỉ 0.2–10 giây.'); return
        auto_key_text, auto_machine_text = self.auto_key.text().strip(), self.auto_machine.text().strip()
        try:
            if auto_machine_text: validate_machine(auto_machine_text)
            protected_auto = (self.saved_auto_key if not self.auto_key_readable and not auto_key_text
                              else protect(auto_key_text) if auto_key_text else '')
        except ValueError as exc:
            self.app.error('Cấu hình Auto', str(exc)); return
        chosen_source = self.PROXY_SOURCES[self.proxy_source.currentText()]
        norm_source = 'manual' if chosen_source == 'manual' else 'proxyno1'
        raw_keys = self.key_box.toPlainText()
        try:
            spec = normalize_proxy_settings(norm_source, raw_keys,
                                            self.manual_proxy.toPlainText(), require_keys=False)
            if not self.keys_ok and not raw_keys.strip():
                protected_keys = self.app.settings.get('proxyno1_keys_protected') or ''
            else:
                protected_keys = protect('\n'.join(spec['keys'])) if spec['keys'] else ''
        except ValueError as exc:
            self.app.error('Proxy không hợp lệ', str(exc)); return
        # Key ProxyVN (nguồn thứ 2): mỗi dòng một key xoay.
        raw_vn = self.vn_key_box.toPlainText()
        vn_keys, vn_seen = [], set()
        for line in raw_vn.splitlines():
            k = line.strip()
            if not k:
                continue
            if not re.fullmatch(r'[A-Za-z0-9_-]{4,128}', k):
                self.app.error('Key ProxyVN không hợp lệ', 'Mỗi dòng một key (chữ/số/_/-, 4–128 ký tự).'); return
            if k not in vn_seen:
                vn_seen.add(k); vn_keys.append(k)
        if not self.vn_keys_ok and not raw_vn.strip():
            protected_vn = self.app.settings.get('proxyvn_keys_protected') or ''
        else:
            protected_vn = protect('\n'.join(vn_keys)) if vn_keys else ''
        # Chế độ xoay + thời gian.
        rotate_mode = self.ROTATE_MODES[self.rotate_mode.currentText()]
        try:
            rotate_minutes = int(self.rotate_minutes.text())
            if not 1 <= rotate_minutes <= 1440:
                raise ValueError
        except ValueError:
            self.app.error('Giá trị không hợp lệ', 'Thời gian xoay 1–1440 phút.'); return
        backend = self.BROWSER_BACKENDS[self.browser_backend.currentText()]
        try:
            from hidemium_browser import normalize_url as normalize_hidemium_url
            hidemium_api = normalize_hidemium_url(self.hidemium_api.text())
        except ValueError as exc:
            self.app.error('Hidemium API không hợp lệ', str(exc)); return
        except Exception as exc:
            self.app.error('Hidemium API không hợp lệ', str(exc)); return
        self.app.settings.update(
            proxy_source=chosen_source, proxyno1_keys_protected=protected_keys, default_proxy=spec['default_proxy'],
            default_proxies=spec['default_proxies'], proxyvn_keys_protected=protected_vn,
            proxy_rotate_mode=rotate_mode, proxy_rotate_minutes=rotate_minutes,
            auto_render_key_protected=protected_auto, auto_machine_code=auto_machine_text)
        self.app.settings.update(
            concurrency=concurrency, timeout=timeout, tabs_per_profile=tabs, action_delay=delay,
            browser=self.fields['browser'].text().strip(), ffmpeg=self.fields['ffmpeg'].text().strip(),
            output=self.fields['output'].text().strip(),
            video_source=self.SOURCE_LABELS[self.source.currentText()],
            engine=next(k for k, v in self.ENGINES.items() if v == self.engine.currentText()),
            upscale=self.upscale.isChecked(),
            browser_backend=backend, use_hidemium=backend == 'hidemium', hidemium_api=hidemium_api,
            use_gpm=False, use_3big_browser=backend == 'chrome', seedance_browser=backend == 'chrome',
            seedance_flow=self.seedance_flow_values[self.seedance_flow.currentText()],
            show_browser=not self.gpm_hidden.isChecked(), gpm_hidden=False)
        if hasattr(self.app, 'cb_show_browser'):
            self.app.cb_show_browser.blockSignals(True)
            self.app.cb_show_browser.setChecked(bool(self.app.settings.get('show_browser', True)))
            self.app.cb_show_browser.blockSignals(False)
        if hasattr(self.app, 'sync_quick_settings_ui'):
            self.app.sync_quick_settings_ui()
        self.app.save(); self.app.log('Đã lưu cấu hình hệ thống. Cấu hình Auto áp dụng từ lần bật tiếp theo.')
        self.app.apply_proxy_rotation()
        self.app.check_license()
        self.app.refresh_gallery(); self.accept()


class ProxyModeField(QWidget):
    """Chọn chế độ proxy; ô nhập chỉ hiện khi chọn 'Nhập proxy tay'."""
    def __init__(self, mode='default', text=''):
        super().__init__()
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(6)
        self.combo = QComboBox(); self.combo.addItems([label for label, _ in PROXY_MODE_LABELS])
        self.combo.setCurrentText(label_for_mode(mode)); self.combo.setFixedWidth(280)
        self.entry = QLineEdit(text); self.entry.setPlaceholderText('host:port hoặc host:port:user:pass')
        lay.addWidget(self.combo); lay.addWidget(self.entry)
        self.combo.currentTextChanged.connect(lambda _=0: self.entry.setVisible(self.mode() == 'manual'))
        self.entry.setVisible(self.mode() == 'manual')

    def mode(self):
        return dict(PROXY_MODE_LABELS)[self.combo.currentText()]


class AccountFormDialog(QDialog):
    """Khung chung cho các hộp thoại thêm tài khoản: tiêu đề, ghi chú, dòng báo lỗi, chạy nền an toàn."""
    def __init__(self, app, title, heading, width=640, height=520):
        super().__init__(app); self.app = app
        self.busy = False; self.closed = False
        self.setWindowTitle(title); self.resize(width, height)
        self.lay = QVBoxLayout(self); self.lay.setContentsMargins(22, 18, 22, 18); self.lay.setSpacing(8)
        head = QLabel(heading); head.setObjectName('h1'); self.lay.addWidget(head)
        self.message = QLabel(''); self.message.setWordWrap(True)
        self.finished.connect(lambda _=0: setattr(self, 'closed', True))

    def note(self, text, color=None):
        lab = _muted(text); lab.setWordWrap(True)
        if color: lab.setStyleSheet(f'color: {color}; font-size: 9pt;')
        self.lay.addWidget(lab); return lab

    def text_box(self, placeholder='', mono=False, height=None):
        box = QPlainTextEdit(); box.setPlaceholderText(placeholder)
        if mono: box.setStyleSheet("font-family: 'Consolas'; font-size: 9pt;")
        if height: box.setFixedHeight(height)
        self.lay.addWidget(box, 0 if height else 1); return box

    def say(self, text, color=RED):
        self.message.setText(text); self.message.setStyleSheet(f'color: {color};')

    def browser_text(self):
        if self.app.settings.get('use_hidemium'):
            return 'Trình duyệt: Hidemium 4 • Profile liên kết bằng UUID'
        if self.app.settings.get('use_gpm'):
            return 'Trình duyệt: GPM Login • Profile do GPM quản lý (proxy/xoá qua API)'
        return ('Trình duyệt: Chrome for Testing của Seedance Studio • Profile riêng' if self.app.settings.get('use_3big_browser')
                else 'Trình duyệt: Google Chrome • Profile riêng')

    def backend_field(self):
        """Chọn trình duyệt (Chrome/Hidemium) để đăng nhập các tài khoản tạo ở hộp thoại này.
        Lựa chọn ghi vào từng tài khoản và giữ nguyên sau khi đăng nhập (không đổi về sau)."""
        self.note('Trình duyệt dùng khi đăng nhập (chọn lúc này; sau khi đăng nhập tài khoản giữ nguyên loại này):')
        self.backend = QComboBox(); self.backend.addItems(list(SettingsDialog.BROWSER_BACKENDS))
        default = 'hidemium' if self.app.settings.get('use_hidemium') else self.app.settings.get('browser_backend', 'chrome')
        self.backend.setCurrentText(next((label for label, value in SettingsDialog.BROWSER_BACKENDS.items()
                                          if value == default), next(iter(SettingsDialog.BROWSER_BACKENDS))))
        self.lay.addWidget(self.backend)
        self.note('Hidemium: nếu chưa liên kết UUID, profile sẽ tự tạo khi mở lần đầu '
                  '(cần mở Hidemium 4, Local API 127.0.0.1:2222).', CYAN)

    def chosen_backend(self):
        return SettingsDialog.BROWSER_BACKENDS.get(self.backend.currentText(), 'chrome') if hasattr(self, 'backend') else 'chrome'

    def background(self, work, apply, busy_text, fallback, always=False):
        """work() chạy nền (ValueError = thông báo cho người dùng); apply(result) chạy trên UI.
        always=True: vẫn apply khi hộp thoại đã đóng (dữ liệu đã ghi ra đĩa)."""
        if self.busy:
            return
        self.busy = True; self.say(busy_text, CYAN)
        def run():
            try:
                result, error = work(), ''
            except ValueError as exc:
                result, error = None, str(exc)
            except Exception:
                result, error = None, fallback
            def done():
                self.busy = False
                if error:
                    if not self.closed: self.say(error)
                elif always or not self.closed:
                    apply(result)
            self.app.post_ui(done)
        self.app.run_background(run)

    def actions(self, *buttons):
        self.lay.addWidget(self.message)
        bar = QHBoxLayout(); bar.addStretch(1)
        for b in buttons: bar.addWidget(b)
        bar.addWidget(_btn('Đóng', self.reject))
        self.lay.addLayout(bar)


class LoginDialog(AccountFormDialog):
    def __init__(self, app, facebook=False):
        super().__init__(app, 'Đăng nhập Facebook → Dola' if facebook else 'Đăng nhập Dola',
                         'ĐĂNG NHẬP VÀ TỰ NHẬN TÊN', 640, 640 if facebook else 520)
        self.facebook = facebook
        self.cookie = None
        if facebook:
            self.note('Cookie Facebook của tài khoản: c_user=…; xs=…')
            self.cookie = self.text_box('c_user=…; xs=…', mono=True, height=100)
            self.note('Nạp cookie trước; bạn chọn đăng nhập Facebook trên Dola. Tool tiếp tục chờ tên tài khoản.')
        self.note('Proxy cho tài khoản mới')
        self.proxy = ProxyModeField(); self.lay.addWidget(self.proxy)
        self.note('Mặc định dùng ProxyNo1 trong Cài đặt (key lấy ở đó). Nhập tay chỉ cho tài khoản này.')
        self.backend_field()
        self.note('Đăng nhập Dola trong cửa sổ mở ra; tool tự nhận tên, lưu và đóng cửa sổ.\n'
                  'Proxy có user/pass được xác thực tự động. Không cần đóng Chrome để xác nhận.')
        self.lay.addStretch(1)
        self.actions(_btn('Mở Dola để đăng nhập', self.begin, primary=True))

    def begin(self):
        if self.app.running:
            return
        mode, typed, backend = self.proxy.mode(), self.proxy.entry.text(), self.chosen_backend()
        cookie_text = self.cookie.toPlainText() if self.facebook else ''
        def work():
            cookies = parse_facebook_cookie_line(cookie_text) if self.facebook else None
            proxy = parse_proxy(typed) if mode == 'manual' else {}
            if mode == 'manual' and not proxy:
                raise ValueError('Nhập proxy dạng host:port hoặc host:port:user:pass.')
            if mode in ('default', 'proxyno1'):
                import proxy_pool
                src = proxy_source(self.app.settings)
                if src == 'manual':
                    from proxyvn import manual_proxies
                    if not manual_proxies(self.app.settings):
                        raise ValueError('Chưa có proxy mặc định trong Cài đặt. Hãy nhập trong Cài đặt → Proxy hoặc chọn "Tắt proxy".')
                else:
                    keys = proxy_pool.source_keys(self.app.settings)
                    if not keys:
                        name = 'ProxyVN' if src == 'pvn' else 'ProxyNo1'
                        raise ValueError(f'Chưa có API key {name} trong Cài đặt. Nhập key trong Cài đặt → Proxy hoặc chọn "Tắt proxy" / "Nhập proxy tay".')
            return cookies, proxy
        def launch(result):
            if self.app.running:
                return
            cookies, proxy = result
            aid = uuid.uuid4().hex
            self.accept()
            self.app.log('Đang mở Dola để đăng nhập; chưa thêm tài khoản vào danh sách.')
            self.app.register_account({'id': aid, 'profile': str(DATA / 'profiles' / aid), 'proxy': proxy,
                                       'proxy_mode': mode, 'browser_backend': backend}, cookies)
        self.background(work, launch, 'Đang chuẩn bị phiên…', 'Cookie hoặc proxy không hợp lệ.')


class FacebookBatchDialog(AccountFormDialog):
    def __init__(self, app):
        super().__init__(app, 'Cookie Facebook → Dola (nhiều dòng)', 'NHẬP COOKIE FACEBOOK — MỖI DÒNG MỘT TÀI KHOẢN', 720, 620)
        self.note('Mỗi dòng là một tài khoản. Nhận nhiều dạng, miễn có cookie Facebook (c_user và xs):\n'
                  '• uid|mật khẩu/2FA|c_user=…; xs=…|email  • chuỗi c_user=…; xs=…  • JSON cookie\n'
                  'Các trường thừa như uid, 2FA, email được bỏ qua.')
        self.cookies = self.text_box('Mỗi dòng một tài khoản…', mono=True)
        self.note('Proxy theo từng dòng (host:port hoặc host:port:user:pass). Dòng trống = proxy mặc định (ProxyNo1) trong Cài đặt.')
        self.proxies = self.text_box('Dòng 1 ↔ tài khoản 1…', mono=True, height=90)
        self.backend_field()
        self.note('Mỗi tài khoản mở một cửa sổ riêng; nạp cookie rồi chờ bạn đăng nhập Facebook trên Dola.\n'
                  'Tool tự nhận tên, lưu và đóng cửa sổ. CAPTCHA/checkpoint/2FA xử lý thủ công. Đóng cửa sổ một tài khoản chỉ dừng tài khoản đó.')
        self.start = _btn('Mở trình duyệt đăng nhập (0)', self.begin, primary=True)
        self.cookies.textChanged.connect(lambda: self.start.setText(f'Mở trình duyệt đăng nhập ({len(self._lines())})'))
        self.actions(self.start)

    def _lines(self):
        return [x.strip() for x in self.cookies.toPlainText().splitlines() if x.strip()]

    def begin(self):
        if self.app.running or self.app.account_jobs:
            self.say('Đóng các cửa sổ đăng nhập/kiểm tra đang mở hoặc chờ batch kết thúc.'); return
        lines, proxy_lines, backend = self._lines(), self.proxies.toPlainText().splitlines(), self.chosen_backend()
        if not lines or len(lines) > 50:
            self.say('Nhập từ 1 đến 50 dòng cookie.'); return
        def apply(built):
            self.accept()
            self.app.start_facebook_cookie_batch(built, backend)
        self.background(lambda: build_facebook_cookie_accounts(lines, proxy_lines), apply,
                        'Đang đọc cookie…', 'Không đọc được cookie Facebook.')


class ProxyDialog(AccountFormDialog):
    def __init__(self, app, account):
        super().__init__(app, 'Proxy — ' + str(account.get('name') or ''), 'PROXY CHO TÀI KHOẢN', 640, 400)
        self.account = account
        self.note('Dùng proxy mặc định (trong Cài đặt), ProxyNo1, nhập tay, hoặc tắt.')
        self.proxy = ProxyModeField(account_proxy_mode(account)); self.lay.addWidget(self.proxy)
        self.note('ProxyNo1 dùng API key trong Cài đặt, mỗi dòng một key; một luồng riêng tự đổi IP 15 phút/lần.\n'
                  'User/pass của proxy (ProxyNo1 hoặc nhập tay) được dùng tự động.')
        self.lay.addStretch(1)
        self.save_button = _btn('Lưu proxy', self.save_proxy, primary=True); self.save_button.setEnabled(False)
        self.actions(self.save_button)
        stored = copy.deepcopy(account.get('proxy') or {})
        def load():
            try:
                return proxy_input_value(stored)
            except ValueError:
                raise ValueError('Không đọc được proxy tay đã lưu; hãy nhập lại nếu chọn Nhập tay.') from None
        def filled(current):
            if current and not self.proxy.entry.text().strip():
                self.proxy.entry.setText(current)
            self.say(''); self.save_button.setEnabled(True)
        self.background(load, filled, 'Đang đọc proxy đã lưu…', 'Không đọc được proxy tay đã lưu; hãy nhập lại nếu chọn Nhập tay.')

    def say(self, text, color=RED):
        super().say(text, color)
        if text and color == RED and hasattr(self, 'save_button'):
            self.save_button.setEnabled(True)  # lỗi đọc proxy cũ vẫn cho nhập mới

    def save_proxy(self):
        account = self.account
        if self.app.running or account['id'] in self.app.account_jobs:
            return
        mode, typed = self.proxy.mode(), self.proxy.entry.text()
        stored = copy.deepcopy(account.get('proxy') or {})
        def work():
            proxy = parse_proxy(typed) if mode == 'manual' else stored
            if mode == 'manual' and not proxy:
                raise ValueError('Nhập proxy dạng host:port hoặc host:port:user:pass.')
            return {} if mode == 'off' else proxy
        def apply(proxy):
            if self.app.running or account['id'] in self.app.account_jobs:
                return
            account.update(proxy_mode=mode, proxy=proxy, session_verified_at=0)
            self.app.save(); self.app.request_accounts_paint()
            self.app.log('Đã lưu proxy cho ' + str(account.get('name') or '') + ': ' + proxy_mode_text(account))
            self.accept()
        self.background(work, apply, 'Đang kiểm tra proxy…', 'Proxy không hợp lệ.')


class DolaCookieDialog(AccountFormDialog):
    def __init__(self, app):
        super().__init__(app, 'Nhập cookie Dola', 'NHẬP PHIÊN DOLA', 780, 640)
        self.note('Chọn nhiều file JSON hoặc dán nhiều khối JSON. Mỗi dòng hay mỗi khối là một tài khoản.\n'
                  'Nhận profile + cookies/cookie, mảng tài khoản, hoặc sessionid=…; mỗi tài khoản một profile.')
        pick = QHBoxLayout(); pick.addWidget(_btn('Chọn nhiều file JSON…', self.choose_files)); pick.addStretch(1)
        self.lay.addLayout(pick)
        self.box = self.text_box('Dán JSON cookie Dola…', mono=True)
        self.note('Proxy riêng cho các profile mới (để trống = proxy mặc định, ProxyNo1):')
        self.proxy = QLineEdit(); self.proxy.setPlaceholderText('host:port hoặc host:port:user:pass • trống = proxy mặc định')
        self.lay.addWidget(self.proxy)
        self.backend_field()
        self.note('Cookie sẽ được nạp vào dola.com. Cookie hết hạn cần đăng nhập lại.\n'
                  'has_pro / pro_agent_mode trong file không xác nhận quyền Pro trên dịch vụ.')
        self.actions(_btn('Lưu • kiểm tra khi chạy', lambda: self.submit(False)),
                     _btn('Nhập & kiểm tra Dola', lambda: self.submit(True), primary=True))

    def choose_files(self):
        if self.busy:
            return
        paths, _ = QFileDialog.getOpenFileNames(self, 'Chọn một hoặc nhiều file cookie Dola', '',
                                                'JSON cookie (*.json);;Văn bản (*.txt)')
        if not paths:
            return
        if len(paths) > 50:
            self.say('Chọn tối đa 50 file mỗi lượt.'); return
        current = self.box.toPlainText().strip()
        def work():
            chunks = []
            for path in paths:
                file = Path(path)
                if file.stat().st_size > 5 * 1024 * 1024:
                    raise ValueError('File vượt quá 5 MB.')
                chunks.append(file.read_text(encoding='utf-8-sig').strip())
            block = '\n'.join(chunk for chunk in chunks if chunk)
            merged = (current + '\n' + block).strip() if current else block
            try:
                return merged, len(parse_dola_accounts(merged))
            except (UnicodeError, ValueError):
                raise ValueError('Không đọc được file hoặc JSON cookie Dola không hợp lệ.') from None
        def apply(result):
            merged, count = result
            self.box.setPlainText(merged)
            self.say(f'Đã đọc {count} tài khoản từ {len(paths)} file; chưa lưu hoặc kiểm tra phiên.', CYAN)
        self.background(work, apply, 'Đang đọc file cookie…', 'Không đọc được file hoặc JSON cookie Dola không hợp lệ.')

    def submit(self, start):
        if self.app.running:
            self.say('Chờ lượt chạy kết thúc trước khi import.'); return
        raw, proxy_text, backend = self.box.toPlainText(), self.proxy.text(), self.chosen_backend()
        def apply(added):
            self.app.commit_accounts(added, f'Đã lưu {len(added)} tài khoản cookie Dola; chưa xác nhận phiên. Cookie được mã hóa riêng.')
            if not self.closed:
                self.box.clear(); self.accept()
            if start:
                self.app.start_cookie_accounts(added)
        self.background(lambda: encode_dola_accounts(parse_dola_accounts(raw), parse_proxy(proxy_text), backend), apply,
                        'Đang lưu tài khoản…', 'Không lưu được cookie mã hóa. Kiểm tra quyền ghi thư mục data.', always=True)


class GoogleCookieDialog(AccountFormDialog):
    def __init__(self, app):
        super().__init__(app, 'Đăng nhập cookie Google', 'ĐĂNG NHẬP COOKIE GOOGLE', 860, 640)
        self.note('Mỗi dòng = toàn bộ cookie của một tài khoản Google bạn quản lý.\n'
                  'Nhận {...},{...} như mẫu xuất, mảng JSON [...], hoặc SID=...; HSID=...')
        self.box = self.text_box('Mỗi dòng một tài khoản…', mono=True)
        self.note('Proxy cho các tài khoản mới (bỏ trống = proxy mặc định, ProxyNo1):')
        self.proxy = QLineEdit(); self.proxy.setPlaceholderText('http://host:port hoặc socks5://host:port')
        self.lay.addWidget(self.proxy)
        row = QHBoxLayout()
        self.user = QLineEdit(); self.user.setPlaceholderText('User proxy (tùy chọn)')
        self.password = QLineEdit(); self.password.setPlaceholderText('Mật khẩu proxy'); self.password.setEchoMode(QLineEdit.Password)
        row.addWidget(self.user); row.addWidget(self.password); self.lay.addLayout(row)
        self.backend_field()
        self.note('Mỗi dòng tạo profile riêng; tên sẽ đổi thành tên hiển thị trên Dola khi xác nhận được.\n'
                  'Cookie không chứa miền sẽ dùng google.com. Cookie hết hạn hoặc Google chặn cần xử lý thủ công.\n'
                  'Cookie và mật khẩu proxy được mã hóa bằng tài khoản Windows hiện tại.')
        self.actions(_btn('Lưu — đăng nhập khi chạy video', lambda: self.submit(False)),
                     _btn('Import & đăng nhập ngay', lambda: self.submit(True), primary=True))

    def submit(self, start):
        if self.app.running:
            return
        lines = [(i, line.strip()) for i, line in enumerate(self.box.toPlainText().splitlines(), 1) if line.strip()]
        if not lines or len(lines) > 50:
            self.say('Nhập từ 1 đến 50 dòng tài khoản mỗi lượt.'); return
        proxy_text, user, password = self.proxy.text(), self.user.text(), self.password.text()
        name_base = len(self.app.accounts); backend = self.chosen_backend()
        def apply(added):
            self.app.commit_accounts(added, f'Đã thêm {len(added)} profile riêng; nội dung cookie không ghi vào nhật ký.')
            if not self.closed:
                self.box.clear(); self.accept()
            if start:
                self.app.start_cookie_accounts(added)
        self.background(lambda: encode_google_accounts(lines, parse_proxy(proxy_text, user, password), name_base, backend), apply,
                        'Đang lưu tài khoản…', 'Không lưu được cookie mã hóa. Kiểm tra quyền ghi thư mục data.', always=True)


class AccountRow(QFrame):
    """Một thẻ tài khoản: chọn • avatar • tên + proxy • trạng thái • Dùng chạy • nút thao tác."""
    def __init__(self, dialog, account):
        super().__init__(); self.setObjectName('accrow')
        self.dialog, self.aid = dialog, account['id']
        lay = QHBoxLayout(self); lay.setContentsMargins(10, 7, 10, 7); lay.setSpacing(10)
        self.pick = QCheckBox(); self.pick.toggled.connect(self._picked); lay.addWidget(self.pick)
        self.avatar = QLabel(); self.avatar.setFixedSize(34, 34); self.avatar.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.avatar)
        names = QVBoxLayout(); names.setSpacing(1)
        self.name = QLabel(); self.name.setObjectName('accname')
        self.sub = QLabel(); self.sub.setObjectName('muted')
        names.addWidget(self.name); names.addWidget(self.sub)
        lay.addLayout(names, 1)
        self.state = _pill('', MUTED, BORDER); self.state.setMinimumWidth(104)
        lay.addWidget(self.state, 0, Qt.AlignVCenter)
        self.run = QCheckBox('Dùng chạy'); self.run.toggled.connect(self._run_toggled); lay.addWidget(self.run)
        app = dialog.app
        self.cookie = _btn('Cookie ↻', lambda: app.start_cookie_accounts([self.account()]))
        self.cookie.setToolTip('Nạp lại cookie đã lưu cho tài khoản này')
        self.hidemium = _btn('Hidemium UUID', lambda: app.hidemium_profile_dialog(self.account()))
        self.chat = _btn('💬 Chat', lambda: app.open_dola_chat(self.account()))
        self.buttons = [self.cookie,
                        _btn('Đổi tên', lambda: app.rename_account_dialog(self.account())),
                        _btn('Proxy', lambda: app.proxy_dialog(self.account())),
                        self.hidemium,
                        self.chat,
                        _btn('Mở/Đăng nhập', lambda: app.run_account_action(self.account(), True)),
                        _btn('Kiểm tra', lambda: app.run_account_action(self.account(), False)),
                        _btn('Xóa phiên', lambda: dialog.remove([self.account()]), color=RED)]
        for b in self.buttons:
            b.setObjectName('rowbtn'); lay.addWidget(b)

    def account(self):
        return self.dialog.app.account_by_id.get(self.aid)

    def paint(self, account, busy, picked):
        name = account.get('name') or '(chưa tên)'
        color = AVATAR_COLORS[sum(map(ord, name)) % len(AVATAR_COLORS)]
        self.avatar.setText(name.strip()[:1].upper() or '?')
        self.avatar.setStyleSheet(f'background: {color}; color: {TEXT}; border-radius: 17px; font-weight: bold; font-size: 14px;')
        self.name.setText(name)
        detail = account_detail(account)
        self.sub.setText('Proxy: ' + proxy_mode_text(account) + (f'  •  {detail}' if detail else ''))
        badge = account_status_badge(account, busy)
        self.sub.setToolTip(f"{account.get('connection') or ''}\nTrạng thái: {badge['text']}")
        key = account_state(account, busy)
        _style_pill(self.state, *ACCOUNT_STATES[key])
        for box, value in ((self.pick, picked), (self.run, bool(account.get('selected', True)))):
            if box.isChecked() != value:
                box.blockSignals(True); box.setChecked(value); box.blockSignals(False)
        for widget in [self.pick, self.run] + self.buttons:
            widget.setEnabled(not busy)
        # Giữ chỗ để các nút thẳng cột giữa tài khoản có / không có cookie.
        policy = self.cookie.sizePolicy(); policy.setRetainSizeWhenHidden(True); self.cookie.setSizePolicy(policy)
        self.cookie.setVisible(bool(account.get('cookie_file')))
        from account_browser_backend import account_backend
        self.hidemium.setVisible(account_backend(account) == 'hidemium')
        self.key = key

    def _picked(self, on):
        (self.dialog.chosen.add if on else self.dialog.chosen.discard)(self.aid)
        self.dialog.sync_toolbar()

    def _run_toggled(self, on):
        account = self.account()
        if account is None:
            return
        account['selected'] = on
        if on:
            account.pop('credit_locked_until', None)  # tick tay bỏ khóa hết điểm
            account.pop('work_locked_until', None)    # tick tay bỏ khóa work
            account.pop('rate_limited_until', None)   # tick tay bỏ khóa rate limit
            account.pop('run_cooldown_until', None)   # tick tay bỏ nghỉ 1 tuần sau khi chạy
        self.dialog.app.later_save(); self.dialog.app.request_accounts_paint()


class AccountsDialog(QDialog):
    FILTERS = [('all', 'Tất cả'), ('live', 'Live'), ('die', 'Die'), ('credit', 'Hết điểm'), ('work_limit', 'Giới hạn Work'), ('rate_limit', 'Rate Limit'), ('rest', 'Nghỉ 7 ngày'), ('unknown', 'Chưa kiểm tra')]

    def __init__(self, app):
        super().__init__(app); self.app = app
        self.setWindowTitle('Quản lý tài khoản'); self.resize(1080, 680)
        self.chosen, self.rows, self.filter = set(), {}, 'all'
        lay = QVBoxLayout(self); lay.setContentsMargins(18, 16, 18, 14); lay.setSpacing(10)
        head = QHBoxLayout()
        title = QLabel('♟  TÀI KHOẢN DOLA'); title.setObjectName('h1'); head.addWidget(title)
        head.addStretch(1)
        self.search = QLineEdit(); self.search.setPlaceholderText('Tìm tên tài khoản…'); self.search.setFixedWidth(240)
        self.search.textChanged.connect(lambda _=0: self.apply_filter()); head.addWidget(self.search)
        lay.addLayout(head)
        lay.addWidget(_muted('Live = phiên Dola hợp lệ • Die = cần đăng nhập lại • Hết điểm = tự mở lại lúc 00:00. '
                             'Bỏ tick "Dùng chạy" để không giao task cho tài khoản đó.'))
        add = QHBoxLayout(); add.setSpacing(6)
        add.addWidget(_muted('THÊM TÀI KHOẢN'))
        for text, fn in (('Đăng nhập', lambda: app.login_account_dialog()),
                         ('Đăng nhập FB', lambda: app.login_account_dialog(facebook=True)),
                         ('Cookie FB nhiều dòng', app.facebook_cookie_batch_dialog),
                         ('Đăng nhập cookie Google', app.cookie_import_dialog),
                         ('Nhập cookie Dola • JSON', app.dola_cookie_import_dialog)):
            add.addWidget(_btn(text, fn, primary=True))
        add.addStretch(1)
        lay.addLayout(add)
        chips = QHBoxLayout(); chips.setSpacing(6)
        self.chips = {}
        for key, label in self.FILTERS:
            b = QPushButton(label); b.setObjectName('chip'); b.setCheckable(True); b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=0, k=key: self.set_filter(k)); chips.addWidget(b); self.chips[key] = (b, label)
        chips.addStretch(1)
        lay.addLayout(chips)

        tools = QHBoxLayout(); tools.setSpacing(6)
        self.pick_all = QCheckBox('Chọn tất cả (đang hiện)'); self.pick_all.clicked.connect(self._pick_all)
        tools.addWidget(self.pick_all)
        self.picked_label = _muted('0 đã chọn'); tools.addWidget(self.picked_label)
        tools.addStretch(1)
        self.bulk = [_btn('Bật dùng chạy', lambda: self._set_run(True)),
                     _btn('Tắt dùng chạy', lambda: self._set_run(False)),
                     _btn('Check live đã chọn', lambda: self.app.check_selected_accounts(self._chosen()), primary=True),
                     _btn('Xóa phiên đã chọn', lambda: self.remove(self._chosen()), color=RED)]
        for b in self.bulk: tools.addWidget(b)
        lay.addLayout(tools)

        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True); self.scroll.setObjectName('acclist')
        holder = QWidget(); holder.setObjectName('acclistbody')
        self.list_layout = QVBoxLayout(holder); self.list_layout.setContentsMargins(6, 6, 6, 6); self.list_layout.setSpacing(6)
        self.list_layout.addStretch(1)
        self.scroll.setWidget(holder); lay.addWidget(self.scroll, 1)
        self.empty = _muted(''); self.empty.setAlignment(Qt.AlignCenter); lay.addWidget(self.empty)

        bar = QHBoxLayout()
        bar.addWidget(_btn('Xóa profile die', lambda: self.app.delete_dead_profiles(), color=RED))
        bar.addWidget(_btn('Đóng cửa sổ trình duyệt đang mở', lambda: self.app.cancel_account_actions()))
        bar.addWidget(_btn('Liên kết từ bản gốc', lambda: self.app.import_original_accounts()))
        bar.addWidget(_btn('💬 Mở Chat Dola', lambda: self.app.open_dola_chat()))
        bar.addStretch(1)
        bar.addWidget(_btn('Đóng', self.accept))
        lay.addLayout(bar)

        self.app.account_redraw = self.redraw
        self.redraw()
        self.finished.connect(lambda _=0: setattr(self.app, 'account_redraw', None))

    def redraw(self):
        accounts = self.app.accounts
        ids = [a['id'] for a in accounts]
        self.chosen.intersection_update(ids)
        if list(self.rows) != ids:
            for row in self.rows.values():
                row.setParent(None); row.deleteLater()
            self.rows = {}
            for i, account in enumerate(accounts):
                row = AccountRow(self, account); self.rows[account['id']] = row
                self.list_layout.insertWidget(i, row)
        busy = self.app.account_jobs
        for account in accounts:
            self.rows[account['id']].paint(account, account['id'] in busy, account['id'] in self.chosen)
        counts = {key: 0 for key, _ in self.FILTERS}
        for row in self.rows.values():
            counts[self._bucket(row.key)] += 1
        counts['all'] = len(accounts)
        for key, (button, label) in self.chips.items():
            button.setText(f'{label}  {counts[key]}')
        self.apply_filter()

    @staticmethod
    def _bucket(key):
        return key if key in ('live', 'die', 'credit', 'work_limit', 'rate_limit', 'rest') else 'unknown'

    def set_filter(self, key):
        self.filter = key; self.apply_filter()

    def apply_filter(self):
        text = self.search.text().strip().casefold()
        shown = 0
        for aid, row in self.rows.items():
            account = self.app.account_by_id.get(aid) or {}
            visible = ((self.filter == 'all' or self._bucket(row.key) == self.filter)
                       and (not text or text in str(account.get('name') or '').casefold()))
            row.setVisible(visible); shown += visible
        for key, (button, _) in self.chips.items():
            button.setChecked(key == self.filter)
        self.empty.setText('' if shown else ('Chưa có tài khoản.' if not self.rows else 'Không có tài khoản khớp bộ lọc.'))
        self.empty.setVisible(not shown)
        self.sync_toolbar()

    def _visible_ids(self):
        return [aid for aid, row in self.rows.items() if not row.isHidden() and row.pick.isEnabled()]

    def sync_toolbar(self):
        visible = self._visible_ids()
        self.pick_all.blockSignals(True)
        self.pick_all.setChecked(bool(visible) and set(visible) <= self.chosen)
        self.pick_all.blockSignals(False)
        self.picked_label.setText(f'{len(self.chosen)} đã chọn')
        for b in self.bulk: b.setEnabled(bool(self.chosen))

    def _pick_all(self, on):
        visible = self._visible_ids()
        if on: self.chosen.update(visible)
        else: self.chosen.difference_update(visible)
        self.redraw()

    def _chosen(self):
        return [a for a in self.app.accounts if a['id'] in self.chosen]

    def _set_run(self, enabled):
        for a in self._chosen():
            a['selected'] = enabled
            if enabled:
                a.pop('credit_locked_until', None)
                a.pop('run_cooldown_until', None)
        self.app.later_save(); self.app.request_accounts_paint(); self.redraw()

    def remove(self, accounts):
        accounts = [a for a in accounts if a]
        if accounts and self.app.ask('Xóa phiên', f'Bỏ {len(accounts)} tài khoản khỏi danh sách?'):
            self.app.remove_accounts(accounts)


def _check_icon():
    """Dấu ✓ cho ô tick (QSS cần file ảnh); lỗi ghi file thì chỉ còn ô tô màu."""
    import tempfile
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 15 15">'
           f'<path d="M3.6 7.9l2.5 2.5 5.3-5.5" fill="none" stroke="{TEXT}" stroke-width="2"'
           ' stroke-linecap="round" stroke-linejoin="round"/></svg>')
    path = Path(tempfile.gettempdir()) / 'studio_qt_check.svg'
    try:
        if not path.is_file() or path.read_text('utf-8') != svg:
            path.write_text(svg, 'utf-8')
    except OSError:
        return ''
    return f'image: url("{path.as_posix()}");'


STYLE = f"""
QWidget {{ background: {BG}; color: {TEXT}; font-family: 'Segoe UI'; font-size: 10pt; }}
QLabel, QCheckBox {{ background: transparent; }}
QDialog {{ background: {PANEL}; }}
QMainWindow::separator {{ background: {BORDER}; height: 1px; }}
#topbar {{ background: {PANEL}; border-bottom: 1px solid {BORDER}; }}
#logo {{ color: {CYAN}; font-size: 22px; }}
#apptitle {{ font-size: 19px; font-weight: bold; }}
#side {{ background: {PANEL}; border-right: 1px solid {BORDER}; }}
#brand {{ color: {CYAN}; font-size: 18px; font-weight: bold; }}
#brandsub {{ color: {MUTED}; font-size: 8pt; font-weight: bold; padding-left: 26px; }}
#navsection {{ color: {MUTED}; font-size: 8pt; font-weight: bold; letter-spacing: 1px; padding: 4px 6px 2px 6px; }}
#h1 {{ font-size: 16px; font-weight: bold; }}
#muted, QLabel#muted {{ color: {MUTED}; font-size: 8.5pt; }}
#ok {{ color: {GREEN}; font-weight: bold; }}
#cyan {{ color: {CYAN}; }}
#section {{ font-weight: bold; margin-top: 6px; }}
QPushButton#nav {{ background: transparent; color: {MUTED}; border: none; border-radius: 7px; padding: 9px 10px; text-align: left; }}
QPushButton#nav:hover {{ background: {HOVER}; color: {CYAN}; }}
QPushButton#nav[active="true"] {{ background: {ACTIVE_BG}; color: {CYAN}; font-weight: bold; }}
#acccard {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 10px; margin: 4px 0; }}
#acccard:hover {{ border-color: {ACCENT}; }}
#acctotal {{ font-size: 22px; font-weight: bold; color: {TEXT}; }}
#statcell {{ font-size: 8.5pt; color: {MUTED}; }}
#tablecard {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 12px; }}
#accrow {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 9px; }}
#accrow:hover {{ border-color: {ACCENT}; }}
#accname {{ font-weight: bold; font-size: 10pt; }}
#acclist, #acclistbody {{ background: {FIELD}; border-radius: 10px; }}
QPushButton#rowbtn {{ background: transparent; color: {TEXT}; border: 1px solid {BORDER}; border-radius: 6px; padding: 4px 10px; font-size: 9pt; }}
QPushButton#rowbtn:hover {{ background: {HOVER}; border-color: {ACCENT}; }}
QPushButton#rowbtn:disabled {{ color: {DISABLED}; }}
QPushButton#chip {{ background: {CARD}; color: {MUTED}; border: 1px solid {BORDER}; border-radius: 13px; padding: 4px 14px; font-size: 9pt; }}
QPushButton#chip:hover {{ color: {TEXT}; }}
QPushButton#chip:checked {{ background: {ACTIVE_BG}; color: {CYAN}; border-color: {ACCENT}; font-weight: bold; }}
QCheckBox::indicator {{ width: 15px; height: 15px; border: 1px solid {SCROLL}; border-radius: 4px; background: {FIELD}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; {_check_icon()} }}
QCheckBox::indicator:disabled {{ border-color: {BORDER}; }}
QPushButton#tool {{ background: {PANEL}; color: {TEXT}; border: 1px solid {BORDER}; border-radius: 6px; padding: 6px 12px; }}
QPushButton#tool:hover {{ background: {HOVER}; }}
QPushButton#tool:disabled {{ color: {DISABLED}; }}
QPushButton#primary {{ background: {ACCENT}; color: {TEXT}; border: none; border-radius: 6px; padding: 6px 14px; font-weight: bold; }}
QPushButton#primary:hover {{ background: {ACCENT_HOVER}; }}
QPushButton#primary:disabled {{ background: {BORDER}; color: {DISABLED}; }}
QPlainTextEdit, QLineEdit, QComboBox {{ background: {FIELD}; color: {TEXT}; border: 1px solid {BORDER}; border-radius: 6px; padding: 5px; }}
QPlainTextEdit#logbox {{ font-family: 'Consolas'; color: {MUTED}; }}
QTableView {{ background: {FIELD}; alternate-background-color: {PANEL}; border: 1px solid {BORDER}; border-radius: 8px;
    selection-background-color: {ACTIVE_BG}; selection-color: {TEXT}; outline: none; }}
QTableView::item {{ padding: 4px 6px; border: none; }}
QHeaderView::section {{ background: {CARD}; color: {MUTED}; padding: 6px; border: none; border-bottom: 1px solid {BORDER}; }}
QListWidget {{ background: {FIELD}; border: 1px solid {BORDER}; border-radius: 8px; }}
QListWidget#gallery::item {{ padding: 8px; border-bottom: 1px solid {BORDER}; }}
QListWidget::item:selected {{ background: {ACTIVE_BG}; color: {TEXT}; }}
QCheckBox {{ color: {TEXT}; font-size: 8.5pt; }}
QProgressBar {{ background: {BORDER}; border: none; border-radius: 2px; }}
QProgressBar::chunk {{ background: {CYAN}; border-radius: 2px; }}
QScrollArea {{ border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: {SCROLL}; border-radius: 5px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; }}
QScrollBar::handle:horizontal {{ background: {SCROLL}; border-radius: 5px; min-width: 30px; }}
QDockWidget {{ color: {MUTED}; }}
QMenu {{ background: {CARD}; color: {TEXT}; border: 1px solid {BORDER}; }}
QMenu::item:selected {{ background: {ACTIVE_BG}; }}
"""


def main():
    app = QApplication(sys.argv)
    app.setFont(QFont('Segoe UI', 10))
    app.setStyleSheet(STYLE)
    win = Studio(); win.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
