"""Local Seedance Chrome discovery, profile migration and proxy ownership."""
import hashlib
import os
import threading
from pathlib import Path
from urllib.parse import urlsplit

from engine import app_dir


def executable():
    folder = app_dir() / 'browser'
    candidates = list(folder.glob('chromium-*/chrome-win64/chrome.exe'))
    candidates.sort(key=lambda p: int(p.parents[1].name.split('-')[-1]), reverse=True)
    candidates += [folder / 'chrome-win64/chrome.exe', folder / 'chrome.exe']
    found = next((path.resolve() for path in candidates if path.is_file()), None)
    if found is not None:
        from bundle_integrity import verify_component
        verify_component('browser')
        return str(found)
    ms_pw = Path(os.environ.get('LOCALAPPDATA', '')) / 'ms-playwright'
    if ms_pw.is_dir():
        pw_candidates = sorted(ms_pw.glob('chromium-*/chrome-win*/chrome.exe'), reverse=True)
        found = next((path.resolve() for path in pw_candidates if path.is_file()), None)
        if found is not None:
            return str(found)
    return ''


def migrate_settings(settings, accounts, data):
    """Only edit in-memory configuration; never copy or delete GPM profiles."""
    if settings.get('use_gpm'):
        settings['show_browser'] = not bool(settings.get('gpm_hidden', False))
    backend = settings.get('browser_backend')
    if backend not in ('chrome', 'hidemium'):
        backend = 'hidemium' if settings.get('use_hidemium') else 'chrome'
    settings.update(use_gpm=False, use_hidemium=backend == 'hidemium', browser_backend=backend,
                    use_3big_browser=backend == 'chrome', show_gpm_controls=False,
                    seedance_browser=backend == 'chrome', manual_new_login=False,
                    browser_stack_version=6, browser=settings.get('browser') or executable())
    settings.setdefault('hidemium_api', 'http://127.0.0.1:2222')
    settings.setdefault('seedance_flow', 'auto')
    settings.setdefault('parallel_chat_submit', True)
    # Luồng API chuỗi hội thoại (seedance_api) có thể treo khi web trả video ở thẻ
    # sản phẩm mà API không mirror ra MP4; quay về luồng DOM/media (bản gốc) để
    # dò video sẵn sàng và tải như toolvidaluat. 30s vẫn tự dùng luồng riêng.
    if settings.get('download_pipeline') in (None, 'seedance_api'):
        settings['download_pipeline'] = 'dola_dom'
    settings['gpm_hidden'] = False
    # Enable each newly supplied extension bundle once. Later checkbox choices
    # remain persistent after this Pro migration.
    if settings.get('extension_bundle_version', 0) < 2:
        settings['load_extension'] = True
        settings['extension_bundle_version'] = 2
    for account in accounts:
        old = str(account.get('profile', ''))
        if account.get('gpm_profile_id') or 'gpm_profile_locks' in old.replace('\\', '/'):
            account['legacy_gpm'] = {'profile': old, 'id': account.pop('gpm_profile_id', ''),
                                     'api': account.pop('gpm_api', settings.get('gpm_api', ''))}
            # Hash the identifier to keep paths inside the managed directory.
            name = hashlib.sha256(str(account['id']).encode()).hexdigest()[:24]
            account['profile'] = str(Path(data) / 'chrome_profiles' / name)
            account['session_verified_at'] = 0
            account['connection'] = 'Chrome Seedance · Cần kiểm tra/đăng nhập lại'
        account['browser_executable'] = settings['browser']
        from account_browser_backend import account_backend
        account.setdefault('browser_backend', account_backend(account))


class ProxyLease:
    def __init__(self, account_id=None, bridge=None):
        self.account_id = account_id
        self.bridge = bridge
        self.closed = False
        self.lock = threading.Lock()

    def start(self):
        if self.bridge is not None:
            self.bridge.start()

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
        try:
            if self.bridge is not None:
                self.bridge.close()
        finally:
            if self.account_id is not None:
                import proxy_pool
                proxy_pool.pool().release(self.account_id)


def proxy_account(settings, account, cancel, report, *, unique):
    """Resolve a profile lease, then expose an auth-free proxy endpoint to Chrome.

    Authenticated HTTP(S) and SOCKS5 use a per-profile loopback bridge, retaining
    credentials only in Python memory for this browser lifetime.
    """
    from proxyvn import gpm_proxy_account, account_proxy_mode, proxy_source
    leased = unique and account_proxy_mode(account) not in ('off', 'manual') and proxy_source(settings) != 'manual'
    lease = ProxyLease(account.get('id', '')) if leased else None
    def local_report(message):
        if report:
            report(message.replace('GPM', 'Chrome Seedance'))
    try:
        runtime = gpm_proxy_account(settings, account, cancel, local_report, unique=unique)
        from cookie_accounts import browser_proxy
        proxy = browser_proxy(runtime)
        needs_bridge = proxy and (proxy.get('username') or proxy.get('password') or
                                  urlsplit(proxy['server']).scheme in ('socks5', 'socks5h'))
        if needs_bridge:
            from proxy_bridge import ProxyBridge
            bridge = ProxyBridge(proxy, local_report)
            if lease is None:
                lease = ProxyLease()
            lease.bridge = bridge
            lease.start()
            runtime = dict(runtime, proxy={'server': bridge.url})
            local_report('Đã gắn proxy qua bridge cục bộ riêng của profile; thông tin xác thực giữ trong tool.')
        return runtime, lease
    except Exception:
        if lease:
            lease.close()
        raise
