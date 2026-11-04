"""Proxy cho Chrome: proxy mặc định ProxyNo1 (đổi IP 15 phút/lần), proxy tay hoặc tắt.

Chrome luôn trỏ vào một proxy cục bộ (RotatingBridge); bridge chèn user/pass và chuyển
kết nối mới sang upstream mới nhất, nên đổi proxy không cần mở lại Chrome.
Giá trị cũ 'proxyvn' trong dữ liệu đã lưu được hiểu là ProxyNo1. Key API luôn được mã hóa,
không ghi vào nhật ký."""
from __future__ import annotations
import base64
import re
import select
import socket
import socketserver
import ssl
import threading
import zlib
from urllib.parse import urlsplit

from cookie_accounts import parse_proxy_lines, proxy_input_value, protect
from proxyno1 import NAME as PROVIDER, ProxyNo1Lease, mask, rotator_for

PROXY_MODE_LABELS = (
    ('Dùng proxy mặc định', 'default'),
    ('ProxyNo1', 'proxyno1'),
    ('Nhập proxy tay', 'manual'),
    ('Tắt proxy', 'off'),
)
LEGACY_PROVIDER = 'proxyvn'  # nhà cung cấp cũ: dữ liệu đã lưu tự chuyển sang ProxyNo1


def account_proxy_mode(account):
    mode = (account or {}).get('proxy_mode')
    if mode == LEGACY_PROVIDER:
        return 'proxyno1'
    if mode in ('default', 'proxyno1', 'manual', 'off'):
        return mode
    if (account or {}).get('proxy'):
        return 'manual'
    return 'default'


def proxy_mode_text(account):
    return {'default': 'Mặc định', 'proxyno1': PROVIDER, 'manual': 'Proxy tay', 'off': 'Tắt proxy'}[account_proxy_mode(account)]


def label_for_mode(mode):
    mode = 'proxyno1' if mode == LEGACY_PROVIDER else mode
    for label, value in PROXY_MODE_LABELS:
        if value == mode:
            return label
    return PROXY_MODE_LABELS[0][0]


def proxy_source(settings):
    """'proxyno1' | 'pvn' (ProxyVN) | 'manual'. Giá trị cũ 'proxyvn' = ProxyNo1 (legacy)."""
    src = (settings or {}).get('proxy_source')
    if src == 'manual':
        return 'manual'
    if src == 'pvn':
        return 'pvn'
    return 'proxyno1'


def split_keys(text):
    keys, seen = [], set()
    for line in str(text or '').splitlines():
        key = line.strip()
        if not key or key in seen:
            continue
        if not re.fullmatch(r'[A-Za-z0-9_-]{4,128}', key):
            raise ValueError(f'Mỗi dòng {PROVIDER} là một API key (chữ, số, dấu gạch).')
        seen.add(key)
        keys.append(key)
    if len(keys) > 100:
        raise ValueError(f'Tối đa 100 API key {PROVIDER}.')
    return keys


def keys_text(settings):
    blob = (settings or {}).get('proxyno1_keys_protected') or ''
    if not blob:
        return ''
    return protect(blob, decrypt=True)


def load_keys(settings):
    return split_keys(keys_text(settings))


def select_key(keys, account_id):
    """Each profile keeps the same key (stable across runs)."""
    if not keys:
        raise RuntimeError(f'Chưa có API key {PROVIDER}. Vào Cài đặt, mỗi dòng một key, hoặc chọn Tắt proxy / Nhập tay.')
    return keys[zlib.crc32(str(account_id).encode('utf-8')) % len(keys)]


def normalize_proxy_settings(source, keys_text_value, manual_proxy, *, require_keys=True):
    source = 'proxyno1' if source == LEGACY_PROVIDER else source
    if source not in ('proxyno1', 'manual'):
        raise ValueError('Nguồn proxy không hợp lệ.')
    keys = split_keys(keys_text_value)
    manual = parse_proxy_lines(manual_proxy)
    if require_keys and source == 'proxyno1' and not keys:
        raise ValueError(f'Proxy mặc định là {PROVIDER}. Nhập ít nhất một API key, mỗi dòng một key.')
    return {'proxy_source': source, 'keys': keys,
            'default_proxies': manual, 'default_proxy': manual[0] if manual else {}}


