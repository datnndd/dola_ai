"""ProxyNo1 (app.proxyno1.com): one fixed proxy endpoint per API key, IP rotated on request.

- key-status → proxy host:port + authentication (user:pass) of the key.
- change-key-ip → the provider swaps the outgoing IP; the endpoint stays the same.
- One background thread per key changes the IP every 15 minutes. The app turns it on for every
  key in Settings as soon as it starts (run_rotation); running tasks are never paused. If a change
  fails, key-status is read again and every Chrome bridge is re-pointed at the proxy it returns;
  "Đợi N giây" from the provider is retried after N seconds, other failures after 2 minutes.
API keys stay encrypted in settings and are never written to logs."""
from __future__ import annotations
import json
import re
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from cookie_accounts import browser_proxy, parse_proxy
from engine import Cancelled

API_BASE = 'https://app.proxyno1.com/api'
ROTATE_SECONDS = 900
RETRY_SECONDS = 300   # đổi IP lỗi không kèm số giây chờ (mất mạng…): thử lại trong 5 phút
NAME = 'ProxyNo1'


class ProxyNo1Error(RuntimeError):
    pass


def mask(text, key):
    text = str(text or '')
    return (text.replace(key, '***') if key else text)[:300]


def _call(path, key, opener=None, timeout=20):
    request = Request(f'{API_BASE}/{path}/{quote(key, safe="")}',
                      headers={'Accept': 'application/json', 'User-Agent': 'StudioPython/1.0'})
    try:
        with (opener or urlopen)(request, timeout=timeout) as response:
            payload = json.loads(response.read(200000).decode('utf-8', 'replace'))
    except HTTPError as exc:
        try:
            payload = json.loads(exc.read(20000).decode('utf-8', 'replace'))
        except Exception:
            raise ProxyNo1Error(f'{NAME} trả HTTP {exc.code}.') from None
    except (URLError, OSError, ValueError):
        raise ProxyNo1Error(f'Không kết nối được {NAME}.') from None
    if not isinstance(payload, dict):
        raise ProxyNo1Error(f'{NAME} trả dữ liệu không hợp lệ.')
    return payload


def proxy_from_status(data):
    """Browser proxy {server, username?, password?} from key-status data.
    Accepts the documented shape (data.http = "host:port") and the live one
    (data.proxy.ip + data.proxy.HTTP_IPv4); credentials come from data.authentication."""
    if not isinstance(data, dict):
        raise ProxyNo1Error(f'{NAME} không trả thông tin key.')
    endpoint = str(data.get('http') or '').strip()
    if not endpoint:
        proxy = data.get('proxy') if isinstance(data.get('proxy'), dict) else {}
        host, port = str(proxy.get('ip') or '').strip(), proxy.get('HTTP_IPv4')
        if host and isinstance(port, int) and 0 < port < 65536:
            endpoint = f'{host}:{port}'
    if not endpoint:
        raise ProxyNo1Error(f'{NAME} chưa trả địa chỉ proxy HTTP cho key này.')
    user, _, password = str(data.get('authentication') or '').partition(':')
    try:
        stored = parse_proxy(endpoint + (f':{user}:{password}' if user else ''))
    except ValueError:
        raise ProxyNo1Error(f'{NAME} trả địa chỉ proxy không đúng dạng host:port.') from None
    return browser_proxy({'proxy': stored})


def key_status(key, opener=None):
    """(proxy, info) for a key; info has plan / expired_at / description for display."""
    payload = _call('key-status', key, opener)
    if payload.get('status') != 0:
        raise ProxyNo1Error(f"{NAME}: {mask(payload.get('message') or 'key không hợp lệ', key)}")
    data = payload.get('data') or {}
    info = {k: data.get(k) for k in ('plan', 'expired_at', 'description', 'type')}
    return proxy_from_status(data), info


def change_ip(key, opener=None):
    """(ok, message). status 5 = must wait (message says how many seconds)."""
    try:
        payload = _call('change-key-ip', key, opener)
    except ProxyNo1Error as exc:
        return False, str(exc)
    return payload.get('status') == 0, mask(payload.get('message') or '', key)


