"""ProxyVN (proxy.vn / proxyxoay.shop): proxy xoay — mỗi key một proxy, gọi get.php để lấy/đổi IP.

- GET https://proxyxoay.shop/api/get.php?key=KEY&nhamang=&tinhthanh= → status 100 + proxyhttp
  "host:port::" (KHÔNG user/pass; tự whitelist IP của máy gọi), message "proxy nay se die sau Ns".
  Mỗi lần gọi thành công là XOAY sang IP mới, và endpoint (host:port) ĐỔI theo.
- Gọi lại quá sớm → status 101 "Con Ns moi co the doi proxy" = cooldown (chưa tới lúc đổi,
  KHÔNG phải key die). Lúc này không trả proxy nên phải dùng lại proxy đã cache.
- Key hết hạn / không tồn tại → status lỗi với message khác (coi là key die).

Key luôn mã hóa trong settings, không ghi log."""
from __future__ import annotations
import json
import re
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from engine import Cancelled

API_URL = 'https://proxyxoay.shop/api/get.php'
NAME = 'ProxyVN'
ROTATE_SECONDS = 900      # chế độ theo lịch: mặc định 15 phút
RETRY_SECONDS = 120       # retry xoay tối đa 2 phút (theo yêu cầu cho ProxyVN)


class ProxyVNError(RuntimeError):
    pass


class ProxyVNCooldown(ProxyVNError):
    """Chưa đến lúc được đổi proxy (status 101 'Con Ns'); kèm số giây phải chờ."""
    def __init__(self, seconds, message=''):
        super().__init__(message or f'Còn {seconds}s mới đổi được proxy')
        self.seconds = int(seconds)


def mask(text, key):
    text = str(text or '')
    return (text.replace(key, '***') if key else text)[:300]


def _parse_endpoint(value):
    # "160.250.166.11:10287::" -> (host, port), bỏ hai dấu ':' cuối (user/pass rỗng).
    parts = str(value or '').split(':')
    if len(parts) < 2 or not parts[0] or not parts[1].isdigit():
        raise ProxyVNError(f'{NAME} trả proxy không đúng dạng host:port.')
    return parts[0], int(parts[1])


def _cooldown_seconds(message):
    """Số giây phải chờ nếu message là cooldown đổi proxy, else None (tức key die/lỗi khác)."""
    m = str(message or '').lower()
    if 'doi proxy' in m or 'đổi proxy' in m or 'moi co the' in m:
        match = re.search(r'(\d+)\s*s', m)
        return max(1, int(match.group(1))) if match else 60
    return None


def get_proxy(key, *, nhamang='random', tinhthanh='0', opener=None, timeout=30):
    """(proxy, info). proxy = {'server': 'http://host:port'} (ProxyVN không user/pass).
    Mỗi lần gọi thành công là XOAY sang IP mới (có cooldown ~60s). Cooldown → ProxyVNCooldown."""
    url = f'{API_URL}?' + urlencode({'key': key, 'nhamang': nhamang or 'random', 'tinhthanh': tinhthanh or '0'})
    req = Request(url, headers={'User-Agent': 'StudioPython/1.0'})
    try:
        with (opener or urlopen)(req, timeout=timeout) as response:
            payload = json.loads(response.read(200000).decode('utf-8', 'replace'))
    except HTTPError as exc:
        raise ProxyVNError(f'{NAME} trả HTTP {exc.code}.') from None
    except (URLError, OSError, ValueError):
        raise ProxyVNError(f'Không kết nối được {NAME}.') from None
    if not isinstance(payload, dict):
        raise ProxyVNError(f'{NAME} trả dữ liệu không hợp lệ.')
    status = payload.get('status')
    message = str(payload.get('message') or '')
    if status == 100:
        host, port = _parse_endpoint(payload.get('proxyhttp'))
        proxy = {'server': f'http://{host}:{port}'}
        info = {'expired_at': payload.get('Token expiration date'), 'nha_mang': payload.get('Nha Mang'),
                'vi_tri': payload.get('Vi Tri'), 'ip': payload.get('ip'), 'message': message}
        return proxy, info
    wait = _cooldown_seconds(message)
    if wait is not None:
        raise ProxyVNCooldown(wait, mask(message, key))
    raise ProxyVNError(f'{NAME}: {mask(message or "key không hợp lệ", key)}')


def endpoint_text(proxy):
    server = str((proxy or {}).get('server') or '')
    return server.split('://', 1)[-1] or 'proxy'
