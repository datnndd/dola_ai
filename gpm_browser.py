"""GPM Login local API v3 and Playwright CDP bridge. No fingerprint overrides."""
import csv
import hashlib
import io
import json
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit, urlencode, quote
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import HTTPError, URLError
from cookie_accounts import browser_proxy
from engine import atomic_json, CREATE_NO_WINDOW, Cancelled

DOLA_URL = 'https://www.dola.com/chat'


def startup_target(settings):
    target = settings.get('open_url') or DOLA_URL
    if not re.fullmatch(r'https://www\.dola\.com/chat(?:/\d+)?', target):
        raise GpmError('Trang khởi động phải là Dola hoặc hội thoại Dola.')
    return target


def configure_startup_page(client, pid, report=None):
    # Global exposes startup_urls; legacy v3 builds may reject or ignore it.
    # No URL is also passed on the command line: that would open a second tab.
    try:
        client.update(pid, startup_urls=DOLA_URL)
    except GpmError:
        if report:
            report('GPM chưa lưu được trang mặc định; tool sẽ mở Dola khi khởi chạy.')


def open_startup_page(context, target, cancel=None, startup_wait=3):
    from playwright.sync_api import TimeoutError as BrowserTimeout
    if cancel is not None and cancel.is_set():
        raise Cancelled('Đã dừng mở trang Dola.')
    def same_destination(url):
        actual, wanted = urlsplit(url), urlsplit(target)
        return (actual.hostname in ('dola.com', 'www.dola.com')
                and actual.path.rstrip('/') == wanted.path.rstrip('/'))
    deadline=time.monotonic()+startup_wait
    while True:
        if cancel is not None and cancel.is_set():
            raise Cancelled('Đã dừng mở trang Dola.')
        pages = [p for p in context.pages if not p.is_closed()]
        page = next((p for p in pages if same_destination(p.url)), None)
        if page is not None or time.monotonic() >= deadline:
            break
        if pages: pages[0].wait_for_timeout(100)
        else: time.sleep(.1)
    if page is None:
        # Reuse the startup Dola tab for a requested conversation, instead of
        # opening a second tab beside the default chat page.
        page = next((p for p in pages if urlsplit(p.url).hostname in ('dola.com','www.dola.com')), None)
        if page is None:
            page = next((p for p in pages if p.url in ('about:blank', 'chrome://newtab/', 'chrome://new-tab-page/')), None)
        if page is None:
            page = context.new_page()
        try:
            page.goto(target, wait_until='domcontentloaded', timeout=45000)
        except BrowserTimeout:
            # The worker can wait/retry loading; don't close a reachable Dola tab.
            if urlsplit(page.url).hostname not in ('dola.com', 'www.dola.com'):
                raise GpmError('Không mở được Dola. Kiểm tra mạng/proxy của profile GPM.') from None
    page.bring_to_front()
    return page


class GpmError(RuntimeError):
    pass


def normalize_url(value):
    """Trả về gốc http://127.0.0.1:PORT (không kèm /api/vN); chỉ chấp nhận máy cục bộ."""
    value=str(value).strip().rstrip('/')
    if not value: raise GpmError('Nhập port GPM API trong Cài đặt → Trình duyệt.')
    if value.isdigit(): value='http://127.0.0.1:'+value
    elif '://' not in value: value='http://'+value
    try:
        u=urlsplit(value)
        if u.scheme!='http' or u.hostname not in ('localhost','127.0.0.1','::1') or not u.port:
            raise ValueError()
        # Cho dán cả dạng có /api/v1 hoặc /api/v3; ta tự bỏ đuôi và dò lại phiên bản.
        if u.username or u.password or u.query or u.fragment or u.path not in ('','/api/v3','/api/v1'):
            raise ValueError()
    except ValueError:
        raise GpmError('GPM API cần port hoặc http://127.0.0.1:PORT (chỉ máy cục bộ).') from None
    return f'http://{u.netloc}'


