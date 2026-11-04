"""Pool key proxy gộp ProxyNo1 + ProxyVN cho chế độ GPM.

- 2 nguồn key riêng: proxyno1_keys_protected / proxyvn_keys_protected (đều mã hóa).
- Mỗi profile GPM lấy MỘT key RIÊNG (lease); số luồng chạy = số key còn sống.
- resolve(): lấy proxy upstream của 1 key. ProxyNo1 có user/pass, endpoint cố định;
  ProxyVN không user/pass, gọi get.php để xoay (có cooldown ~60s) nên cache lại proxy.
- Key die (hết hạn / không tồn tại) -> KeyDead -> bỏ key, giảm 1 luồng.
- Retry khi chưa xoay được: ProxyVN tối đa 2 phút, ProxyNo1 tối đa 5 phút.
Key không bao giờ ghi vào log."""
from __future__ import annotations
import threading
import time

import proxyno1
import proxyvn_api as proxyvn
from cookie_accounts import protect
from engine import Cancelled

NO1 = 'no1'
VN = 'vn'
NAME_NO1 = proxyno1.NAME
NAME_VN = proxyvn.NAME
PROVIDER_NAME = {NO1: NAME_NO1, VN: NAME_VN}

# Retry xoay/resolve theo nhà cung cấp (giây).
RETRY_WINDOW = {NO1: 300, VN: 120}


class KeyDead(RuntimeError):
    """Key proxy hỏng/hết hạn/không tồn tại — bỏ key này, không dùng nữa."""


def _decode_keys(blob):
    """Danh sách key đã giải mã (mỗi dòng 1 key), loại trùng; lỗi giải mã -> rỗng."""
    if not blob:
        return []
    try:
        text = protect(blob, decrypt=True)
    except Exception:
        return []
    out, seen = [], set()
    for line in str(text).splitlines():
        key = line.strip()
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return out[:200]


def source_keys(settings):
    """[(provider, key)] của ĐÚNG nguồn đang chọn (ProxyNo1 hoặc ProxyVN)."""
    src = str((settings or {}).get('proxy_source') or 'proxyno1')
    if src == 'pvn':
        return [(VN, k) for k in _decode_keys((settings or {}).get('proxyvn_keys_protected'))]
    return [(NO1, k) for k in _decode_keys((settings or {}).get('proxyno1_keys_protected'))]


def load_tagged_keys(settings):
    """[(provider, key)] gộp cả 2 nguồn (dùng khi cần đếm chung); loại trùng, tối đa 200."""
    out = [(NO1, k) for k in _decode_keys((settings or {}).get('proxyno1_keys_protected'))]
    out += [(VN, k) for k in _decode_keys((settings or {}).get('proxyvn_keys_protected'))]
    return out[:200]


# ---- ProxyVN: cache proxy theo key (vì lúc cooldown get.php không trả proxy) ----
_vn_cache = {}
_vn_lock = threading.Lock()


def _resolve_no1(key):
    try:
        proxy, _info = proxyno1.key_status(key)
        return proxy
    except proxyno1.ProxyNo1Error as exc:
        # exc đã kèm tên nhà cung cấp, chỉ cần mask key.
        raise KeyDead(proxyno1.mask(exc, key)) from None


def _resolve_vn(key, *, cancel=None, report=None, window=None, force_new=False):
    """Lấy proxy ProxyVN.
    - force_new=False (Theo thời gian — giữ IP hiện tại): có cache thì trả cache, KHÔNG xoay.
    - force_new=True (Tự động — xoay khi mở profile): PHẢI đổi IP THÀNH CÔNG mới chạy. Nếu
      đang cooldown thì CHỜ và thử lại, tối đa `window` giây (mặc định 2 phút); quá đó thì BỎ
      (KeyDead), KHÔNG chạy với IP cũ. Áp dụng cả lúc đăng nhập.
    - Lỗi khác cooldown (key hết hạn/không tồn tại) -> KeyDead."""
    window = RETRY_WINDOW[VN] if window is None else window
    with _vn_lock:
        cached = _vn_cache.get(key)
    if cached is not None and not force_new:
        return cached
    deadline = time.monotonic() + window
    while True:
        try:
            proxy, _info = proxyvn.get_proxy(key)
            with _vn_lock:
                _vn_cache[key] = proxy
            return proxy
        except proxyvn.ProxyVNCooldown as cd:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise KeyDead(f'{proxyvn.NAME}: không đổi được IP trong {int(window)}s (cooldown); bỏ.') from None
            wait = min(cd.seconds + 2, max(1, remaining))
            if report:
                report(f'{proxyvn.NAME}: {cd}; chờ {int(wait)}s để đổi IP (còn {int(remaining)}s).')
            if cancel is not None:
                if cancel.wait(wait):
                    raise Cancelled('Đã dừng lấy proxy ProxyVN.')
            else:
                time.sleep(wait)
        except proxyvn.ProxyVNError as exc:
            raise KeyDead(proxyvn.mask(exc, key)) from None


