"""Read video sources returned to this Dola page; never invent or rewrite URLs."""
from collections import deque
from dataclasses import dataclass
import html
import base64
import json
import re
import time
from urllib.parse import parse_qs, urlsplit


MAX_BODY = 8 * 1024 * 1024
CLEAN_KEYS = {'no_watermark_url', 'unwatermarked_url', 'watermark_free_url', 'video_no_watermark_url',
              'resource_without_watermark', 'video_without_watermark', 'url_without_watermark',
              'no_logo_url', 'logo_free_url', 'origin_url', 'original_url', 'clean_url',
              'download_url_without_watermark', 'video_url_without_watermark'}
URL_KEYS = CLEAN_KEYS | {'main_url', 'backup_url', 'backup_url_1', 'play_url', 'download_url', 'video_url', 'url'}
VIDEO_KEYS = {'video_model', 'video_list', 'video_info', 'videos', 'video', 'video_data'}


def media_url(value):
    if not isinstance(value,str) or len(value)>32768:return ''
    value=html.unescape(value.strip())
    if not value.startswith(('https://','http://')):
        try:value=base64.b64decode(value+'='*(-len(value)%4),altchars=b'-_',validate=True).decode('utf-8')
        except (ValueError,UnicodeError):return ''
    try:
        u=urlsplit(value)
        if u.scheme not in ('https','http') or not u.hostname or u.username or u.password:return ''
        # A service response must not turn into a request to the local machine.
        import ipaddress
        try:
            if not ipaddress.ip_address(u.hostname).is_global:return ''
        except ValueError:
            if u.hostname=='localhost' or '.' not in u.hostname or u.hostname.endswith(('.local','.localhost')):return ''
        return value
    except ValueError:return ''


def watermark_status(url):
    """Analyze URL query parameters to determine watermark status."""
    query = parse_qs(urlsplit(url).query)
    values = {k: [v.casefold() for v in vs] for k, vs in query.items()}
    
    # Explicit watermark indicators (value = 1 or marked)
    if ('1' in values.get('watermark', []) or 
        '1' in values.get('wm', []) or
        any(v in ('watermarked', 'video_gen_watermark', 'video_gen_watermark_dyn', 'with_watermark', 'logo', 'has_logo', 'with_logo') 
            for vs in values.values() for v in vs)):
        return 'marked'
    
    # Explicit clean indicators (value = 0 or no watermark)
    if ('0' in values.get('watermark', []) or 
        '0' in values.get('wm', []) or
        any(v in ('unwatermarked', 'video_gen_no_watermark', 'no_watermark', 'without_watermark',
                  'no_logo', 'without_logo', 'clean', 'origin', 'original') 
            for vs in values.values() for v in vs)):
        return 'clean'
    
    # Check URL path/hostname for watermark hints
    from urllib.parse import urlsplit as _urlsplit
    parts = _urlsplit(url)
    path = parts.path.casefold()
    if any(kw in path for kw in ('watermark', 'logo', 'wm_')):
        return 'marked'
    if any(kw in path for kw in ('nowatermark', 'nowm', 'nologo', 'clean', 'original')):
        return 'clean'
    
    return 'unknown'


def provenance_has_logo(provenance):
    """Treat explicit watermark and Dola's fallback/served MP4 as logo output."""
    provenance = provenance if isinstance(provenance, dict) else {}
    status = str(provenance.get('watermark_status', '')).casefold()
    evidence = str(provenance.get('evidence', '')).casefold()
    mode = str(provenance.get('mode', '')).casefold()
    return (status in ('marked', 'watermarked', 'with_watermark', 'has_logo')
            or evidence in ('dola_download_url', 'served_video_url')
            or mode == 'standard')


@dataclass(frozen=True)
class VideoSource:
    url: str
    conversation: str
    asset_ids: tuple
    evidence: str