def _safe_gpm_message(text):
    """Hiện thông báo lỗi của GPM nhưng lọc bỏ thông tin nhạy cảm (proxy/IP/URL/credentials)."""
    text=' '.join(str(text or '').split())
    if not text:
        return 'UNKNOWN_ERROR'
    if '@' in text or re.search(r'\b\d{1,3}(?:\.\d{1,3}){3}\b', text) or re.search(r'https?://', text):
        return 'UNKNOWN_ERROR'
    if re.search(r'\S+:\S+:\S+', text):  # trông giống host:port:user:pass
        return 'UNKNOWN_ERROR'
    if len(text)<=160 and re.fullmatch(r'[\w .,:;!?()\-/À-ỹ]+', text):
        return text
    return 'UNKNOWN_ERROR'


GPM_CANDIDATE_PORTS=(9495,19995,19996,9494,19994)


class GpmClient:
    # GPM Login Global (bản mới) dùng /api/v1; GPM Login cổ điển dùng /api/v3.
    # Dò (port + phiên bản) một lần cho mỗi cấu hình rồi nhớ lại để khỏi gọi mạng lặp lại.
    _resolved={}

    def __init__(self, base):
        self.configured=normalize_url(base)
        self.opener=build_opener(ProxyHandler({}))
        self.root,self.version=self._resolve()
        self.base=self.root+'/api/'+self.version

    def _looks_like_profiles(self,data):
        # Danh sách thật trả data là list (v3) hoặc dict phân trang (v1);
        # route sai của GPM Global trả data là chuỗi "GPMLogin Global API".
        if not isinstance(data,dict) or data.get('success') is not True: return False
        payload=data.get('data')
        return isinstance(payload,list) or (isinstance(payload,dict) and isinstance(payload.get('data'),list))

    def _version_on(self,root):
        """Trả 'v1'/'v3' nếu GPM API trả danh sách profile thật ở root này, else None."""
        for version in ('v1','v3'):
            url=f'{root}/api/{version}/profiles?page=1&per_page=1'
            try:
                with self.opener.open(Request(url,headers={'Content-Type':'application/json'}),timeout=8) as r:
                    if self._looks_like_profiles(json.load(r)):
                        return version
            except Exception:
                continue
        return None

    def _resolve(self):
        cached=GpmClient._resolved.get(self.configured)
        if cached: return cached
        # 1) Thử đúng port người dùng cấu hình.
        version=self._version_on(self.configured)
        if version:
            GpmClient._resolved[self.configured]=(self.configured,version)
            return self.configured,version
        # 2) Không thấy GPM ở port đó → tự dò các port GPM thường gặp.
        for port in GPM_CANDIDATE_PORTS:
            root=f'http://127.0.0.1:{port}'
            if root==self.configured: continue
            version=self._version_on(root)
            if version:
                GpmClient._resolved[self.configured]=(root,version)
                return root,version
        # 3) Vẫn không thấy (GPM chưa mở): giữ port cấu hình + v1; lệnh thật báo lỗi kết nối rõ ràng.
        return self.configured,'v1'

    def request(self, path, body=None, query=None):
        url=self.base+path+('?' + urlencode(query) if query else '')
        req=Request(url,data=json.dumps(body).encode('utf-8') if body is not None else None,
                    headers={'Content-Type':'application/json'})
        try:
            with self.opener.open(req,timeout=60) as response:
                data=json.load(response)
        except HTTPError as exc: raise GpmError(f'GPM API trả HTTP {exc.code}; kiểm tra GPM Login đã bật API chưa.') from None
        except (URLError,TimeoutError,OSError):
            raise GpmError('Không kết nối được GPM API. Mở GPM Login, bật API và kiểm tra port trong Cài đặt.') from None
        except ValueError: raise GpmError('GPM API không trả JSON hợp lệ.') from None
        if not isinstance(data,dict) or data.get('success') is not True:
            code=str(data.get('message') or data.get('error') or '') if isinstance(data,dict) else ''
            messages={'INVALID_MODE':'Thiếu hoặc sai mode xóa profile.',
                      'PROFILE_NOT_FOUND':'Không tìm thấy profile trong GPM.',
                      'PROFILE_IS_RUNNING':'Profile đang mở; đóng trong GPM rồi thử lại.',
                      'PROFILE_RUNNING':'Profile đang mở; đóng trong GPM rồi thử lại.'}
            # Hiện thông báo gốc của GPM (đã lọc bỏ thông tin nhạy cảm như proxy/IP/URL)
            # để chẩn đoán được, thay vì luôn che thành UNKNOWN_ERROR.
            safe_code=_safe_gpm_message(code)
            raise GpmError(f'GPM API {path.split("?")[0].rsplit("/",1)[0]}: {safe_code}. '+
                           messages.get(code,'GPM từ chối yêu cầu; kiểm tra trạng thái profile/proxy trong GPM.'))
        return data

    def profiles(self, page=1, size=100):
        result=self.request('/profiles',query={'page':page,'per_page':size,'page_size':size})
        data=result.get('data')
        if isinstance(data,list):
            rows=data;pagination=result.get('pagination') or {}
        elif isinstance(data,dict) and isinstance(data.get('data'),list):
            rows=data['data'];pagination=data
        else: raise GpmError('Response danh sách profile có cấu trúc chưa hỗ trợ.')
        return rows,pagination,result

    def all_profiles(self):
        rows=[]
        for page in range(1,101):
            batch,pagination,_=self.profiles(page)
            rows.extend(batch)
            last=pagination.get('total_page',pagination.get('last_page'))
            if not batch or (last is not None and page>=int(last)) or (last is None and len(batch)<100): return rows
        raise GpmError('Danh sách quá 100 trang; lọc/import trực tiếp bằng ID profile.')

    def create(self,name,proxy=''):
        # Global v1 dùng 'name'; v3 cổ điển dùng 'profile_name'. Gửi cả hai cho an toàn
        # (GPM bỏ qua khóa lạ). raw_proxy dạng host:port:user:pass.
        result=self.request('/profiles/create',{'name':name,'profile_name':name,'raw_proxy':proxy})
        data=result.get('data') or {}
        if not data.get('id'): raise GpmError('GPM đã nhận lệnh tạo nhưng không trả ID. Kiểm tra GPM trước khi thử lại.')
        return data

    def update(self,pid,**values):
        return self.request('/profiles/update/'+quote(str(pid),safe=''),values)

    def start(self,pid,args=''):
        # Legacy v3 spells this "addination_args" (verified against installed GPM).
        return self.request('/profiles/start/'+quote(str(pid),safe=''),query={'addination_args':args} if args else None)['data']

    def close(self,pid):
        # GPM Login Global (v1) đóng profile bằng 'stop'; v3 cổ điển dùng 'close'.
        # (Trên v1, route 'close/{id}' không tồn tại -> trả stub "success" nhưng KHÔNG đóng.)
        verb='stop' if self.version=='v1' else 'close'
        return self.request('/profiles/'+verb+'/'+quote(str(pid),safe=''))

    def delete(self,pid):
        """Delete exactly one linked profile, using the verified v3 mode=1."""
        pid=str(pid).strip()
        if not pid or len(pid)>100 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in pid):
            raise GpmError('ID profile GPM không hợp lệ; chưa xóa.')
        if not any(str(row.get('id'))==pid for row in self.all_profiles()):
            return {'success':True,'already_absent':True}
        result=self.request('/profiles/delete/'+quote(pid,safe=''),query={'mode':1})
        if any(str(row.get('id'))==pid for row in self.all_profiles()):
            raise GpmError('GPM vẫn còn profile sau yêu cầu xóa. Đóng profile rồi thử lại; phiên trong tool được giữ.')
        return result