def resolve(provider, key, *, cancel=None, report=None, force_new=False):
    """Trả proxy upstream {server, username?, password?} cho 1 key. Key die -> KeyDead.

    force_new (chế độ Tự động): cố đổi IP khi mở profile. Đổi KHÔNG thành công (cooldown/mạng)
    thì VẪN lấy IP hiện tại để chạy — chỉ dừng khi key thật sự die."""
    if provider == NO1:
        if force_new:
            try:
                ok, message = proxyno1.change_ip(key)
                if report:
                    report(f'{proxyno1.NAME}: {"đã đổi IP" if ok else "chưa đổi được IP, dùng IP hiện tại"} '
                           f'({proxyno1.mask(message, key)})')
            except Exception:
                pass  # đổi IP lỗi -> vẫn dùng IP hiện tại
        return _resolve_no1(key)
    return _resolve_vn(key, cancel=cancel, report=report, force_new=force_new)


def rotate(provider, key, *, cancel=None, report=None):
    """Xoay IP 1 lần. ProxyNo1: change-key-ip (endpoint giữ nguyên) rồi trả proxy.
    ProxyVN: get.php lấy endpoint MỚI. Trả proxy mới (hoặc hiện tại nếu cooldown)."""
    if provider == NO1:
        ok, message = proxyno1.change_ip(key)
        if report:
            report(f'{proxyno1.NAME}: {"đã đổi IP" if ok else "chưa đổi được IP"} ({proxyno1.mask(message, key)})')
        return _resolve_no1(key)
    return _resolve_vn(key, cancel=cancel, report=report, force_new=True)


class LeasePool:
    """Cấp phát 1 key RIÊNG cho mỗi account/profile; số luồng chạy = số key còn sống."""

    def __init__(self):
        self.lock = threading.Lock()
        self.free = []          # [(provider, key)] đang rảnh
        self.leased = {}        # account_id -> (provider, key)
        self.dead = set()       # (provider, key) đã die
        self.all = []           # tất cả key đã cấu hình (còn sống)

    def reset(self):
        """Xoá mọi lease + đánh dấu die (bắt đầu lần chạy mới / đổi key)."""
        with self.lock:
            self.free = []
            self.leased = {}
            self.dead = set()
            self.all = []

    def configure(self, tagged_keys):
        with self.lock:
            # Bỏ các lease trỏ tới key không còn trong danh sách (đổi key).
            valid = set(tagged_keys)
            for aid, held in list(self.leased.items()):
                if held not in valid:
                    self.leased.pop(aid, None)
            live = [k for k in tagged_keys if k not in self.dead]
            self.all = list(live)
            held = set(self.leased.values())
            self.free = [k for k in live if k not in held]

    def live_count(self):
        with self.lock:
            return len([k for k in self.all if k not in self.dead])

    def lease(self, account_id):
        """(provider, key) riêng cho account này, hoặc None nếu hết key sống."""
        with self.lock:
            if account_id in self.leased and self.leased[account_id] not in self.dead:
                return self.leased[account_id]
            while self.free:
                item = self.free.pop(0)
                if item in self.dead:
                    continue
                self.leased[account_id] = item
                return item
            return None

    def release(self, account_id):
        with self.lock:
            item = self.leased.pop(account_id, None)
            if item is not None and item not in self.dead and item not in self.free:
                self.free.append(item)

    def mark_dead(self, item):
        with self.lock:
            self.dead.add(item)
            if item in self.free:
                self.free.remove(item)
            for aid, held in list(self.leased.items()):
                if held == item:
                    self.leased.pop(aid, None)


# Pool dùng chung cho lần chạy hiện tại (cấu hình lại mỗi khi bật chạy / lưu cài đặt).
_POOL = LeasePool()


def pool():
    return _POOL