def extract_sources(payload, conversation='', max_nodes=25000, allow_model=False):
    """Only explicit clean fields/flags qualify; arbitrary video_model URLs do not."""
    result=[];seen=set();remaining=[max_nodes]
    def walk(node,cid,ids,video=False,clean=False,depth=0):
        remaining[0]-=1
        if remaining[0]<0 or depth>25:return
        if isinstance(node,str):
            if len(node)<=MAX_BODY and node.lstrip().startswith(('{','[')):
                try:walk(json.loads(node),cid,ids,video,clean,depth+1)
                except (ValueError,RecursionError):pass
            return
        if isinstance(node,list):
            for item in node:walk(item,cid,ids,video,clean,depth+1)
            return
        if not isinstance(node,dict):return
        cid=str(node.get('conversation_id') or node.get('conversationId') or node.get('chat_id') or cid)
        ids=tuple(dict.fromkeys(ids+tuple(str(node[k]) for k in ('video_id','vid','item_id')
                                         if isinstance(node.get(k),(str,int)) and str(node[k]))))
        video=video or bool(VIDEO_KEYS.intersection(node)) or bool(CLEAN_KEYS.intersection(node))
        flag=node.get('watermark',node.get('has_watermark'))
        marked=flag is True or flag==1 or str(flag).lower() in ('true','watermarked')
        clean=(clean or flag is False or flag==0 or str(flag).lower() in ('false','0')
               or node.get('logo_type')=='unwatermarked' or node.get('is_unwatermarked') is True) and not marked
        for key,value in node.items():
            if key in ('cover','poster','thumbnail','image','images','avatar'):continue
            if video and key in URL_KEYS:
                url=media_url(value)
                if url:
                    status=watermark_status(url)
                    model_source=allow_model and key=='main_url' and 'video_list' in ids
                    explicit=key in CLEAN_KEYS or clean or status=='clean' or model_source
                    if explicit and status!='marked' and (not marked or key in CLEAN_KEYS):
                        evidence='video_model:main_url (unverified)' if model_source and not clean and status!='clean' else key if key in CLEAN_KEYS else ('metadata:no_watermark' if clean else 'url:unwatermarked')
                        signature=(url,cid,ids)
                        if signature not in seen:
                            seen.add(signature);result.append(VideoSource(url,cid,tuple(i for i in ids if i!='video_list'),evidence))
            if key=='video_model' and isinstance(value,str) and not value.lstrip().startswith(('{','[')):
                try:value=base64.b64decode(value+'='*(-len(value)%4),altchars=b'-_',validate=True).decode('utf-8')
                except (ValueError,UnicodeError):continue
            if key=='video_list' and isinstance(value,dict):
                def bitrate(item):
                    try:return float(item.get('bitrate') or item.get('real_bitrate') or 0) if isinstance(item,dict) else 0
                    except (TypeError,ValueError):return 0
                value=sorted(value.values(),key=bitrate,reverse=True)
            walk(value,cid,ids+('video_list',) if key=='video_list' else ids,video or key in VIDEO_KEYS,clean or key=='unwatermarked',depth+1)
    walk(payload,conversation,())
    return result


def select_sources(sources,conversation,player_urls):
    """Bind results to the open conversation and, when available, the player asset."""
    players=[media_url(u) for u in player_urls if media_url(u)]
    paths={(urlsplit(u).hostname,urlsplit(u).path) for u in players}
    matched=[];scoped=[]
    for source in sources:
        if source.conversation and source.conversation!=conversation:continue
        u=urlsplit(source.url)
        same_path=(u.hostname,u.path) in paths
        tokens=set(re.findall(r'[A-Za-z0-9_-]+',' '.join(players)))
        same_id=any(len(asset)>=6 and asset in tokens for asset in source.asset_ids)
        if same_path or same_id:matched.append(source)
        elif source.conversation==conversation and conversation:scoped.append(source)
    if matched:return prefer_clean(list(dict.fromkeys(matched)))
    # An unambiguous single asset in the current new/resumed conversation is safe.
    groups={s.asset_ids or (urlsplit(s.url).hostname,urlsplit(s.url).path) for s in scoped}
    return prefer_clean(list(dict.fromkeys(scoped))) if len(groups)==1 else []