def delete_account_profile(settings,account):
    """Resolve an existing link only; deleting must never create a new profile."""
    base=account.get('gpm_api') or settings.get('gpm_api','')
    if not base:
        if account.get('gpm_profile_id'):
            raise GpmError('Chưa có địa chỉ API của profile cần xóa.')
        if list((Path(settings['lock_root']).parent/'gpm_links').glob('*.json')):
            raise GpmError('Nhập lại port GPM để xác định liên kết trước khi xóa phiên.')
        return False
    client=GpmClient(base)
    key=hashlib.sha256((client.root+'|'+account['id']).encode()).hexdigest()
    path=Path(settings['lock_root']).parent/'gpm_links'/(key+'.json')
    link=json.loads(path.read_text('utf-8')) if path.exists() else {}
    pid=account.get('gpm_profile_id') or link.get('id')
    if not pid:
        if link.get('create_pending'):
            raise GpmError('Profile tạo trước đó chưa rõ ID. Liên kết đúng GPM ID rồi xóa lại.')
        return False
    client.delete(pid)
    if path.exists():path.unlink()
    return True


def proxy_string(account):
    proxy=browser_proxy(account)
    if not proxy:return ''
    u=urlsplit(proxy['server'])
    # GPM nhận raw_proxy dạng host:port:user:pass, có tiền tố socks5:// cho SOCKS.
    host=f'[{u.hostname}]' if ':' in u.hostname else u.hostname
    value=f'{host}:{u.port}'
    if proxy.get('username') or proxy.get('password'):
        if ':' in proxy.get('username','') or ':' in proxy.get('password',''):
            raise GpmError('GPM proxy không nhận dấu hai chấm trong user/mật khẩu.')
        value+=':'+proxy.get('username','')+':'+proxy.get('password','')
    return ('socks5://' if u.scheme=='socks5' else 'https://' if u.scheme=='https' else '')+value


