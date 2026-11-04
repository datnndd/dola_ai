"""Parse user-provided Dola sessions without trusting exported account flags."""
import json
import math
import re
from urllib.parse import urlsplit


def normalize_cookies(raw):
    if isinstance(raw, str):
        if raw.lower().startswith('cookie:'):
            raw = raw[7:].strip()
        parts = [field.strip() for field in raw.split(';') if field.strip()]
        if any('=' not in field for field in parts):
            raise ValueError('Cookie cần dạng sessionid=…; tên=giá_trị.')
        raw = [dict(zip(('name', 'value'), field.split('=', 1))) for field in parts]
    elif isinstance(raw, dict):
        raw = [raw] if 'name' in raw and 'value' in raw else [{'name':k, 'value':v} for k,v in raw.items()]
    if not isinstance(raw, list) or not 1 <= len(raw) <= 500:
        raise ValueError('Mỗi tài khoản cần từ 1 đến 500 cookie Dola.')
    result, seen = [], set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError('Mỗi cookie phải là đối tượng JSON.')
        name, value = item.get('name'), item.get('value')
        if not isinstance(name, str) or not re.fullmatch(r'[^\s=;\x00-\x1f\x7f]+', name):
            raise ValueError('Tên cookie không hợp lệ.')
        if not isinstance(value, str) or re.search(r'[\x00-\x1f\x7f]', value):
            raise ValueError('Giá trị cookie không hợp lệ.')
        domain = item.get('domain') or (urlsplit(item.get('url', '')).hostname if item.get('url') else '.dola.com')
        if not isinstance(domain, str) or not re.fullmatch(r'(?:[a-z0-9-]+\.)*dola\.com', domain.lower().lstrip('.')):
            raise ValueError('Chỉ nhận cookie thuộc miền dola.com; không dùng cookie Google/Facebook ở đây.')
        domain = domain.lower()
        if item.get('hostOnly') is True: domain = domain.lstrip('.')
        if item.get('hostOnly') is False: domain = '.' + domain.lstrip('.')
        path = item.get('path', '/')
        if not isinstance(path, str) or not path.startswith('/'):
            raise ValueError('Đường dẫn cookie không hợp lệ.')
        key = (name, domain, path)
        if key in seen: raise ValueError('Cookie trùng tên, miền và đường dẫn.')
        seen.add(key)
        cookie = {'name':name, 'value':value, 'domain':domain, 'path':path,
                  'secure':bool(item.get('secure', True)), 'httpOnly':bool(item.get('httpOnly', name in ('sessionid','sessionid_ss')))}
        cookie['sameSite'] = {'strict':'Strict','lax':'Lax','none':'None','no_restriction':'None'}.get(str(item.get('sameSite','Lax')).lower(),'Lax')
        if cookie['sameSite']=='None' or name.startswith(('__Secure-', '__Host-')): cookie['secure']=True
        if name.startswith('__Host-'):
            cookie.pop('domain');cookie.pop('path');cookie['url']='https://'+domain.lstrip('.')+'/'
        expires = item.get('expires', item.get('expirationDate'))
        if expires is not None and not item.get('session'):
            try: expires = float(expires)
            except (ValueError, TypeError): raise ValueError('Hạn cookie không hợp lệ.') from None
            if not math.isfinite(expires): raise ValueError('Hạn cookie không hợp lệ.')
            if expires > 0: cookie['expires']=expires
        result.append(cookie)
    if not any(c['name'] in ('sessionid','sessionid_ss') and c['value'].strip() for c in result):
        raise ValueError('Chưa có sessionid của Dola trong dữ liệu nhập.')
    return result


def json_documents(text):
    """One JSON value, or several objects/arrays pasted one after another."""
    if not text or text[0] not in '{[':
        return None
    decoder = json.JSONDecoder()
    index, documents = 0, []
    while index < len(text):
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text):
            break
        if text[index] not in '{[':
            raise ValueError('JSON không hợp lệ. Mỗi tài khoản là một khối JSON, có thể dán nhiều khối hoặc nhiều dòng.')
        try:
            value, end = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            raise ValueError('JSON không hợp lệ. Mỗi tài khoản là một khối JSON, có thể dán nhiều khối hoặc nhiều dòng.') from None
        documents.append(value)
        index = end
    return documents


def _account_items(value):
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list) and value and all(isinstance(c, dict) and 'name' in c and 'value' in c for c in value):
        return [{'cookies': value}]
    if isinstance(value, list):
        return value
    raise ValueError('Tài khoản phải là đối tượng JSON.')


def parse_dola_accounts(text):
    text = text.lstrip('\ufeff').strip()
    if not text: raise ValueError('Chọn file JSON hoặc dán cookie Dola trước.')
    documents = json_documents(text)
    if documents is None:
        value = [{'cookie': line} for line in text.splitlines() if line.strip()]
    else:
        value = []
        for document in documents:
            value.extend(_account_items(document))
    if not isinstance(value,list) or not 1 <= len(value) <= 50:
        raise ValueError('Nhập từ 1 đến 50 tài khoản mỗi lượt.')
    accounts=[]
    for index, item in enumerate(value,1):
        try:
            if not isinstance(item,dict): raise ValueError('Tài khoản phải là đối tượng JSON.')
            raw = item.get('cookies') or item.get('cookie')
            if raw is None and item.get('session_id'): raw={'sessionid':item['session_id']}
            if raw is None and item.get('sessionid'): raw={'sessionid':item['sessionid']}
            if raw is None and 'name' in item and 'value' in item: raw=[item]
            cookies=normalize_cookies(raw)
            # Exporters commonly include both fields. Reject inconsistent sessions.
            if item.get('cookies') and item.get('cookie'):
                secondary=normalize_cookies(item['cookie'])
                a={c['name']:c['value'] for c in cookies if c['name'] in ('sessionid','sessionid_ss')}
                b={c['name']:c['value'] for c in secondary if c['name'] in ('sessionid','sessionid_ss')}
                if any(a[k]!=b[k] for k in a.keys() & b.keys()):
                    raise ValueError('Trường cookies và cookie chứa session khác nhau.')
            name=item.get('profile') or item.get('name') or f'Dola {index}'
            if not isinstance(name,str): raise ValueError('Tên profile phải là chuỗi.')
            name=' '.join(name.split())[:100] or f'Dola {index}'
            accounts.append({'name':name,'cookies':cookies})
        except (ValueError, TypeError):
            # Parser errors never interpolate input, session values or JSON snippets.
            raise ValueError(f'Tài khoản {index}: dữ liệu cookie Dola không hợp lệ. Kiểm tra sessionid, miền dola.com và các trường cookies/cookie có khớp nhau không.') from None
    return accounts
