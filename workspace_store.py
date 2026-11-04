"""Lưu workspace.json trên luồng nền để giao diện không bị khựng khi bảng task lớn.

Giao diện chỉ chụp nhanh dữ liệu thành chuỗi JSON (không kèm nhật ký của task — nhật ký đã có
file riêng trong data/task_logs); luồng nền ghi file. Nhiều lần lưu dồn dập chỉ ghi bản mới nhất."""
from __future__ import annotations
import json
import os
from pathlib import Path
import threading

SKIP_TASK_KEYS = frozenset({'task_logs'})


def workspace_snapshot(settings, tasks, accounts, hidden):
    """Chuỗi JSON của workspace; bỏ nhật ký task (có thể hàng trăm dòng mỗi task)."""
    slim = [{k: v for k, v in task.items() if k not in SKIP_TASK_KEYS} for task in tasks]
    return json.dumps({'version': 2, 'settings': settings, 'tasks': slim,
                       'accounts': accounts, 'hidden': hidden}, ensure_ascii=False)


def write_atomic(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    with open(temp, 'w', encoding='utf-8') as handle:
        handle.write(text)
    os.replace(temp, path)


class WorkspaceWriter:
    def __init__(self, path, on_error=None):
        self.path = Path(path)
        self.on_error = on_error or (lambda exc: None)
        self.lock = threading.Lock()
        self.write_lock = threading.Lock()
        self.pending = None
        self.wake = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True, name='workspace-writer')
        self.thread.start()

    def submit(self, text):
        with self.lock:
            self.pending = text
        self.wake.set()

    def _take(self):
        with self.lock:
            text, self.pending = self.pending, None
        return text

    def _write(self, text):
        with self.write_lock:
            try:
                write_atomic(self.path, text)
            except OSError as exc:
                self.on_error(exc)

    def _run(self):
        while True:
            self.wake.wait()
            self.wake.clear()
            text = self._take()
            if text is not None:
                self._write(text)

    def flush(self):
        """Ghi ngay bản đang chờ (dùng khi đóng app)."""
        text = self._take()
        if text is not None:
            self._write(text)
        else:
            with self.write_lock:  # chờ lượt ghi đang chạy dở xong
                pass
