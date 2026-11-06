"""Studio Python backend shared by the UI: engines, accounts, proxies, Auto, settings.
No UI toolkit is imported here (the Qt app loads this module, never tkinter)."""
from __future__ import annotations
import copy
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from engine import (ORIGINAL, Cancelled, atomic_json, find_ffmpeg,
                    render_local, render_adapter, CREATE_NO_WINDOW)
from dola_browser import render_dola, finish_video, discover_accounts, find_browser, find_3big_browser, account_browser, cookie_account_browser, register_dola_account, ProfileLease
from cookie_accounts import parse_cookie_line, parse_facebook_cookie_line, facebook_cookie_from_line, parse_proxy, save_cookies, proxy_input_value, protect
from proxyvn import (PROXY_MODE_LABELS, account_proxy_mode, endpoint, keys_text, load_keys,
                     label_for_mode, normalize_proxy_settings, proxy_mode_text, proxy_source)
from proxyno1 import ProxyNo1Error, key_status, rotation_status, rotator_for, run_rotation, stop_rotation
from gpm_browser import GpmClient, GpmError, normalize_url, delete_account_profile
from hidemium_browser import HidemiumError, normalize_url as normalize_hidemium_url
from video_postprocess import default_video_edit, validate_video_edit, export_video
from task_status import is_failed_status, is_retryable_status
from task_runtime import TaskCancel, task_failure_status, task_settings
from dola_cookie_import import parse_dola_accounts
from account_health import (dead_accounts, purge_managed_profile, is_credit_error, lock_for_credits,
                            credit_locked, credit_lock_text, release_credit_locks, CREDIT_ERROR,
                            CreditExhausted, credit_until, unlock_text,
                            run_cooldown_locked, lock_for_run_cooldown, run_cooldown_text, release_run_cooldowns,
                            WorkLimitReached, RateLimitExceeded, is_work_limit_error, is_rate_limit_error,
                            work_locked, rate_limited, lock_for_work, lock_for_rate_limit,
                            release_account_locks, account_status_badge, is_account_available,
                            rate_limit_text, work_lock_text)
from account_selector import sort_accounts_by_lru, select_best_account, get_earliest_recovery_time
from dola_browser import session_snapshot_path
from auto_jobs import AutoJobs, validate_machine, output_for_job, task_output_path
from sheet_video import SheetAutoJobs, DEFAULT_URL, reference_defaults
from work_queue import WorkQueue, next_attempt
from proxyvn import manual_proxy_text, assign_manual_proxies

# Chạy từ EXE đã đóng gói: PyInstaller đặt sys.frozen, Nuitka đặt __compiled__ cho module.
# Phải lấy thư mục cạnh EXE (không phải thư mục giải nén tạm của Nuitka onefile) để dữ liệu không bị mất.
def _app_home():
    # Thư mục chứa EXE thật. Nuitka onefile đặt sys.executable ở thư mục giải nén tạm, nên phải
    # lấy qua __main__.__compiled__.containing_dir; PyInstaller dùng sys.executable; dev dùng mã nguồn.
    main = sys.modules.get('__main__')
    containing = getattr(getattr(main, '__compiled__', None), 'containing_dir', None)
    if containing:
        return Path(containing)
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


HOME = _app_home()
# Con trỏ thư mục dữ liệu do người dùng chọn (lưu ở LOCALAPPDATA để không phụ thuộc chỗ đặt EXE).
DATA_POINTER = Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'SeedanceAuto' / 'data_dir.txt'


def saved_data_dir():
    try:
        if DATA_POINTER.is_file():
            text = DATA_POINTER.read_text('utf-8').strip()
            if text:
                return Path(text)
    except OSError:
        pass
    return None


def set_data_dir(path):
    """Ghi nhớ thư mục lưu dữ liệu; có hiệu lực ở lần mở tool tiếp theo."""
    DATA_POINTER.parent.mkdir(parents=True, exist_ok=True)
    DATA_POINTER.write_text(str(Path(path)), encoding='utf-8')


def resolve_data_dir():
    """Thứ tự: biến môi trường STUDIO_DATA_DIR > thư mục đã chọn > <cạnh EXE>/data."""
    env = os.environ.get('STUDIO_DATA_DIR')
    if env:
        return Path(env)
    chosen = saved_data_dir()
    return chosen if chosen is not None else HOME / 'data'


DATA = resolve_data_dir()


def list_gallery_videos(folder, hidden):
    """List saved MP4 files. Call off the UI thread; resolving many paths can stall."""
    hidden = set(hidden or [])
    files = sorted((p for p in Path(folder).rglob('*.mp4') if '_previous' not in p.parts),
                   key=lambda path: path.stat().st_mtime, reverse=True)
    rows = []
    for path in files:
        if '.download' in path.name or '.export' in path.name:
            continue
        resolved = str(path.resolve())
        if resolved in hidden:
            continue
        stat = path.stat()
        rows.append((resolved, path.name, stat.st_size, stat.st_mtime))
    return rows


