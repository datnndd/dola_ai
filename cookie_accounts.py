"""User-supplied cookie input and per-account proxy settings. Never logs secrets."""
import base64
import ctypes
from ctypes import wintypes
import json
import math
import os
from pathlib import Path
import re
from urllib.parse import urlsplit, unquote, quote


def protect(text, decrypt=False):
    if os.name != 'nt':
        raise ValueError('Lưu thông tin nhạy cảm hiện cần Windows DPAPI.')
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    raw = base64.b64decode(text, validate=True) if decrypt else text.encode('utf-8')
    buf = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    api = ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    api.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                    ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    api.restype = wintypes.BOOL
    if not api(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise ValueError('Không mã hóa/giải mã được dữ liệu bằng tài khoản Windows hiện tại.')
    try:
        value = ctypes.string_at(result.data, result.size)
        return value.decode('utf-8') if decrypt else base64.b64encode(value).decode('ascii')
    finally:
        free = ctypes.windll.kernel32.LocalFree
        free.argtypes = [ctypes.c_void_p]
        free.restype = ctypes.c_void_p
        free(result.data)


def parse_proxy(server, username='', password=''):
    server = server.strip()
    if not server: return {}
    try:
        # Shorthand HTTP proxy; split at most three times so ':' in passwords survives.
        if '://' not in server and '@' not in server and not server.startswith('['):
            parts = server.split(':', 3)
            if len(parts) == 4:
                server = parts[0] + ':' + parts[1]
                username, password = parts[2], parts[3]
        u = urlsplit(server if '://' in server else 'http://' + server)
        if u.scheme not in ('http', 'https', 'socks5') or not u.hostname or not u.port:
            raise ValueError()
        if u.path not in ('', '/') or u.query or u.fragment: raise ValueError()
        username = username or unquote(u.username or '')
        password = password or unquote(u.password or '')
        if u.scheme == 'socks5' and (username or password):
            raise ValueError('SOCKS5 có mật khẩu chưa được hỗ trợ; dùng HTTP/HTTPS proxy.')
        host = f'[{u.hostname}]' if ':' in u.hostname else u.hostname
        result = {'server': f'{u.scheme}://{host}:{u.port}'}
        if username: result['username'] = username
        if password: result['password_protected'] = protect(password)
        return result
    except ValueError as exc:
        if str(exc).startswith('SOCKS5'): raise
        raise ValueError('Proxy cần host:port hoặc host:port:user:pass (HTTP); cũng hỗ trợ URL http://, https://, socks5:// và ô user/mật khẩu riêng.') from None


def parse_proxy_lines(text):
    """One proxy per non-empty line, with line-only diagnostics (no credentials)."""
    proxies = []
    for number, line in enumerate(str(text or '').splitlines(), 1):
        if not line.strip():
            continue
        try:
            proxies.append(parse_proxy(line))
        except ValueError:
            raise ValueError(f'Proxy dòng {number} không hợp lệ. Dùng host:port hoặc host:port:user:pass.') from None
    return proxies


def browser_proxy(account):
    stored = account.get('proxy') or {}
    if not stored: return None
    result = {'server': stored['server']}
    if stored.get('username'): result['username'] = stored['username']
    # 'password' đã giải mã sẵn (proxy đã resolve, vd ProxyNo1); 'password_protected' là
    # bản mã hóa (account lưu tay). Đọc cả hai để không rớt mật khẩu khi gắn proxy vào GPM.
    if stored.get('password'):
        result['password'] = stored['password']
    elif stored.get('password_protected'):
        result['password'] = protect(stored['password_protected'], decrypt=True)
    return result


def proxy_input_value(stored):
    """Display an existing proxy in the same single field used for new accounts."""
    proxy = browser_proxy({'proxy': stored})
    if not proxy:
        return ''
    parsed = urlsplit(proxy['server'])
    user, password = proxy.get('username', ''), proxy.get('password', '')
    if parsed.scheme == 'http' and ':' not in parsed.hostname and ':' not in user and '@' not in user + password:
        return parsed.netloc + (f':{user}:{password}' if user or password else '')
    credentials = f'{quote(user, safe="")}:{quote(password, safe="")}@' if user or password else ''
    return f'{parsed.scheme}://{credentials}{parsed.netloc}'


def parse_cookie_line(line):
    line = line.strip()
    if line.startswith(('[', '{')):
        try:
            value = json.loads(line)
        except ValueError:
            # Some cookie exporters emit {...},{...} without an outer array.
            try:
                if not line.startswith('{'): raise ValueError()
                value = json.loads('[' + line + ']')
            except ValueError:
                raise ValueError('JSON không hợp lệ; mỗi tài khoản phải nằm trọn trên một dòng. Nhận cả {...},{...} không có ngoặc vuông.') from None
        raw = ([value] if 'name' in value and 'value' in value else value.get('cookies')) if isinstance(value, dict) else value
        if not isinstance(raw, list) or not raw: raise ValueError('Cần mảng cookie JSON hoặc đối tượng có trường cookies.')
    else:
        if line.lower().startswith('cookie:'): line = line[7:].strip()
        raw = []
        for field in line.split(';'):
            if not field.strip(): continue
            if '=' not in field: raise ValueError('Chuỗi cookie cần dạng tên=giá_trị; tên=giá_trị.')
            key, value = field.strip().split('=', 1)
            raw.append({'name':key, 'value':value, 'domain':'accounts.google.com' if key.startswith('__Host-') else '.google.com', 'path':'/', 'secure':True})
    if not raw or len(raw) > 500: raise ValueError('Cần từ 1 đến 500 cookie cho mỗi tài khoản.')
    result=[]; seen=set()
    for item in raw:
        if not isinstance(item, dict): raise ValueError('Mỗi cookie phải là một đối tượng JSON.')
        name, value = item.get('name'), item.get('value')
        if not isinstance(name,str) or not re.fullmatch(r'[^\s=;\x00-\x1f]+',name) or not isinstance(value,str):
            raise ValueError('Cookie cần name và value hợp lệ.')
        if any(c in value for c in '\r\n\x00'): raise ValueError('Giá trị cookie có ký tự không hợp lệ.')
        domain = item.get('domain') or (urlsplit(item.get('url','')).hostname if item.get('url') else '.google.com')
        if not isinstance(domain,str) or not re.fullmatch(r'(?:[a-z0-9-]+\.)*(?:google\.(?:com|com\.vn|co\.uk|co\.jp|co\.id|de|fr)|youtube\.com)',domain.lstrip('.').lower()):
            raise ValueError('Chỉ nhận cookie Google/YouTube được hỗ trợ; dùng bản xuất cookie của tài khoản Google.')
        domain=domain.lower()
        if item.get('hostOnly') is True:
            domain=domain.lstrip('.')
        elif item.get('hostOnly') is False:
            domain='.'+domain.lstrip('.')
        path = item.get('path','/')
        if not isinstance(path,str) or not path.startswith('/'): raise ValueError('Đường dẫn cookie không hợp lệ.')
        key = (name,domain,path)
        if key in seen: raise ValueError('Một dòng chứa cookie trùng tên/miền/đường dẫn.')
        seen.add(key)
        cookie={'name':name,'value':value,'domain':domain,'path':path,
                'secure':bool(item.get('secure',True)), 'httpOnly':bool(item.get('httpOnly',False))}
        same = str(item.get('sameSite','Lax')).lower()
        cookie['sameSite'] = {'strict':'Strict','lax':'Lax','none':'None','no_restriction':'None','unspecified':'Lax'}.get(same,'Lax')
        if cookie['sameSite']=='None' or name.startswith(('__Secure-','__Host-')): cookie['secure']=True
        if name.startswith('__Host-'):
            cookie.pop('domain'); cookie.pop('path')
            cookie['url']='https://' + domain.lstrip('.') + '/'
        expiry=item.get('expires',item.get('expirationDate'))
        if expiry is not None and not item.get('session'):
            try: expiry=float(expiry)
            except (ValueError,TypeError): raise ValueError('Hạn dùng cookie không hợp lệ.') from None
            if not math.isfinite(expiry): raise ValueError('Hạn dùng cookie không hợp lệ.')
            if expiry > 0: cookie['expires']=expiry
        result.append(cookie)
    return result


def parse_facebook_cookie_line(line):
    """Parse one Facebook cookie export without accepting credentials/password fields."""
    line=line.strip()
    if not line: raise ValueError('Dòng cookie Facebook đang trống.')
    if line.startswith(('[','{')):
        try: value=json.loads(line)
        except ValueError: raise ValueError('JSON cookie Facebook không hợp lệ.') from None
        raw=([value] if isinstance(value,dict) and 'name' in value and 'value' in value else
             value.get('cookies') if isinstance(value,dict) else value)
    else:
        if line.lower().startswith('cookie:'): line=line[7:].strip()
        raw=[]
        for field in line.split(';'):
            if not field.strip(): continue
            if '=' not in field: raise ValueError('Cookie Facebook cần dạng name=value; name=value.')
            name,value=field.strip().split('=',1)
            raw.append({'name':name.strip(),'value':value,'domain':'.facebook.com','path':'/','secure':True})
    if not isinstance(raw,list) or not raw or len(raw)>500: raise ValueError('Cần từ 1 đến 500 cookie Facebook.')
    result=[]; seen=set()
    for item in raw:
        if not isinstance(item,dict) or not isinstance(item.get('name'),str) or not isinstance(item.get('value'),str):
            raise ValueError('Cookie Facebook cần name và value hợp lệ.')
        domain=str(item.get('domain') or urlsplit(item.get('url','')).hostname or '.facebook.com').lower()
        if domain.endswith('facebook.com'):
            domain = '.facebook.com'
        elif not domain.startswith('.'):
            domain = '.' + domain
        if not re.fullmatch(r'(?:\.[a-z0-9-]+)*\.facebook\.com',domain):
            raise ValueError('Chỉ nhận cookie thuộc facebook.com.')
        key=(item['name'],domain,item.get('path','/'))
        if key in seen: raise ValueError('Cookie Facebook bị trùng tên/miền/đường dẫn.')
        seen.add(key)
        raw_samesite = str(item.get('sameSite') or 'None').capitalize()
        samesite = 'None' if raw_samesite in ('None', 'No_restriction') else (raw_samesite if raw_samesite in ('Lax', 'Strict') else 'None')
        secure = True if samesite == 'None' else bool(item.get('secure', True))
        result.append({'name':item['name'],'value':item['value'],'domain':domain,'path':item.get('path','/'),
                       'secure':secure,'httpOnly':bool(item.get('httpOnly',False)),
                       'sameSite':samesite})
    names={c['name'] for c in result}
    if not {'c_user','xs'}.issubset(names):
        raise ValueError('Cookie Facebook cần có cả c_user và xs.')
    return result


def _has_facebook_cookie(text):
    low=text.lower()
    return 'c_user=' in low and 'xs=' in low


def facebook_cookie_from_line(line):
    """Pull the Facebook cookie out of one line in any common shape and parse it.

    Accepts the pipe export ``uid|secret|c_user=…; xs=…|email`` (extra fields such
    as uid, 2FA secret and email are ignored), a plain ``c_user=…; xs=…`` string,
    ``cookie: …``, or a JSON array/object. Fails only when no cookie is present."""
    line=line.strip()
    if not line:
        raise ValueError('Dòng cookie Facebook đang trống.')
    if line.startswith(('[','{')):
        return parse_facebook_cookie_line(line)
    segments=[s.strip() for s in line.split('|')] if '|' in line else [line]
    candidates=[s for s in segments if _has_facebook_cookie(s)]
    if not candidates:
        # Some exports put each name=value in its own pipe field.
        joined='; '.join(s for s in segments if '=' in s and ';' not in s and s.count('=')==1)
        if _has_facebook_cookie(joined):
            candidates=[joined]
    if not candidates:
        raise ValueError('Không tìm thấy cookie Facebook trong dòng này (cần cả c_user và xs).')
    # The richest segment carries the full cookie when several fields match.
    return parse_facebook_cookie_line(max(candidates, key=len))


def save_cookies(folder, account_id, cookies):
    folder=Path(folder); folder.mkdir(parents=True,exist_ok=True)
    target=folder/(account_id+'.dpapi')
    target.write_text(protect(json.dumps(cookies,ensure_ascii=False)),encoding='ascii')
    return str(target.resolve())


def load_cookies(account):
    try:
        return json.loads(protect(Path(account['cookie_file']).read_text(encoding='ascii'),decrypt=True))
    except Exception:
        raise ValueError('Không đọc được cookie đã lưu. Nhập lại cookie bằng đúng tài khoản Windows.') from None
