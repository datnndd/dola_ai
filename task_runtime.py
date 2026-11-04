"""Per-task settings and cancellation independent of sibling tasks."""
import time
import re

TASK_OPTION_KEYS = ('duration', 'model', 'dola_mode', 'ratio', 'timeout')
DEFAULT_TASK_TIMEOUT = 900
# A job that cannot get its browser/profile up and ready within this many seconds
# is released back to the web queue so the thread can claim another, instead of
# sitting at "Chờ chạy" forever. Configurable via settings['auto_startup_timeout'].
DEFAULT_STARTUP_TIMEOUT = 120


def requires_skill_file(settings):
    """Duration is authoritative: only an actual 30-second task uses 30s handling."""
    duration = str(settings.get('duration', '')).strip()
    return bool(re.fullmatch(r'30(?:\.0+)?(?:\s*(?:s|giây|seconds?))?', duration, re.I))


def task_timeout_seconds(settings):
    """Seconds before this task stops. The clock is not shared with other tasks."""
    try:
        value = int(float((settings or {}).get('timeout', DEFAULT_TASK_TIMEOUT)))
    except (TypeError, ValueError):
        value = DEFAULT_TASK_TIMEOUT
    return min(7200, max(30, value))


def startup_timeout_seconds(settings):
    """Seconds a job may spend starting its browser/profile before it is given
    up and handed back to the web. Kept short so a stuck launch never holds a
    thread; bounded to [30, 1800]."""
    try:
        value = int(float((settings or {}).get('auto_startup_timeout', DEFAULT_STARTUP_TIMEOUT)))
    except (TypeError, ValueError):
        value = DEFAULT_STARTUP_TIMEOUT
    return min(1800, max(30, value))


class StartupDeadline:
    """A cancel token used ONLY while a profile/browser is starting up. It fires
    when the base cancel fires OR the startup budget elapses, so every wait that
    honours cancel (proxy rotation, CDP connect, extension/login checks) is also
    bounded by the budget. `expired` tells a real stop apart from the timeout."""
    def __init__(self, base, seconds):
        self.base = base
        self.deadline = time.monotonic() + max(1, seconds)

    @property
    def expired(self):
        return time.monotonic() >= self.deadline

    def is_set(self):
        return self.base.is_set() or self.expired

    def wait(self, timeout=None):
        remaining = max(0, self.deadline - time.monotonic())
        budget = remaining if timeout is None else min(timeout, remaining)
        self.base.wait(budget)
        return self.is_set()


def ensure_task_time(settings, report=None):
    deadline = (settings or {}).get('_task_deadline')
    if deadline is None or time.monotonic() < deadline:
        return
    if report:
        report(f'Hết {task_timeout_seconds(settings)} giây của task này. Dừng task này, các task khác vẫn chạy.')
    raise TimeoutError('Hết thời gian task')


class VideoDownloadError(RuntimeError):
    """Dola made the video, but saving/checking the MP4 failed. Use 'Lấy video' to retry."""


class PolicyBlocked(RuntimeError):
    """Dola refused the prompt under its content safety policy."""


class RetryLimit(RuntimeError):
    """Dola kept replying with text only after every confirmation retry."""


class StartupTimeout(RuntimeError):
    """The browser/profile took too long to start (never reached 'đang chạy').
    The job is released to the web queue so another thread/account can take it."""


def task_failure_status(error):
    text = str(error)
    if text in ('Không đủ điểm tín dụng', 'Hết thời gian task'):
        return text
    if isinstance(error, PolicyBlocked):
        return f'Vi phạm chính sách • {error}'
    if isinstance(error, RetryLimit):
        return f'Lỗi 3 lần • {error}'
    if isinstance(error, StartupTimeout):
        return f'Quá giờ mở • {error}'
    if isinstance(error, VideoDownloadError):
        return f'Lỗi tải video • {error}'
    return f'Lỗi • {error}'


def task_settings(settings, task):
    """One dict per task. A shared deadline must not carry from one task into the next."""
    merged = {**(settings or {}), **{k: v for k, v in (task or {}).get('options', {}).items() if k in TASK_OPTION_KEYS}}
    merged.pop('_task_deadline', None)
    # Legacy workspaces stored 30s as a mode. Keep the selected duration and
    # normalize that old mode to Pro, so choosing 15s can never be forced to 30s.
    if str(merged.get('dola_mode', '')).lower() == 'skill30':
        merged['dola_mode'] = 'pro'
    if task and task.get('_auto_deadline') is not None:
        merged['_task_deadline'] = task['_auto_deadline']
    return merged


class TaskCancel:
    def __init__(self, batch, task):
        self.batch, self.task = batch, task

    def is_set(self):
        return self.batch.is_set() or self.task.is_set()

    def wait(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        while not self.is_set():
            remaining = .05 if deadline is None else min(.05, deadline - time.monotonic())
            if remaining <= 0:
                return False
            self.task.wait(remaining)
        return True