def batch_blockers(cfg, jobs, accounts, all_accounts, busy_ids, resume):
    """Profile and file checks for a batch. Returns (problems, title, use_info_dialog)."""
    busy_profiles = set()
    for account in all_accounts:
        if account.get('id') in busy_ids and account.get('profile'):
            busy_profiles.add(os.path.normcase(str(Path(account['profile']).resolve())))
    if cfg['engine'] != 'local' and any(
            account['id'] in busy_ids or (
                account.get('profile') and os.path.normcase(str(Path(account['profile']).resolve())) in busy_profiles)
            for account in accounts):
        return (['Đóng cửa sổ đăng nhập hoặc chờ kiểm tra của tài khoản được chọn hoàn tất.'],
                'Profile đang mở', True)
    if cfg['engine'] == 'local':
        missing = [task['name'] for task in jobs if not task['images'] or any(not Path(path).is_file() for path in task['images'])]
        if missing or not find_ffmpeg(cfg['ffmpeg']):
            detail = 'Mỗi task cần ít nhất một ảnh còn tồn tại và FFmpeg đã cấu hình.'
            if missing:
                detail += '\n' + ', '.join(missing)
            return ([detail], 'Chưa thể chạy', False)
        return ([], '', False)
    if cfg['engine'] == 'dola':
        problems = []
        try:
            validate_video_edit(cfg.get('video_edit'))
        except (ValueError, TypeError) as exc:
            problems.append('Cấu hình FFmpeg: ' + str(exc))
        if not accounts:
            problems.append('Chọn ít nhất một tài khoản.')
        from account_browser_backend import account_backend, account_settings
        for account in accounts:
            try:
                account_settings(cfg, account, require_profile=False)
            except ValueError as exc:
                problems.append(str(exc))
        if any(account_backend(a) == 'hidemium' for a in accounts):
            try:
                normalize_hidemium_url(cfg.get('hidemium_api', ''))
            except HidemiumError as exc:
                problems.append(str(exc))
            ids = [str(a.get('hidemium_profile_id')) for a in accounts if a.get('hidemium_profile_id')]
            if len(set(ids)) != len(ids):
                problems.append('Hai tài khoản đang dùng cùng một UUID profile Hidemium.')
        elif cfg.get('use_gpm'):
            try:
                normalize_url(cfg.get('gpm_api', ''))
            except GpmError as exc:
                problems.append(str(exc))
        if any(account_backend(a) == 'chrome' for a in accounts) and not find_browser(cfg.get('browser', '')):
            problems.append('Chọn Chromium/Chrome trong Cài đặt.')
        if not find_ffmpeg(cfg.get('ffmpeg', '')):
            problems.append('Cần FFmpeg để kiểm tra/xuất video.')
        if not resume and any(not str(task.get('prompt', '')).strip() for task in jobs):
            problems.append('Nhập prompt cho mọi task sẽ chạy.')
        if any(not account.get('profile') for account in accounts):
            problems.append('Mỗi tài khoản cần một profile trình duyệt.')
        profiles = [os.path.normcase(str(Path(account['profile']).resolve()))
                    for account in accounts if account.get('profile')]
        if len(set(profiles)) != len(profiles):
            problems.append('Hai tài khoản đang trỏ tới cùng một profile. Chọn lại profile riêng.')
        if not resume and any(len(task.get('images') or []) > 10 or any(not Path(path).is_file() for path in task.get('images') or [])
                              for task in jobs):
            problems.append('Kiểm tra lại ảnh tham chiếu.')
        if problems:
            return (problems, 'Chưa thể chạy Dola', False)
        return ([], '', False)
    if (not accounts or not Path(cfg.get('adapter') or '').is_file() or not Path(cfg.get('python') or '').is_file()
            or any(not str(task.get('prompt', '')).strip() for task in jobs)):
        return (['Cần cấu hình adapter .py, python.exe, chọn tài khoản và nhập prompt cho mọi task.\nXem ADAPTER.md trong thư mục ứng dụng.'],
                'Chưa kết nối dịch vụ AI', False)
    return ([], '', False)


def fresh_task(number=1):
    return {'id': uuid.uuid4().hex, 'name': f'Task #{number}', 'prompt': '', 'images': [], 'status': 'Chờ xử lý'}


