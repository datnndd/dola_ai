"""Account health, rate limit recovery, and profile lifecycle owned by StudioPython."""
from datetime import datetime, time as day_start, timedelta
from pathlib import Path
import shutil
import time

CREDIT_ERROR = 'Không đủ điểm tín dụng'
WORK_LIMIT_ERROR = 'Đạt giới hạn chế độ Work'
RATE_LIMIT_ERROR = 'Giới hạn tần suất gửi tin nhắn (Rate Limit)'


class CreditExhausted(RuntimeError):
    """Hết điểm / hết lượt dùng Dola. until = thời điểm Dola cho dùng lại (None: 00:00 hôm sau)."""
    def __init__(self, until=None):
        super().__init__(CREDIT_ERROR)
        self.until = until


class WorkLimitReached(RuntimeError):
    """Đạt giới hạn chế độ Work / Pro. until = thời điểm Dola cho dùng lại Work."""
    def __init__(self, until=None):
        super().__init__(WORK_LIMIT_ERROR)
        self.until = until


class RateLimitExceeded(RuntimeError):
    """Bị Rate Limit / 429 Too Many Requests. until = thời điểm hồi phục."""
    def __init__(self, until=None):
        super().__init__(RATE_LIMIT_ERROR)
        self.until = until


def is_credit_error(error):
    return str(error) == CREDIT_ERROR or isinstance(error, CreditExhausted)


def is_work_limit_error(error):
    return str(error) == WORK_LIMIT_ERROR or isinstance(error, WorkLimitReached)


def is_rate_limit_error(error):
    return str(error) == RATE_LIMIT_ERROR or isinstance(error, RateLimitExceeded)


def credit_until(error):
    """Giờ mở lại kèm theo lỗi hết điểm/limit, nếu Dola có báo."""
    return getattr(error, 'until', None)


def credit_unlock_time(until=None, now=None, current=None):
    """Thời điểm mở lại: giờ Dola báo (nếu còn ở tương lai), không thì 00:00 hôm sau.
    Không rút ngắn một khóa đang dài hơn (vd đang khóa 7 ngày thì lỗi sau không đè thành 00:00)."""
    now = time.time() if now is None else now
    target = until if until and until > now else next_midnight(now)
    if current and current > target:
        target = current
    return target


def next_midnight(now=None):
    today = datetime.fromtimestamp(time.time() if now is None else now).date()
    return datetime.combine(today + timedelta(days=1), day_start.min).timestamp()


def credit_locked(account, now=None):
    until = account.get('credit_locked_until') or 0
    return until > (time.time() if now is None else now)


def work_locked(account, now=None):
    until = account.get('work_locked_until') or 0
    return until > (time.time() if now is None else now)


def rate_limited(account, now=None):
    until = account.get('rate_limit_until') or 0
    return until > (time.time() if now is None else now)


def lock_for_credits(account, now=None, until=None):
    """Out of credits: untick 'Dùng chạy' until Dola's reopen time, else 00:00 of the next day."""
    now = time.time() if now is None else now
    current = account.get('credit_locked_until') if credit_locked(account, now) else None
    target = credit_unlock_time(until, now, current)
    account['selected'] = False
    account['credit_locked_until'] = target
    return target


def lock_for_work(account, now=None, until=None):
    """Reached Work mode limit. Sets work_locked_until.
    Account remains selected for normal Chat/Prompt!"""
    now = time.time() if now is None else now
    current = account.get('work_locked_until') if work_locked(account, now) else None
    target = credit_unlock_time(until, now, current)
    account['work_locked_until'] = target
    return target


def lock_for_rate_limit(account, now=None, until=None, seconds=300):
    """Temporary rate limit cooldown (default 5 minutes if no time given)."""
    now = time.time() if now is None else now
    current = account.get('rate_limit_until') if rate_limited(account, now) else None
    target = until if until and until > now else (now + seconds)
    if current and current > target:
        target = current
    account['rate_limit_until'] = target
    return target


def is_account_available(account, mode='chat', check_selected=True, now=None):
    """Check if account is ready to handle a request of type `mode` ('work' or 'chat')."""
    now = time.time() if now is None else now
    if account.get('live_state') == 'die':
        return False
    if check_selected and not account.get('selected', True):
        return False
    if credit_locked(account, now) or run_cooldown_locked(account, now) or rate_limited(account, now):
        return False
    normalized_mode = str(mode or 'chat').strip().lower()
    if normalized_mode in ('work', 'pro') and work_locked(account, now):
        return False
    return True


def unlock_text(timestamp):
    if not timestamp:
        return ''
    return datetime.fromtimestamp(timestamp).strftime('%H:%M %d/%m')


def credit_lock_text(account):
    return 'Hết điểm • mở lại ' + unlock_text(account['credit_locked_until'])


def work_lock_text(account):
    return 'Khóa Work • mở lại ' + unlock_text(account['work_locked_until'])


def rate_limit_text(account):
    return 'Quá tải • mở lại ' + unlock_text(account['rate_limit_until'])


def release_credit_locks(accounts, now=None):
    """Re-tick accounts whose credit lock has ended (00:00 or the reopen time Dola gave)."""
    now = time.time() if now is None else now
    released = []
    for account in accounts:
        until = account.get('credit_locked_until')
        if until and now >= until and account.get('live_state') != 'die':
            account.pop('credit_locked_until', None)
            account['selected'] = True
            released.append(account)
    return released