def wait_seconds(message, default=10):
    """Seconds named in a provider message ("Đợi 224 giây…"), else `default` (may be None)."""
    match = re.search(r'(\d+)\s*(?:giây|giay|seconds?|secs?|s)\b', str(message or ''), re.I)
    return max(1, int(match.group(1))) if match else default


class KeyRotator:
    """Shared by every Chrome using one key: holds the key's proxy and rotates its IP."""

    def __init__(self, key, *, status=None, change=None, interval=ROTATE_SECONDS,
                 clock=time.monotonic, tick=5.0):
        self.key = key
        self.status = status or key_status
        self.change = change or change_ip
        self.interval, self.clock, self.tick = interval, clock, tick
        self.lock = threading.RLock()
        self.fetch_lock = threading.Lock()
        self.bridges = {}            # bridge -> report
        self.proxy = None
        self.last_change = None
        self.retry_at = None         # lần thử lại sớm sau một lần đổi IP lỗi
        self.always = False          # True: đổi IP theo lịch kể cả khi chưa có Chrome nào dùng key
        self.sink = None             # nhật ký chung của app (khi chạy theo lịch)
        self.last_ok = None          # kết quả lần đổi gần nhất: True / False / None (chưa đổi)
        self.last_text = ''
        self.last_wall = None        # giờ máy của lần đổi gần nhất
        self.thread = None

    def current(self, cancel=None, report=None, attempts=3, pause=2.0):
        """The key's proxy (key-status once, then cached; refreshed when a change fails)."""
        with self.fetch_lock:
            with self.lock:
                if self.proxy:
                    return self.proxy
            error = None
            for attempt in range(attempts):
                if cancel is not None and cancel.is_set():
                    raise Cancelled('Đã dừng lấy proxy.')
                try:
                    proxy, _info = self.status(self.key)
                    with self.lock:
                        self.proxy = proxy
                    return proxy
                except ProxyNo1Error as exc:
                    error = exc
                    if attempt + 1 == attempts:
                        break
                    if report:
                        report(f'{mask(exc, self.key)} Thử lại sau {pause:g} giây.')
                    if cancel is not None:
                        if cancel.wait(pause):
                            raise Cancelled('Đã dừng lấy proxy.')
                    else:
                        time.sleep(pause)
            raise ProxyNo1Error(mask(error, self.key))

    def _ensure_thread(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self._loop, daemon=True, name=f'{NAME}-rotate')
            self.thread.start()

    def enable(self, sink=None):
        """Rotate on schedule from now on, whether or not a Chrome is using the key."""
        with self.lock:
            if not self.always and not self.bridges:
                self.last_change = self.clock()   # lần đổi đầu tiên sau 15 phút
            self.always = True
            self.sink = sink
            self._ensure_thread()

    def disable(self):
        with self.lock:
            self.always = False
            self.sink = None
            self.retry_at = None

    def attach(self, bridge, report):
        with self.lock:
            idle = not self.bridges and not self.always
            self.bridges[bridge] = report or (lambda text: None)
            if idle or self.last_change is None:
                # Chrome vừa mở sau lúc rảnh: tính 15 phút từ bây giờ.
                self.last_change = self.clock()
            self._ensure_thread()

    def detach(self, bridge):
        with self.lock:
            self.bridges.pop(bridge, None)

    def _say(self, text):
        with self.lock:
            target = self.sink or next(iter(self.bridges.values()), None)
        if target:
            target(text)  # một thông báo cho cả key, không lặp theo từng Chrome

    def active(self):
        with self.lock:
            return self.always or bool(self.bridges)

    def next_in(self):
        """Seconds until the next change attempt, or None when the key is not in use."""
        with self.lock:
            if not (self.always or self.bridges) or self.last_change is None:
                return None
            at = self.retry_at if self.retry_at is not None else self.last_change + self.interval
            return max(0.0, at - self.clock())

    def due(self):
        remaining = self.next_in()
        return remaining is not None and remaining <= 0

    def _loop(self):
        while True:
            time.sleep(self.tick)
            try:
                if self.due():
                    self.rotate()
            except Exception:
                pass  # luồng nền không được chết vì một lần lỗi

    def _record(self, ok, text):
        with self.lock:
            self.last_ok, self.last_text, self.last_wall = ok, text, time.time()

    def rotate(self):
        """Change the IP once; on failure re-read the key, re-point every bridge and retry soon."""
        with self.lock:
            self.last_change = self.clock()
            self.retry_at = None
        ok, message = self.change(self.key)
        if ok:
            self._record(True, message or 'Đổi IP thành công')
            every = f'{self.interval // 60:g} phút' if self.interval >= 60 else f'{self.interval:g} giây'
            self._say(f'{NAME}: đã đổi IP ({message or "thành công"}). Lần tiếp theo sau {every}; task đang chạy không bị dừng.')
            return True
        wait = wait_seconds(message, default=None)
        delay = min(self.interval, (wait + 3) if wait else RETRY_SECONDS)
        with self.lock:
            self.retry_at = self.clock() + delay
        self._record(False, message or 'không rõ lỗi')
        self._say(f'{NAME}: chưa đổi được IP ({message or "không rõ lỗi"}); thử lại sau {int(delay)} giây. '
                  'Lấy lại thông tin key để gắn proxy.')
        try:
            proxy, _info = self.status(self.key)
        except ProxyNo1Error as exc:
            self._say(f'{NAME}: không lấy được thông tin key ({mask(exc, self.key)}); giữ proxy hiện tại.')
            return False
        with self.lock:
            self.proxy = proxy
            bridges = list(self.bridges)
        for bridge in bridges:
            try:
                bridge.update(proxy)
            except Exception:
                pass
        self._say(f'{NAME}: đã gắn lại proxy {endpoint_text(proxy)} cho {len(bridges)} Chrome.')
        return False