def manual_proxies(settings):
    if 'default_proxies' in settings:
        return list(settings.get('default_proxies') or [])
    old = settings.get('default_proxy')
    return [old] if old else []


def manual_proxy_text(settings):
    return '\n'.join(proxy_input_value(proxy) for proxy in manual_proxies(settings))


def assign_manual_proxies(settings, accounts):
    """Persist profile positions so tab/task order cannot change a Chrome's proxy."""
    slots = dict(settings.get('manual_proxy_slots') or {})
    next_slot = max(slots.values(), default=-1) + 1
    for account in accounts:
        if account_proxy_mode(account) == 'default' and account['id'] not in slots:
            slots[account['id']] = next_slot
            next_slot += 1
    return slots


def endpoint(proxy):
    parsed = urlsplit((proxy or {}).get('server') or '')
    if not parsed.hostname or not parsed.port:
        return 'proxy'
    return f'{parsed.hostname}:{parsed.port}'


def _headers(stream):
    lines, size = [], 0
    while True:
        line = stream.readline(65537)
        size += len(line)
        if not line or size > 65536:
            raise OSError('Incomplete or oversized proxy headers')
        lines.append(line)
        if line == b'\r\n':
            return lines


class _Upstream:
    def __init__(self, proxy):
        parsed = urlsplit(proxy['server'])
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or not parsed.port:
            raise ValueError('Proxy mặc định cần proxy HTTP.')
        user, password = proxy.get('username') or '', proxy.get('password') or ''
        self.address = (parsed.hostname, parsed.port)
        self.tls = parsed.scheme == 'https'
        self.authorization = base64.b64encode(f'{user}:{password}'.encode('utf-8')) if user or password else None


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = False
    block_on_close = False

    def handle_error(self, request, client_address):
        pass


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        upstream_sock = None
        response_started = False
        bridge = self.server.bridge
        bridge.track(self.connection)
        try:
            self.connection.settimeout(20)
            request = _headers(self.rfile)
            method, target, _version = request[0].decode('ascii').strip().split(' ', 2)
            if method != 'CONNECT' and urlsplit(target).scheme != 'http':
                raise OSError('Expected HTTP proxy request')
            upstream = bridge.snapshot()
            upstream_sock = socket.create_connection(upstream.address, timeout=20)
            if upstream.tls:
                upstream_sock = ssl.create_default_context().wrap_socket(
                    upstream_sock, server_hostname=upstream.address[0])
            bridge.track(upstream_sock)
            headers = [request[0]]
            for line in request[1:-1]:
                key = line.split(b':', 1)[0].lower()
                if key not in (b'proxy-authorization', b'proxy-connection', b'connection'):
                    headers.append(line)
            if upstream.authorization:
                headers.append(b'Proxy-Authorization: Basic ' + upstream.authorization + b'\r\n')
            if method != 'CONNECT':
                headers.extend([b'Connection: close\r\n', b'Proxy-Connection: close\r\n'])
            upstream_sock.sendall(b''.join(headers) + b'\r\n')
            if method == 'CONNECT':
                with upstream_sock.makefile('rb', buffering=0) as source:
                    reply = _headers(source)
                status = int(reply[0].split()[1])
                if status != 200:
                    bridge.failure(f'{PROVIDER} từ chối user/pass proxy (HTTP 407).' if status == 407
                                   else f'{PROVIDER} trả HTTP {status}; chưa mở được kết nối.')
                    raise OSError('Upstream rejected tunnel')
                self.connection.sendall(b'HTTP/1.1 200 Connection Established\r\n\r\n')
                response_started = True
            else:
                fields = {line.split(b':', 1)[0].lower(): line.split(b':', 1)[1].strip()
                          for line in request[1:-1] if b':' in line}
                if b'transfer-encoding' in fields:
                    raise OSError('Chunked plain HTTP requests are unsupported')
                remaining = int(fields.get(b'content-length', b'0'))
                while remaining:
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        raise OSError('Incomplete request body')
                    upstream_sock.sendall(chunk)
                    remaining -= len(chunk)
                with upstream_sock.makefile('rb', buffering=0) as source:
                    reply = _headers(source)
                self.connection.sendall(b''.join(reply))
                response_started = True
            self.connection.settimeout(None)
            upstream_sock.settimeout(None)
            sockets = [self.connection, upstream_sock]
            while not bridge.stopped.is_set():
                pending = isinstance(upstream_sock, ssl.SSLSocket) and upstream_sock.pending()
                readable = [upstream_sock] if pending else select.select(sockets, [], [], 1)[0]
                for source in readable:
                    data = source.recv(65536)
                    if not data:
                        return
                    (upstream_sock if source is self.connection else self.connection).sendall(data)
        except (OSError, ValueError):
            if not response_started:
                try:
                    self.connection.sendall(b'HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
                except OSError:
                    pass
        finally:
            for sock in (upstream_sock, self.connection):
                if sock is not None:
                    bridge.untrack(sock)
                    sock.close()


class RotatingBridge:
    """Local proxy Chrome keeps using. New connections follow the latest upstream."""

    def __init__(self, proxy, report=None):
        self.report = report or (lambda text: None)
        self.reported = set()
        self.stopped = threading.Event()
        self.lock = threading.Lock()
        self.sockets = set()
        self.upstream = _Upstream(proxy)
        self.server = _Server(('127.0.0.1', 0), _Handler)
        self.server.bridge = self
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}'
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.1}, daemon=True)

    def snapshot(self):
        with self.lock:
            return self.upstream

    def update(self, proxy):
        upstream = _Upstream(proxy)
        with self.lock:
            self.upstream = upstream

    def failure(self, message):
        with self.lock:
            if message in self.reported:
                return
            self.reported.add(message)
        self.report(message)

    def track(self, sock):
        with self.lock:
            if self.stopped.is_set():
                sock.close()
                raise OSError('Proxy stopped')
            self.sockets.add(sock)

    def untrack(self, sock):
        with self.lock:
            self.sockets.discard(sock)

    def start(self):
        self.thread.start()

    def close(self):
        with self.lock:
            if self.stopped.is_set():
                return
            self.stopped.set()
            sockets = list(self.sockets)
        # Flag, not shutdown(): shutdown waits forever if the accept loop has not started.
        self.server._BaseServer__shutdown_request = True
        if self.thread.ident is not None:
            self.thread.join(timeout=2)
        self.server.server_close()
        for sock in sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()