def prefer_clean(result):
    """Rank candidates so confirmed clean URLs beat unknown, marked last.

    ``watermark_status`` only reads explicit signals from the URL, so a
    source is dropped as 'marked' only when it actually advertises a
    watermark. Sources with an unknown status are kept and tried first
    among the unconfirmed ones; a fully watermarked batch is still
    returned so the caller can fall back instead of getting nothing.
    """
    unique=list(dict.fromkeys(result))
    if len(unique)<2:return unique
    rank={'clean':0,'unknown':1,'marked':2}
    order=sorted(range(len(unique)),key=lambda i:(rank.get(watermark_status(unique[i].url),1),i))
    ranked=[unique[i] for i in order]
    if all(watermark_status(s.url)=='marked' for s in unique):return unique
    kept=[s for s in ranked if watermark_status(s.url)!='marked']
    return kept or unique


class DolaMediaTracker:
    """Drain only finished responses, so SSE body reads cannot stall the browser."""
    def __init__(self,page):
        self.page=page;self.epoch=0;self.requests={};self.pending=deque(maxlen=32)
        self.sources=[];self.media_urls=[];self.parse_failures=0
        self.history_request=None;self.last_pull=0
        self.fallbacks={};self.fallback_attempts={};self.last_fallback=0
        page.on('request',self.started)
        page.on('requestfinished',self.finished)
        page.on('requestfailed',self.failed)
        page.on('response',self.responded)

    def responded(self,response):
        # Streaming video responses may remain open indefinitely. Capture their
        # served URL from headers instead of waiting for requestfinished.
        try:
            if self.requests.get(response.request)!=self.epoch:return
            if response.status in (200,206) and 'video/' in response.headers.get('content-type','').lower():
                url=media_url(response.url)
                if url and url not in self.media_urls:
                    self.media_urls.append(url)
                    self.media_urls=self.media_urls[-100:]
        except Exception:
            pass

    def reset(self):
        self.epoch+=1;self.pending.clear();self.sources.clear();self.media_urls.clear()
        self.fallbacks.clear();self.fallback_attempts.clear();self.history_request=None

    def started(self,request):
        self.requests[request]=self.epoch
        try:
            host=urlsplit(request.url).hostname or ''
            data=request.post_data_json
            chain=data.get('uplink_body',{}).get('pull_singe_chain_uplink_body',{})
            if (host=='dola.com' or host.endswith('.dola.com')) and data.get('cmd')==3100 and chain.get('conversation_id'):
                self.history_request=(request.url,data)
        except Exception:pass

    def pull_history(self,conversation):
        # Replay only the observed read-history request in the same session.
        if not self.history_request or time.monotonic()-self.last_pull<15:return
        self.last_pull=time.monotonic()
        endpoint,data=self.history_request
        chain=data['uplink_body']['pull_singe_chain_uplink_body']
        if str(chain['conversation_id'])!=conversation:return
        response=None
        try:
            response=self.page.context.request.post(endpoint,data=data,timeout=10000,max_redirects=0)
            if response.ok:
                body=response.body()
                if len(body)<=MAX_BODY:
                    payload=json.loads(body)
                    from dola_fallback import fallback_urls
                    for endpoint in fallback_urls(payload):self.fallbacks[endpoint]=conversation
                    for source in extract_sources(payload,conversation,allow_model=True):
                        if source not in self.sources:self.sources.append(source)
        except Exception:pass
        finally:
            if response is not None:response.dispose()

    def failed(self,request):
        self.requests.pop(request,None)

    def finished(self,request):
        if self.requests.pop(request,None)==self.epoch:self.pending.append(request)

    def drain(self):
        for _ in range(min(8,len(self.pending))):
            request=self.pending.popleft()
            try:
                response=request.response()
                if response is None or response.status not in (200,206):continue
                mime=response.headers.get('content-type','').lower()
                if 'video/' in mime:
                    if response.url not in self.media_urls:self.media_urls.append(response.url)
                    continue
                host=urlsplit(response.url).hostname or ''
                if host!='dola.com' and not host.endswith('.dola.com'):continue
                if not any(kind in mime for kind in ('json','event-stream')):continue
                if int(response.headers.get('content-length','0'))>MAX_BODY:continue
                body=response.body()
                if len(body)>MAX_BODY:continue
                text=body.decode('utf-8','replace')
                payloads=[]
                if 'event-stream' in mime:
                    for block in re.split(r'\r?\n\r?\n',text):
                        data='\n'.join(line[5:].lstrip() for line in block.splitlines() if line.startswith('data:'))
                        if data and data!='[DONE]':
                            try:payloads.append(json.loads(data))
                            except ValueError:pass
                else:payloads=[json.loads(text)]
                query=parse_qs(urlsplit(request.url).query)
                cid=next((query[k][0] for k in ('conversation_id','chat_id') if query.get(k)), '')
                if not cid:
                    try:
                        data=request.post_data_json
                        if isinstance(data,dict):cid=str(data.get('conversation_id') or data.get('chat_id') or '')
                    except Exception:pass
                for payload in payloads:
                    from dola_fallback import fallback_urls
                    for endpoint in fallback_urls(payload):self.fallbacks[endpoint]=cid
                    for source in extract_sources(payload,cid,allow_model=True):
                        if source not in self.sources:self.sources.append(source)
                self.sources=self.sources[-256:];self.media_urls=self.media_urls[-100:]
            except Exception:
                # A finished response can disappear during navigation; retry on
                # the next observed response rather than logging signed URLs.
                self.parse_failures+=1

    def resolved_clean(self,conversation):
        from dola_fallback import fallback_urls, resolve
        self.drain()
        if time.monotonic()-self.last_fallback<10:
            return [s for s in self.sources if s.conversation==conversation and s.evidence=='fallback_api:confirmed_unwatermarked']
        self.last_fallback=time.monotonic()
        for endpoint in fallback_urls(self.page.content()):self.fallbacks.setdefault(endpoint,conversation)
        for endpoint,cid in list(self.fallbacks.items()):
            if cid and cid!=conversation:continue
            if self.fallback_attempts.get(endpoint,0)>=3:continue
            self.fallback_attempts[endpoint]=self.fallback_attempts.get(endpoint,0)+1
            try:
                for source in resolve(self.page.context.request,endpoint,conversation):
                    if source not in self.sources:self.sources.append(source)
            except Exception:pass
        return [s for s in self.sources if s.conversation==conversation and s.evidence=='fallback_api:confirmed_unwatermarked']

    def candidates(self,conversation,player_urls,allow_model=False):
        self.drain()
        if allow_model:self.pull_history(conversation)
        self.resolved_clean(conversation)
        sources=self.sources if allow_model else [s for s in self.sources if s.evidence!='video_model:main_url (unverified)']
        return select_sources(sources,conversation,player_urls)

    def close(self):
        for event,handler in [('request',self.started),('requestfinished',self.finished),('requestfailed',self.failed),('response',self.responded)]:
            self.page.remove_listener(event,handler)
        self.requests.clear();self.pending.clear()


def has_video_result(states, links, network_urls, card_texts=()):
    """A tile/placeholder alone does not mean generation returned media."""
    if any(s.get('ready') for s in states):return True
    # Dola sometimes renders a completed asset as a product card instead of a
    # <video>/<a>. The card title (for example, "corporate_betrayal_15s.mp4")
    # is enough to trigger the refresh that exposes its VOD metadata.
    if any(re.search(r'\.(?:mp4|webm|mov)\b', str(text), re.I) for text in card_texts):
        return True
    urls=list(links)+list(network_urls)+[s.get('url','') for s in states]
    for value in urls:
        url=media_url(value)
        if not url:continue
        parts=urlsplit(url)
        if '/video/' in parts.path or parts.path.lower().endswith(('.mp4','.webm','.m3u8')) or 'video_mp4' in parts.query:
            return True
    return False
