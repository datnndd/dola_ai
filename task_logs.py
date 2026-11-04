"""Nhật ký riêng của từng task, ghi ra file để còn xem được sau khi task bị xóa khỏi bảng.

data/task_logs/<ngày>/<tên task>_<id>.log — mỗi dòng có giờ. Ghi theo lô (flush) để không chặn giao diện."""
from __future__ import annotations
from pathlib import Path
import re
import threading
import time

MAX_LINES_IN_TASK = 500


def safe_label(task):
    name = re.sub(r'[^\w.-]+', '_', str(task.get('name') or 'task'), flags=re.UNICODE).strip('_.')
    return (name or 'task')[:80] + '_' + str(task.get('id') or 'x')[:8]


class TaskLogFiles:
    def __init__(self, root, clock=time.localtime):
        self.root = Path(root)
        self.clock = clock
        self.lock = threading.Lock()
        self.pending = {}          # Path -> [lines]
        self.known = set()         # file đã có dòng tiêu đề (khỏi hỏi ổ đĩa mỗi dòng)

    def path_for(self, task):
        """File log của task; chọn một lần rồi lưu vào task['log_file'] để giữ qua các lần mở app."""
        saved = task.get('log_file')
        if saved:
            return Path(saved)
        path = self.root / time.strftime('%Y-%m-%d', self.clock()) / (safe_label(task) + '.log')
        task['log_file'] = str(path)
        return path

    def add(self, task, line):
        path = self.path_for(task)
        with self.lock:
            lines = self.pending.setdefault(path, [])
            if path not in self.known:
                self.known.add(path)
                if not path.exists():
                    lines.append(f"# {task.get('name') or 'Task'} • {time.strftime('%d/%m/%Y %H:%M:%S', self.clock())}")
            lines.append(line)
        return path

    def tail(self, task, limit=300):
        """Các dòng cuối trong file nhật ký của task (đọc tối đa 128 KB cuối file)."""
        saved = task.get('log_file')
        if not saved:
            return []
        path = Path(saved)
        try:
            with open(path, 'rb') as handle:
                handle.seek(0, 2)
                size = handle.tell()
                handle.seek(max(0, size - 128 * 1024))
                text = handle.read().decode('utf-8', 'replace')
        except OSError:
            return []
        lines = [line for line in text.splitlines() if line and not line.startswith('# ')]
        if size > 128 * 1024 and lines:
            lines = lines[1:]  # dòng đầu có thể bị cắt giữa chừng
        return lines[-limit:]

    def start_flusher(self, interval=1.0, on_failure=None):
        """Luồng nền ghi các dòng đang chờ mỗi `interval` giây (không chạy trên luồng giao diện)."""
        stop = threading.Event()
        def run():
            warned = False
            while not stop.wait(interval):
                if self.flush() and not warned and on_failure:
                    warned = True
                    on_failure()
            self.flush()
        threading.Thread(target=run, daemon=True, name='task-log-flusher').start()
        return stop

    def flush(self):
        """Ghi các dòng đang chờ; trả về số file lỗi (thư mục data không ghi được)."""
        with self.lock:
            pending, self.pending = self.pending, {}
        failed = 0
        for path, lines in pending.items():
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, 'a', encoding='utf-8') as handle:
                    handle.write('\n'.join(lines) + '\n')
            except OSError:
                failed += 1
        return failed


def stamp(message, clock=time.localtime):
    return f'[{time.strftime("%H:%M:%S", clock())}] {message}'