class BoundContext:
    def __init__(self, context, lease):
        self._context = context
        self._lease = lease
        self._lock = threading.Lock()
        self._closed = False
        self._lease.start()

    def close(self):
        with self._lock:
            first = not self._closed
            self._closed = True
        if not first:
            return None
        try:
            return self._context.close()
        finally:
            self._lease.close()

    def __getattr__(self, name):
        return getattr(self._context, name)


def open_runtime_proxy(settings, account, cancel=None, report=None, *, rotators=None):
    """Return (account copy, lease). The saved account proxy is left unchanged."""
    report = report or (lambda text: None)
    mode = account_proxy_mode(account)
    if mode == 'off':
        runtime = dict(account)
        runtime['proxy'] = {}
        return runtime, None
    if mode == 'manual':
        return account, None
    if mode == 'default' and proxy_source(settings) != 'proxyno1':
        runtime = dict(account)
        proxies = manual_proxies(settings or {})
        if not proxies:
            runtime['proxy'] = {}
            return runtime, None
        position = (settings or {}).get('manual_proxy_slots', {}).get(account.get('id'))
        if position is None:
            position = zlib.crc32(str(account.get('id', '')).encode('utf-8'))
        index = position % len(proxies)
        runtime['proxy'] = dict(proxies[index])
        report(f'Proxy tay {index + 1}/{len(proxies)} • {endpoint(runtime["proxy"])} • dùng chung cho các tab của profile.')
        return runtime, None
    keys = load_keys(settings)
    key = select_key(keys, (account or {}).get('id', ''))
    rotator = (rotators or rotator_for)(key)
    try:
        proxy = rotator.current(cancel, report)
    except RuntimeError as exc:
        raise RuntimeError(mask(exc, key)) from None
    bridge = RotatingBridge(proxy, report)
    try:
        bridge.start()
        lease = ProxyNo1Lease(bridge, rotator, report)
        runtime = dict(account)
        runtime['proxy'] = {'server': bridge.url}
        report(f'{PROVIDER} key {keys.index(key) + 1}/{len(keys)} • {endpoint(proxy)} • đổi IP 15 phút/lần (luồng riêng).')
        return runtime, lease
    except Exception:
        bridge.close()
        raise