def prepare_profile(settings,account):
    client=GpmClient(settings.get('gpm_api',''))
    key=hashlib.sha256((client.root+'|'+account['id']).encode()).hexdigest()
    path=Path(settings['lock_root']).parent/'gpm_links'/(key+'.json')
    link=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    explicit=account.get('gpm_profile_id')
    if explicit:
        if account.get('gpm_api') and normalize_url(account['gpm_api'])!=client.root:
            raise GpmError('Profile đã liên kết với địa chỉ GPM khác. Chọn lại đúng port hoặc liên kết profile lại.')
        if link.get('id')!=explicit: link={'id':explicit,'owned':False}
    if not link.get('id'):
        if link.get('create_pending'):
            raise GpmError('Lần tạo GPM trước chưa xác nhận được ID. Mở Test API / tải profile và liên kết profile đã tạo; không tự tạo trùng.')
        # No cookie/profile folder copying: create a separate GPM-owned profile.
        raw_proxy=proxy_string(account)
        client.profiles(size=1)
        atomic_json(path,{'create_pending':True})
        created=client.create('StudioPython • '+account['name'],raw_proxy)
        link={'id':created['id'],'owned':True,'proxy_applied':account.get('proxy') or {}}
        atomic_json(path,link)
    # Luôn gắn proxy (IP vừa đổi) vào profile TRƯỚC khi start — nhất là ProxyVN đổi endpoint
    # mỗi lần xoay. Chỉ bỏ qua khi không có proxy (tắt proxy).
    if account.get('proxy'):
        client.update(link['id'],raw_proxy=proxy_string(account))
        link['proxy_applied']=account['proxy'];atomic_json(path,link)
    return client,link,path


def rename_profile(settings,account,name):
    client,link,_=prepare_profile(settings,account)
    client.update(link['id'],name=name,profile_name=name)


def start_process_id(data):
    """PID của phiên vừa mở; GPM bản mới có thể đặt dưới các khóa khác nhau."""
    if not isinstance(data,dict):return None
    info=data.get('addition_info')
    info=info if isinstance(info,dict) else {}
    return (data.get('process_id') or data.get('pid') or data.get('browser_pid')
            or info.get('process_id') or info.get('pid'))


