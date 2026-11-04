"""Hidemium 4 local API and Playwright CDP bridge.

The Hidemium application owns profile storage and fingerprint settings.  This
module only opens a profile selected by UUID (or an unambiguous exact name),
then attaches Playwright to the loopback debugging port returned by Hidemium.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, ProxyHandler

from cookie_accounts import browser_proxy, load_cookies
from engine import Cancelled
from gpm_browser import open_startup_page, move_window_offscreen
from studio_extension import extension_dir, verify_loaded


class HidemiumError(RuntimeError):
    pass


def normalize_url(value):
    """Accept only a loopback Hidemium API root."""
    value = str(value or 'http://127.0.0.1:2222').strip().rstrip('/')
    if value.isdigit():
        value = 'http://127.0.0.1:' + value
    elif '://' not in value:
        value = 'http://' + value
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != 'http' or parsed.hostname not in ('localhost', '127.0.0.1', '::1')
                or not parsed.port or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in ('', '/')):
            raise ValueError
    except ValueError:
        raise HidemiumError('Hidemium API phải là port hoặc http://127.0.0.1:PORT (chỉ máy cục bộ).') from None
    return f'http://{parsed.netloc}'


def _safe_error(data):
    if isinstance(data, dict):
        text = data.get('message') or data.get('title') or data.get('status') or data.get('type') or ''
    else:
        text = ''
    text = ' '.join(str(text).split())
    return text[:160] if text and not re.search(r'https?://|\b\d{1,3}(?:\.\d{1,3}){3}\b', text) else 'yêu cầu bị từ chối'


class HidemiumClient:
    def __init__(self, base='http://127.0.0.1:2222'):
        self.root = normalize_url(base)
        self.opener = build_opener(ProxyHandler({}))

    def request(self, path, *, method='GET', body=None, query=None, timeout=60):
        url = self.root + path + (('?' + urlencode(query)) if query else '')
        raw = json.dumps(body).encode('utf-8') if body is not None else None
        request = Request(url, data=raw, method=method,
                          headers={'Content-Type': 'application/json', 'Accept': 'application/json'})
        try:
            with self.opener.open(request, timeout=timeout) as response:
                data = json.load(response)
        except HTTPError as exc:
            raise HidemiumError(f'Hidemium API trả HTTP {exc.code}; mở Hidemium 4 và bật Local API.') from None
        except (URLError, TimeoutError, OSError):
            raise HidemiumError('Không kết nối được Hidemium API. Mở Hidemium 4 và kiểm tra port 2222.') from None
        except ValueError:
            raise HidemiumError('Hidemium API không trả JSON hợp lệ.') from None
        if not isinstance(data, dict):
            raise HidemiumError('Hidemium API trả dữ liệu không hợp lệ.')
        status = str(data.get('status') or data.get('type') or '').casefold()
        nested = data.get('data') if isinstance(data.get('data'), dict) else {}
        nested_status = str(nested.get('type') or '').casefold()
        if status in ('error', 'failed', 'fail') or nested_status in ('error', 'failed', 'fail'):
            raise HidemiumError('Hidemium: ' + _safe_error(nested or data) + '.')
        return data

    def profiles(self, *, local):
        body = {'orderName': 0, 'orderLastOpen': 0, 'page': 1, 'limit': 1000,
                'search': '', 'status': '', 'date_range': ['', ''], 'folder_id': []}
        data = self.request('/v1/browser/list', method='POST', body=body,
                            query={'is_local': str(bool(local)).lower()})
        candidates = [data, data.get('data'), (data.get('data') or {}).get('data')
                      if isinstance(data.get('data'), dict) else None]
        for item in candidates:
            if not isinstance(item, dict):
                continue
            rows = item.get('content')
            if isinstance(rows, list):
                return rows
        return []

    def resolve_profile(self, account):
        explicit = str(account.get('hidemium_profile_id') or '').strip()
        if explicit:
            if not re.fullmatch(r'[A-Za-z0-9_-]{4,160}', explicit):
                raise HidemiumError('UUID profile Hidemium không hợp lệ.')
            return explicit
        wanted = str(account.get('name') or '').strip().casefold()
        matches = []
        for local in (True, False):
            for row in self.profiles(local=local):
                if str(row.get('name') or '').strip().casefold() == wanted and row.get('uuid'):
                    matches.append(str(row['uuid']))
        matches = list(dict.fromkeys(matches))
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise HidemiumError('Chưa liên kết profile Hidemium. Bấm “Hidemium UUID” ở tài khoản và dán UUID profile.')
        raise HidemiumError('Có nhiều profile Hidemium trùng tên; hãy liên kết đúng UUID trong Quản lý tài khoản.')

    def create_profile(self, name):
        result = self.request('/create-profile-custom', method='POST',
                              query={'is_local': 'true'},
                              body={'name': name, 'os': 'win', 'browser': 'chrome',
                                    'StartURL': 'https://www.dola.com/chat'}, timeout=90)
        def find_uuid(value):
            if isinstance(value, dict):
                if value.get('uuid'):
                    return str(value['uuid'])
                for key in ('content', 'data'):
                    found = find_uuid(value.get(key))
                    if found:
                        return found
            if isinstance(value, list) and len(value) == 1:
                return find_uuid(value[0])
            return ''
        profile_id = find_uuid(result)
        if not re.fullmatch(r'[A-Za-z0-9_-]{4,160}', profile_id):
            raise HidemiumError('Hidemium chưa trả UUID sau lệnh tạo. Kiểm tra profile vừa tạo trong Hidemium trước khi thử lại.')
        return profile_id


    def open_profile(self, profile_id, *, command='', proxy=''):
        query = {'uuid': profile_id}
        if command:
            query['command'] = command
        if proxy:
            query['proxy'] = proxy
        data = self.request('/openProfile', query=query, timeout=90)
        payload = data.get('data') if isinstance(data.get('data'), dict) else data
        port = payload.get('remote_port') or payload.get('remote_debugging_port') or payload.get('debug_port')
        endpoint = (payload.get('websocket_debugging_url') or payload.get('remote_debugging_address')
                    or payload.get('remote_debug_address'))
        if not endpoint and port:
            endpoint = f'http://127.0.0.1:{port}'
        if not isinstance(endpoint, str) or not endpoint:
            raise HidemiumError('Hidemium đã mở profile nhưng không trả cổng remote debugging.')
        if '://' not in endpoint:
            endpoint = 'http://' + endpoint
        parsed = urlsplit(endpoint)
        if parsed.scheme not in ('http', 'ws') or parsed.hostname not in ('localhost', '127.0.0.1', '::1'):
            raise HidemiumError('Hidemium trả địa chỉ debug không phải máy cục bộ.')
        return endpoint

    def close_profile(self, profile_id):
        return self.request('/closeProfile', query={'uuid': profile_id}, timeout=20)


def create_account_profile(settings, account, cancel, report):
    """Create once per account; retain a durable receipt even if login fails."""
    from pathlib import Path
    import hashlib
    from engine import atomic_json
    if account.get('hidemium_profile_id'):
        return account
    if cancel is not None and cancel.is_set():
        raise Cancelled('Đã dừng tạo profile Hidemium.')
    client = HidemiumClient(settings.get('hidemium_api'))
    key = hashlib.sha256((client.root + '|' + account['id']).encode()).hexdigest()
    receipt = Path(settings['lock_root']).parent / 'hidemium_links' / (key + '.json')
    saved = json.loads(receipt.read_text('utf-8')) if receipt.exists() else {}
    if saved.get('pending'):
        raise HidemiumError('Lệnh tạo profile trước chưa xác nhận UUID. Kiểm tra Hidemium; chưa tạo trùng.')
    if not saved.get('uuid'):
        name = 'Seedance - ' + account['id']
        atomic_json(receipt, {'pending': True, 'name': name})
        profile_id = client.create_profile(name)
        saved = {'uuid': profile_id, 'name': name}
        atomic_json(receipt, saved)
        if report:
            report('Đã tạo profile Hidemium mới: ' + profile_id)
    account.update(browser_backend='hidemium', hidemium_profile_id=saved['uuid'],
                   hidemium_owned=True)
    account.setdefault('name', saved['name'])
    return account


def proxy_string(account):
    proxy = browser_proxy(account)
    if not proxy:
        return ''
    parsed = urlsplit(proxy['server'])
    kind = {'http': 'HTTP', 'https': 'HTTP', 'socks5': 'SOCKS5', 'socks5h': 'SOCKS5',
            'socks4': 'SOCKS4'}.get(parsed.scheme)
    if not kind or not parsed.hostname or not parsed.port:
        raise HidemiumError('Proxy không thuộc loại Hidemium hỗ trợ (HTTP/SOCKS4/SOCKS5).')
    user, password = proxy.get('username', ''), proxy.get('password', '')
    if any('|' in str(value) for value in (parsed.hostname, user, password)):
        raise HidemiumError('Proxy Hidemium không nhận ký tự | trong host/user/mật khẩu.')
    return '|'.join((kind, parsed.hostname, str(parsed.port), str(user), str(password)))


class HidemiumContext:
    def __init__(self, context, browser, client, profile_id, startup_page=None):
        self._context, self._browser = context, browser
        self._client, self._profile_id = client, profile_id
        self._startup_page, self._closed = startup_page, False

    def __getattr__(self, name):
        return getattr(self._context, name)

    @property
    def pages(self):
        pages = list(self._context.pages)
        if self._startup_page in pages:
            pages.remove(self._startup_page)
            pages.insert(0, self._startup_page)
        return pages

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self._client.close_profile(self._profile_id)
            return
        except HidemiumError:
            pass
        try:
            self._browser.new_browser_cdp_session().send('Browser.close')
        except Exception:
            try:
                self._browser.close()
            except Exception:
                pass


def connect_hidemium(playwright, settings, account, *, video_session=False, cancel=None, report=None, hidden=False):
    if cancel is not None and cancel.is_set():
        raise Cancelled('Đã dừng kết nối Hidemium.')
    client = HidemiumClient(settings.get('hidemium_api', 'http://127.0.0.1:2222'))
    profile_id = client.resolve_profile(account)
    with_extension = bool(video_session and settings.get('load_extension', True))
    if with_extension:
        folder = extension_dir()
        args = [f'--disable-extensions-except={folder}', f'--load-extension={folder}']
    else:
        args = ['--disable-extensions']
    if hidden:
        args += ['--window-position=-32000,-32000', '--window-size=1100,760']
    command = subprocess.list2cmdline(args)
    endpoint = client.open_profile(profile_id, command=command, proxy=proxy_string(account))
    browser = None
    try:
        deadline, attempt = time.monotonic() + 45, 0
        while True:
            if cancel is not None and cancel.is_set():
                raise Cancelled('Đã dừng kết nối Hidemium.')
            try:
                browser = playwright.chromium.connect_over_cdp(endpoint, timeout=5000)
                if not browser.contexts:
                    raise HidemiumError('Hidemium chưa trả context Chromium.')
                break
            except Exception:
                if time.monotonic() >= deadline:
                    raise
                attempt += 1
                delay = min(3, .5 * 2 ** min(attempt - 1, 3))
                if report:
                    report(f'Đang chờ profile Hidemium sẵn sàng • lần {attempt}, nghỉ {delay:g}s…')
                if cancel is not None:
                    if cancel.wait(delay):
                        raise Cancelled('Đã dừng kết nối Hidemium.')
                else:
                    time.sleep(delay)
        context = browser.contexts[0]
        context.set_default_timeout(15000)
        context._seedance_proxy = dict(browser_proxy(account) or {})
        target = settings.get('open_url') or 'https://www.dola.com/chat'
        page = open_startup_page(context, target, cancel)
        wrapper = HidemiumContext(context, browser, client, profile_id, page)
        if hidden:
            move_window_offscreen(context, page)
        if with_extension:
            verify_loaded(context, cancel, report)
        if report:
            report('Đã mở Dola bằng profile Hidemium và kết nối điều khiển qua CDP.')
        return wrapper
    except Cancelled:
        try:
            client.close_profile(profile_id)
        except HidemiumError:
            pass
        raise
    except Exception as exc:
        try:
            client.close_profile(profile_id)
        except HidemiumError:
            pass
        if isinstance(exc, HidemiumError):
            raise
        raise HidemiumError('Không kết nối được profile Hidemium qua remote debugging.') from None


def manual_hidemium(settings, account, cancel, report, target):
    from playwright.sync_api import sync_playwright
    cfg = dict(settings, open_url=target)
    with sync_playwright() as playwright:
        context = connect_hidemium(playwright, cfg, account, video_session=False,
                                   cancel=cancel, report=report, hidden=False)
        if account.get('cookie_file'):
            context.add_cookies(load_cookies(account))
            report('Đã nạp cookie đã lưu vào profile Hidemium.')
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(target, wait_until='domcontentloaded', timeout=45000)
        report('Đã mở Dola trong Hidemium. Tự đăng nhập/thao tác rồi đóng cửa sổ khi xong.')
        try:
            while context.pages:
                if cancel.wait(.5):
                    context.close()
                    report('Đã đóng profile Hidemium.')
                    return False
                try:
                    context.pages[0].wait_for_timeout(100)
                except Exception:
                    break
        finally:
            context.close()
    return True