def gpm_proxy_account(settings, account, cancel=None, report=None, *, rotators=None, unique=True):
    """Bản copy account với proxy upstream thật, để gắn thẳng vào profile GPM.

    unique=True (lúc CHẠY): mỗi profile giữ MỘT key RIÊNG (lease); số luồng = số key sống.
    unique=False (đăng nhập / mở tay / check): chỉ cần một proxy để mở profile; chia sẻ key
    theo vòng (không giới hạn theo số key) để đăng nhập được nhiều tài khoản hơn số key."""
    report = report or (lambda text: None)
    mode = account_proxy_mode(account)
    if mode == 'off':
        return dict(account, proxy={})
    if mode == 'manual':
        return account
    # Nguồn "Proxy nhập tay": dùng danh sách proxy tự nhập, không đụng key xoay.
    if proxy_source(settings) == 'manual':
        runtime = dict(account)
        proxies = manual_proxies(settings or {})
        if not proxies:
            runtime['proxy'] = {}
            return runtime
        position = (settings or {}).get('manual_proxy_slots', {}).get(account.get('id'))
        if position is None:
            position = zlib.crc32(str(account.get('id', '')).encode('utf-8'))
        runtime['proxy'] = dict(proxies[position % len(proxies)])
        report(f'Proxy tay {position % len(proxies) + 1}/{len(proxies)} • {endpoint(runtime["proxy"])} • gắn vào GPM.')
        return runtime
    # Nguồn "ProxyNo1" hoặc "ProxyVN": pool key của ĐÚNG nguồn; mỗi profile GPM 1 key RIÊNG.
    import proxy_pool
    tagged = proxy_pool.source_keys(settings)
    if not tagged:
        name = proxy_pool.NAME_VN if proxy_source(settings) == 'pvn' else proxy_pool.NAME_NO1
        raise RuntimeError(f'Chưa có key {name}. Điền key {name} trong Cài đặt → Proxy '
                           '(hoặc chọn "Proxy nhập tay"). Dừng tài khoản này.')
    force_new = str(settings.get('proxy_rotate_mode', 'auto')) == 'auto'
    if not unique:
        # Đăng nhập / mở tay: chia sẻ key theo vòng, không giữ chỗ (không giới hạn theo số key).
        start = zlib.crc32(str((account or {}).get('id', '')).encode('utf-8')) % len(tagged)
        last = None
        for offset in range(len(tagged)):
            provider, key = tagged[(start + offset) % len(tagged)]
            try:
                proxy = proxy_pool.resolve(provider, key, cancel=cancel, report=report, force_new=force_new)
            except proxy_pool.KeyDead as exc:
                last = exc
                continue
            report(f'{proxy_pool.PROVIDER_NAME[provider]} • {endpoint(proxy)} • gắn vào GPM.')
            return dict(account, proxy=dict(proxy))
        raise RuntimeError(f'Tất cả key proxy đều lỗi ({last}). Thay key còn hạn. Dừng tài khoản này.')
    pool = proxy_pool.pool()
    pool.configure(tagged)
    item = pool.lease((account or {}).get('id', ''))
    if item is None:
        raise RuntimeError('Hết key proxy còn sống để gán cho tài khoản này; '
                           'giảm số luồng hoặc thêm key. Dừng tài khoản này.')
    provider, key = item
    # Chế độ Tự động: mỗi lần mở profile thì xoay IP 1 lần. Theo thời gian: giữ IP hiện tại.
    try:
        proxy = proxy_pool.resolve(provider, key, cancel=cancel, report=report, force_new=force_new)
    except proxy_pool.KeyDead as exc:
        pool.mark_dead(item)
        raise RuntimeError(f'Key proxy lỗi ({exc}). Bỏ key này (giảm 1 luồng); dừng tài khoản này.') from None
    report(f'{proxy_pool.PROVIDER_NAME[provider]} • {endpoint(proxy)} • gắn vào GPM'
           + (' (xoay khi mở profile)' if force_new else ' (xoay theo thời gian)') + '.')
    return dict(account, proxy=dict(proxy))