def debugger_endpoint(data):
    if not isinstance(data,dict):raise GpmError('Response start không có data hợp lệ.')
    # GPM Login bản mới (api-docs.gpmloginapp.com) có thể dùng tên trường khác nhau cho
    # địa chỉ CDP: 'remote_debug_address' (mẫu Selenium/Puppeteer), các bản cũ dùng
    # 'remote_debugging_address' / 'websocket_debugging_url'. Chấp nhận mọi biến thể.
    value=(data.get('websocket_debugging_url') or data.get('remote_debugging_address')
           or data.get('remote_debug_address') or data.get('websocket_debug_address')
           or data.get('ws_debugger_url') or data.get('debug_address'))
    if not value:
        port=(data.get('remote_debugging_port') or data.get('remote_debug_port')
              or data.get('remote_port') or data.get('debug_port'))
        if port:value='127.0.0.1:'+str(port)
    if not isinstance(value,str) or not value:raise GpmError('Response start thiếu địa chỉ remote debugging.')
    if '://' not in value:value='http://'+value
    u=urlsplit(value)
    if u.scheme not in ('http','ws') or u.hostname not in ('127.0.0.1','localhost','::1'):
        raise GpmError('GPM trả địa chỉ debug không phải máy cục bộ.')
    return value


def process_ids():
    try:
        result=subprocess.run(['tasklist','/FO','CSV','/NH'],capture_output=True,creationflags=CREATE_NO_WINDOW,timeout=15)
        if result.returncode:raise ValueError()
        return {int(row[1]) for row in csv.reader(io.StringIO(result.stdout.decode('utf-8',errors='replace')))
                if len(row)>1 and row[1].isdigit()}
    except Exception:
        raise GpmError('Không kiểm tra được profile đang mở. Chưa mở GPM để tránh đóng nhầm cửa sổ đang dùng.') from None


def terminate_pid(pid):
    """Tắt thẳng cây tiến trình trình duyệt của profile (fallback khi API đóng không được)."""
    try:
        pid=int(pid)
    except (TypeError,ValueError):
        return False
    try:
        subprocess.run(['taskkill','/PID',str(pid),'/T','/F'],
                       capture_output=True,creationflags=CREATE_NO_WINDOW,timeout=15)
    except Exception:
        return False
    try:
        return pid not in process_ids()
    except GpmError:
        return False


class GpmContext:
    def __init__(self,context,browser,client,pid,process_id,startup_page=None):
        self._context,self._browser,self._client,self._pid=context,browser,client,pid
        self._process_id=int(process_id)
        self._closed=False
        self._startup_page=startup_page
    def __getattr__(self,name):return getattr(self._context,name)
    @property
    def pages(self):
        pages=list(self._context.pages)
        if self._startup_page in pages:
            pages.remove(self._startup_page)
            pages.insert(0,self._startup_page)
        return pages
    def close(self):
        if self._closed:return
        # 1) Đóng chuẩn qua API GPM (stop trên Global v1 / close trên v3).
        try:
            self._client.close(self._pid)
        except GpmError:
            pass
        # 2) Chờ GPM tự đóng tiến trình trình duyệt của profile.
        deadline=time.monotonic()+12
        while self._process_id in process_ids():
            if time.monotonic()>deadline:break
            time.sleep(.3)
        # 3) Fallback: profile vẫn mở thì tắt thẳng tiến trình (đóng qua tự động).
        if self._process_id in process_ids() and not terminate_pid(self._process_id):
            raise GpmError('GPM chưa đóng xong profile. Đóng thủ công trong GPM trước khi chạy tiếp.')
        self._closed=True


def move_window_offscreen(context, page):
    """Đưa cửa sổ trình duyệt ra ngoài màn hình (chạy ẩn) — giữ 'normal' nên KHÔNG bị throttle."""
    try:
        cdp = context.new_cdp_session(page)
        win = cdp.send('Browser.getWindowForTarget')
        cdp.send('Browser.setWindowBounds', {'windowId': win['windowId'],
                 'bounds': {'left': -32000, 'top': -32000, 'width': 1100, 'height': 760}})
        return True
    except Exception:
        return False


