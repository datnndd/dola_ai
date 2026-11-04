"""Resolve VOD metadata referenced by the current Dola conversation."""
import base64
import hashlib
import html
import json
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from dola_media import VideoSource, media_url, watermark_status


def decode_token(token, seed=''):
    direct=media_url(token)
    if direct:return direct
    if not token.startswith('qAAB') or not seed:return ''
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    def b64(x):return base64.b64decode(x+'='*(-len(x)%4),altchars=b'-_')
    try:
        data=b64(token);salt=bytes.fromhex('4dd4c2e6b83162090e52b3c7a6733ba41cb2462b829ab58a196b39db57177524f49baf7f08e8d68d26a72e37c1a95a2f1f05a51892aef2949732b62a38aadd58')
        digest=hashlib.sha512(hashlib.sha512(b64(seed)[:32]).digest()+salt).digest()
        key,iv=digest[:16],digest[16:32]
        variants=[(data[4:],key,iv),(data[4:],iv,key),(data[36:],key,data[20:36]),(data[36:],key,iv)]
        for body,k,v in variants:
            if not body or len(body)%16:continue
            decrypt=Cipher(algorithms.AES(k),modes.CBC(v)).decryptor()
            plain=decrypt.update(body)+decrypt.finalize()
            pad=plain[-1]
            if 1<=pad<=16 and plain.endswith(bytes([pad])*pad):plain=plain[:-pad]
            url=media_url(plain.decode('utf-8',errors='replace'))
            if url:return url
    except (ValueError,TypeError):pass
    return ''


def fallback_urls(payload):
    text=html.unescape(payload if isinstance(payload,str) else json.dumps(payload))
    for _ in range(5):
        text=text.replace('\\u002F','/').replace('\\/','/').replace('\\"','"').replace('\\\\','\\')
    found=[]
    for value in re.findall(r'"fallback_api"\s*:\s*"([^"\s]+)',text):
        value=value.rstrip('\\');u=urlsplit(value)
        if u.scheme=='https' and u.hostname and (u.hostname=='byteintlapi.com' or u.hostname.endswith('.byteintlapi.com')) and not u.username:
            if value not in found:found.append(value)
    return found


def clean_sources(payload,conversation):
    entries=[];seeds=[]
    def walk(node):
        if isinstance(node,dict):
            if isinstance(node.get('key_seed'),str):seeds.append(node['key_seed'])
            if node.get('main_url'):entries.append(node)
            for v in node.values():walk(v)
        elif isinstance(node,list):
            for v in node:walk(v)
    walk(payload)
    def number(entry, *keys):
        for key in keys:
            try:
                value = float(entry.get(key) or 0)
            except (ValueError, TypeError):
                value = 0
            if value:
                return value
        return 0
    def rank(entry):
        return (number(entry, 'height', 'vheight'), number(entry, 'width', 'vwidth'),
                number(entry, 'bitrate', 'real_bitrate'))
    result=[]
    for entry in sorted(entries,key=rank,reverse=True):
        for seed in ['']+seeds:
            url=decode_token(entry['main_url'],seed)
            if url:
                if watermark_status(url)=='clean':
                    result.append(VideoSource(url,conversation,(),'fallback_api:confirmed_unwatermarked'))
                break
    return list(dict.fromkeys(result))


def resolve(request, endpoint, conversation):
    if endpoint not in fallback_urls({'fallback_api':endpoint}):return []
    u=urlsplit(endpoint);query=dict(parse_qsl(u.query,keep_blank_values=True))
    query.update(logo_type='unwatermarked',codec_type='8',channel='no')
    target=urlunsplit(u._replace(query=urlencode(query)))
    response=None
    try:
        response=request.get(target,timeout=15000,max_redirects=0)
        if response.status!=200:return []
        data=response.body()
        return clean_sources(json.loads(data),conversation) if len(data)<8*1024*1024 else []
    finally:
        if response is not None:response.dispose()