def defaults():
    return {'mode': 1, 'model': 'Seedance 2.5', 'dola_mode': 'pro', 'duration': '15',
            'auto_duration_policy': 'web', 'one_job_per_account': True,
            'fallback_to_dola_link': True, 'ratio': '9:16',
            'upscale': True, 'output': str(HOME / 'downloads'), 'concurrency': 2, 'tabs_per_profile': 3,
            'engine': 'dola', 'ffmpeg': find_ffmpeg(), 'adapter': '',
            'python': shutil.which('python') or '', 'timeout': 900, 'per_task_timeout_version': 1,
            'action_delay': 3,
            'video_source': 'api_preview', 'video_source_version': 9, 'download_pipeline': 'dola_dom',
            'video_edit': default_video_edit(),
            'browser_backend': 'chrome', 'use_hidemium': False,
            'hidemium_api': 'http://127.0.0.1:2222',
            'use_gpm': False, 'use_3big_browser': True, 'gpm_api': '', 'show_gpm_controls': False,
            'seedance_browser': True, 'seedance_flow': 'auto', 'parallel_chat_submit': True,
            'skip_login_precheck': True,
            'auto_render_key_protected': '', 'auto_machine_code': '',
            'sheet_video_url': DEFAULT_URL, 'sheet_video_tab': 'Sheet1',
            'sheet_video_credentials': '', 'sheet_return_errors': True, **reference_defaults(),
            'default_proxy': {}, 'proxy_source': 'proxyno1', 'proxyno1_keys_protected': '',
            'proxyvn_keys_protected': '', 'proxy_rotate_mode': 'auto', 'proxy_rotate_minutes': 15,
            'gpm_hidden': False,
            'manual_new_login': False, 'browser_stack_version': 5,
            'browser': find_browser(), 'show_browser': True, 'load_extension': True, 'extension_bundle_version': 2, 'auto_retry': False, 'lock_root': str(DATA / 'locks'), 'import_on_start': True}


def build_facebook_cookie_accounts(cookie_lines, proxy_lines):
    """(cookies, proxy, mode) per line. Raises ValueError naming the bad line only."""
    built = []
    for index, line in enumerate(cookie_lines, 1):
        try:
            cookies = facebook_cookie_from_line(line)
        except ValueError as exc:
            raise ValueError(f'Dòng {index}: {exc}') from None
        raw = proxy_lines[index - 1].strip() if index - 1 < len(proxy_lines) else ''
        if raw:
            proxy = parse_proxy(raw)
            if not proxy:
                raise ValueError(f'Dòng {index}: proxy không hợp lệ (host:port hoặc host:port:user:pass).')
            built.append((cookies, proxy, 'manual'))
        else:
            built.append((cookies, {}, 'default'))
    return built


def encode_dola_accounts(parsed, proxy_cfg, backend='chrome'):
    """Encrypt Dola cookies into new accounts. Safe off the UI thread; touches no account list.

    backend ('chrome'|'hidemium') is the browser chosen at import time; it becomes the
    account's permanent backend. A Hidemium account with no UUID gets a profile created
    on first open (see account_browser_backend.prepare_account_settings)."""
    backend = backend if backend in ('chrome', 'hidemium') else 'chrome'
    label = 'Cookie Dola • Hidemium • chưa kiểm tra' if backend == 'hidemium' else 'Cookie Dola • chưa kiểm tra'
    added = []
    for item in parsed:
        aid = uuid.uuid4().hex
        path = save_cookies(DATA / 'cookie_vault', aid, item['cookies'])
        added.append({'id': aid, 'name': item['name'], 'profile': str(DATA / 'profiles' / aid),
                      'cookie_file': path, 'cookie_kind': 'dola', 'proxy': copy.deepcopy(proxy_cfg),
                      'proxy_mode': 'manual' if proxy_cfg else 'default', 'browser_backend': backend,
                      'selected': True, 'connection': label, 'session_verified_at': 0})
    return added


def encode_google_accounts(lines, proxy_cfg, name_base, backend='chrome'):
    """lines: [(line_no, text)]. Encrypt Google cookies into new accounts; ValueError names the bad line.

    backend ('chrome'|'hidemium') is the browser chosen at import time and becomes permanent."""
    backend = backend if backend in ('chrome', 'hidemium') else 'chrome'
    parsed = []
    for i, line in lines:
        try:
            parsed.append(parse_cookie_line(line))
        except ValueError as exc:
            raise ValueError(f'Dòng {i}: {exc}') from None
    added = []
    for cookies in parsed:
        aid = uuid.uuid4().hex
        added.append({'id': aid, 'name': f'Google {name_base + len(added) + 1}',
                      'profile': str(DATA / 'profiles' / aid), 'selected': True, 'connection': 'Chờ đăng nhập cookie',
                      'cookie_file': save_cookies(DATA / 'cookie_vault', aid, cookies), 'proxy': copy.deepcopy(proxy_cfg),
                      'proxy_mode': 'manual' if proxy_cfg else 'default', 'browser_backend': backend})
    return added


def safe_login_error(exc):
    # Browser exceptions can echo proxy credentials; use a safe summary.
    if isinstance(exc, (ValueError, RuntimeError)) and not type(exc).__module__.startswith('playwright'):
        return 'Chưa lưu tài khoản: ' + str(exc)
    return 'Đăng nhập bị gián đoạn; chưa lưu tài khoản. Kiểm tra mạng/proxy hoặc đóng cửa sổ cũ rồi thử lại.'