def connect_gpm(playwright,settings,account,extension=None,*,cancel=None,report=None,hidden=False):
    if cancel is not None and cancel.is_set():raise Cancelled('Đã dừng kết nối GPM.')
    target=startup_target(settings)
    client,link,_=prepare_profile(settings,account)
    configure_startup_page(client,link['id'],report)
    previous=process_ids()
    args=(subprocess.list2cmdline([f'--disable-extensions-except={extension}',f'--load-extension={extension}'])
          if extension else '--disable-extensions')
    data=client.start(link['id'],args)
    pid=start_process_id(data)
    if not pid or int(pid) in previous:
        raise GpmError('Profile GPM đang mở sẵn hoặc chưa xác định được phiên mới. Hãy tự đóng profile trong GPM rồi chạy lại; tool giữ nguyên cửa sổ đang dùng.')
    try:
        endpoint=debugger_endpoint(data)
        deadline=time.monotonic()+45
        attempt=0
        while True:
            if cancel is not None and cancel.is_set():raise Cancelled('Đã dừng kết nối GPM.')
            try:
                browser=playwright.chromium.connect_over_cdp(endpoint,timeout=5000)
                if not browser.contexts:raise GpmError('GPM chưa trả context Chromium.')
                break
            except Exception:
                if time.monotonic()>=deadline:raise
                attempt+=1
                delay=min(3,.5*2**min(attempt-1,3))
                if report:report(f'Đang chờ GPM sẵn sàng kết nối • lần {attempt}, nghỉ {delay:g}s…')
                if cancel is not None:
                    if cancel.wait(delay):raise Cancelled('Đã dừng kết nối GPM.')
                else:time.sleep(delay)
        browser.contexts[0].set_default_timeout(15000)
        page=open_startup_page(browser.contexts[0],target,cancel)
        if report:report('Đã mở trang Dola trong profile GPM.')
        if hidden:
            if move_window_offscreen(browser.contexts[0],page):
                if report:report('Chạy ẩn: đã đưa cửa sổ GPM ra ngoài màn hình.')
            elif report:report('Chạy ẩn: chưa ẩn được cửa sổ; vẫn chạy bình thường.')
        return GpmContext(browser.contexts[0],browser,client,link['id'],pid,page)
    except Cancelled:
        try:client.close(link['id'])
        except GpmError:pass
        raise
    except Exception:
        try:client.close(link['id'])
        except GpmError:pass
        raise GpmError('Không kết nối được phiên GPM qua remote debugging. Kiểm tra phiên bản GPM/browser.') from None


def manual_gpm(settings,account,cancel,report,target):
    target=startup_target(dict(settings,open_url=target))
    client,link,_=prepare_profile(settings,account)
    configure_startup_page(client,link['id'],report)
    previous=process_ids()
    data=client.start(link['id'],'--disable-extensions')
    pid=start_process_id(data)
    if not pid or int(pid) in previous:
        raise GpmError('Profile GPM đã mở sẵn. Dùng cửa sổ đó và đóng trước khi chạy video.')
    # Briefly attach only to ensure the login tab opens even when startup args
    # are ignored by GPM. Disconnect Playwright without closing the user's browser.
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as p:
            deadline=time.monotonic()+45
            while True:
                if cancel.is_set():raise Cancelled('Đã dừng mở trang đăng nhập Dola.')
                try:
                    browser=p.chromium.connect_over_cdp(debugger_endpoint(data),timeout=5000)
                    if not browser.contexts:raise GpmError('GPM chưa trả context Chromium.')
                    break
                except Exception:
                    if time.monotonic()>=deadline:raise
                    if cancel.wait(.5):raise Cancelled('Đã dừng mở trang đăng nhập Dola.')
            context=browser.contexts[0]
            if account.get('cookie_file'):
                from cookie_accounts import load_cookies
                cookies=load_cookies(account)
                context.add_cookies(cookies)
                report('Đã nạp cookie vào profile GPM; đang mở Facebook để bạn xác nhận đăng nhập.')
            open_startup_page(context,target,cancel)
    except Cancelled:
        report('Đã dừng mở trang; cửa sổ GPM vẫn được giữ để bạn tự đóng.')
        return False
    except Exception:
        raise GpmError('GPM đã mở nhưng chưa mở được Dola. Cửa sổ được giữ lại; kiểm tra mạng/proxy và mở https://www.dola.com/chat.') from None
    report('Đã mở Dola trong GPM để bạn tự đăng nhập. Đóng profile rồi bấm Kiểm tra.')
    while int(pid) in process_ids():
        if cancel.wait(1):
            report('Đã dừng theo dõi; cửa sổ GPM vẫn được giữ để bạn tự đóng.')
            return False
    return True