def release_account_locks(accounts, now=None):
    """Release all expired locks: credit, work limit, rate limit, and run cooldown.
    Returns dict mapping lock types to lists of accounts that were restored."""
    now = time.time() if now is None else now
    released = {'credits': [], 'work': [], 'rate_limit': [], 'cooldown': []}
    for account in accounts:
        if account.get('live_state') == 'die':
            continue
        c_until = account.get('credit_locked_until')
        if c_until and now >= c_until:
            account.pop('credit_locked_until', None)
            account['selected'] = True
            released['credits'].append(account)

        w_until = account.get('work_locked_until')
        if w_until and now >= w_until:
            account.pop('work_locked_until', None)
            released['work'].append(account)

        r_until = account.get('rate_limit_until')
        if r_until and now >= r_until:
            account.pop('rate_limit_until', None)
            released['rate_limit'].append(account)

        cd_until = account.get('run_cooldown_until')
        if cd_until and now >= cd_until:
            account.pop('run_cooldown_until', None)
            account['selected'] = True
            released['cooldown'].append(account)
    released['rate_limits'] = released['rate_limit']
    released['cooldowns'] = released['cooldown']
    return released


RUN_COOLDOWN_SECONDS = 7 * 24 * 3600


def run_cooldown_locked(account, now=None):
    until = account.get('run_cooldown_until') or 0
    return until > (time.time() if now is None else now)


def lock_for_run_cooldown(account, now=None, seconds=RUN_COOLDOWN_SECONDS):
    """Ran a video successfully: untick 'Dùng chạy' and rest this account for `seconds`
    (default 7 days). Never shorten a longer rest already in place."""
    now = time.time() if now is None else now
    target = max(now + seconds, account.get('run_cooldown_until') or 0)
    account['selected'] = False
    account['run_cooldown_until'] = target
    return target


def run_cooldown_text(account):
    return 'Chạy xong • nghỉ đến ' + unlock_text(account['run_cooldown_until'])


def release_run_cooldowns(accounts, now=None):
    """Re-tick accounts whose 7-day rest after a successful run has ended."""
    now = time.time() if now is None else now
    released = []
    for account in accounts:
        until = account.get('run_cooldown_until')
        if until and now >= until and account.get('live_state') != 'die':
            account.pop('run_cooldown_until', None)
            account['selected'] = True
            released.append(account)
    return released


def account_status_badge(account, now=None):
    """Returns status badge information: label text, color, can_work, can_chat."""
    now = time.time() if now is None else now
    if account.get('live_state') == 'die':
        return {'text': 'Chết phiên', 'color': '#ff4d4f', 'can_work': False, 'can_chat': False}
    if credit_locked(account, now):
        return {'text': 'Hết điểm • mở lại ' + unlock_text(account['credit_locked_until']),
                'color': '#fa8c16', 'can_work': False, 'can_chat': False}
    if rate_limited(account, now):
        return {'text': 'Quá tải • mở lại ' + unlock_text(account['rate_limit_until']),
                'color': '#fadb14', 'can_work': False, 'can_chat': False}
    if run_cooldown_locked(account, now):
        return {'text': 'Nghỉ chạy • mở lại ' + unlock_text(account['run_cooldown_until']),
                'color': '#8c8c8c', 'can_work': False, 'can_chat': False}
    if work_locked(account, now):
        return {'text': 'Khóa Work (Chat OK) • mở lại ' + unlock_text(account['work_locked_until']),
                'color': '#1890ff', 'can_work': False, 'can_chat': True}
    return {'text': 'Sẵn sàng (Work + Chat)', 'color': '#52c41a', 'can_work': True, 'can_chat': True}


def dead_accounts(accounts):
    # Missing names, proxy errors and timeouts are not proof of an expired login.
    return [a for a in accounts if a.get('live_state') == 'die']


def purge_managed_profile(settings, account, other_accounts=()):
    data = Path(settings['lock_root']).resolve().parent
    raw = account.get('profile')
    if not raw:
        raise ValueError('Profile không có đường dẫn; chưa xóa.')
    target = Path(raw).resolve()
    roots = [(data / name).resolve() for name in ('profiles', 'chrome_profiles')]
    if not any(root.is_relative_to(data) and root != data and target != root and target.is_relative_to(root) for root in roots):
        raise ValueError('Profile nằm ngoài thư mục do StudioPython quản lý; giữ lại để tránh xóa dữ liệu ứng dụng khác.')
    for other in other_accounts:
        if other.get('id') != account.get('id') and other.get('profile'):
            other_path = Path(other['profile']).resolve()
            if target == other_path or other_path.is_relative_to(target) or target.is_relative_to(other_path):
                raise ValueError('Đường dẫn profile đang được tài khoản khác dùng chung; chưa xóa.')
    if target.exists():
        if not target.is_dir():
            raise ValueError('Đường dẫn profile không phải thư mục; chưa xóa.')
        shutil.rmtree(target)
    # Only erase the exact saved cookie file if it is owned by this data directory.
    cookie = account.get('cookie_file')
    if cookie:
        cookie_path = Path(cookie).resolve()
        shared = any(other.get('id') != account.get('id') and other.get('cookie_file')
                     and Path(other['cookie_file']).resolve() == cookie_path for other in other_accounts)
        if not shared and cookie_path.is_relative_to(data) and cookie_path.is_file():
            cookie_path.unlink()
    return True