def endpoint_text(proxy):
    server = str((proxy or {}).get('server') or '')
    return server.split('://', 1)[-1] or 'proxy'


_ROTATORS = {}
_ROTATORS_LOCK = threading.Lock()


def rotator_for(key):
    with _ROTATORS_LOCK:
        rotator = _ROTATORS.get(key)
        if rotator is None:
            rotator = _ROTATORS[key] = KeyRotator(key)
        return rotator


def run_rotation(keys, sink=None, *, rotators=None, interval=None):
    """Background IP rotation for exactly these keys (app start / settings saved).
    `interval` (giây) đặt chu kỳ xoay; None giữ mặc định 15 phút."""
    rotators = rotators or rotator_for
    wanted = list(dict.fromkeys(keys))
    with _ROTATORS_LOCK:
        others = [r for key, r in _ROTATORS.items() if key not in wanted]
    for rotator in others:
        rotator.disable()
    for key in wanted:
        rotator = rotators(key)
        if interval:
            with rotator.lock:
                rotator.interval = max(60, int(interval))
        rotator.enable(sink)


def stop_rotation():
    with _ROTATORS_LOCK:
        rotators = list(_ROTATORS.values())
    for rotator in rotators:
        rotator.disable()


def rotation_status(keys, *, rotators=None):
    """[{index, next_in, last_ok, last_text, last_wall, chrome}] for display; never contains the key."""
    rotators = rotators or rotator_for
    rows = []
    for index, key in enumerate(keys, 1):
        rotator = rotators(key)
        with rotator.lock:
            rows.append({'index': index, 'next_in': rotator.next_in(), 'last_ok': rotator.last_ok,
                         'last_text': mask(rotator.last_text, key), 'last_wall': rotator.last_wall,
                         'chrome': len(rotator.bridges)})
    return rows


class ProxyNo1Lease:
    """One Chrome's hold on a key: attaches its bridge to the key's rotator while open."""

    def __init__(self, bridge, rotator, report=None):
        self.bridge, self.rotator, self.report = bridge, rotator, report
        self.lock = threading.Lock()
        self.started = self.closed = False

    def start(self):
        """The rotation clock counts from when Chrome has actually opened."""
        with self.lock:
            if self.closed or self.started:
                return
            self.started = True
        self.rotator.attach(self.bridge, self.report)

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
        self.rotator.detach(self.bridge)
        self.bridge.close()
