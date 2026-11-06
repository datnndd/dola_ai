"""Read-only Dola conversation polling and proxy-bound streamed MP4 downloads."""
import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import json
from pathlib import Path
import re
import struct
import time
from urllib.parse import urljoin

from dola_media import media_url, watermark_status
from engine import Cancelled
from task_runtime import VideoDownloadError, ensure_task_time, task_timeout_seconds


def _run_io(factory):
    # Sync Patchright executes inside its own running asyncio loop/greenlet.
    # Give aiohttp an independent thread and loop instead of nesting run().
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix='seedance-download') as pool:
        return pool.submit(lambda: asyncio.run(factory())).result()


READ_JS = r'''async ({conversationId, msToken, fp, endpoint}) => {
  const batch = endpoint !== "single";
  const version = batch ? "3.32.62" : "3.32.61";
  const params = new URLSearchParams({version_code:"20800",language:"ja",device_platform:"web",
    doubao_device_platform:"web",aid:"495671",real_aid:"495671",pkg_type:"release_version",
    pc_version:version,doubao_pc_version:version,region:"JP",sys_region:"JP",samantha_web:"1",
    web_platform:"browser","use-olympus-account":"1",web_tab_id:crypto.randomUUID()});
  if (batch && msToken) params.set("msToken",msToken);
  if (batch && fp) params.set("fp",fp);
  const uplink = batch ? {batch_pull_singe_chain_uplink_body:{conversation_type:3,direction:3,
    limit:20,params:[{conversation_id:conversationId}],evaluate_ab_params:"",evaluate_common_params:"",ext:{}}}
    : {pull_singe_chain_uplink_body:{conversation_id:conversationId,anchor_index:Number.MAX_SAFE_INTEGER,
      conversation_type:3,direction:1,limit:20,ext:{},filter:{index_list:[]},evaluate_ab_params:"",evaluate_common_params:""}};
  const controller = new AbortController();
  const timer = setTimeout(()=>controller.abort(),15000);
  try {
    const response = await fetch((batch?"/im/chain/batch_single?":"/im/chain/single?")+params,{
      method:"POST",headers:{"Content-Type":"application/json; encoding=utf-8","agw-js-conv":"str","Accept":"*/*"},
      body:JSON.stringify({cmd:batch?3101:3100,uplink_body:uplink,sequence_id:crypto.randomUUID(),channel:2,version:"1"}),
      credentials:"include",signal:controller.signal});
    if (!response.ok) return {http_status:response.status};
    const text = await response.text();
    if (text.length>16*1024*1024) throw new Error("Conversation response too large");
    return {http_status:response.status,payload:JSON.parse(text)};
  } finally {clearTimeout(timer);}
}'''


@dataclass(frozen=True)
class Candidate:
    url: str
    source: str
    bitrate: float = 0


def _object(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            try:
                return json.loads(base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_'))
            except (ValueError, UnicodeError):
                return {}
    return value


def model_candidates(model):
    model = _object(model)
    if not isinstance(model, dict):
        return []
    entries = model.get('video_list') or {}
    entries = entries.values() if isinstance(entries, dict) else entries if isinstance(entries, list) else []
    result = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        url = media_url(item.get('main_url'))
        if not url or watermark_status(url) == 'marked':
            continue
        try:
            bitrate = float(item.get('bitrate') or item.get('real_bitrate') or 0)
        except (TypeError, ValueError):
            bitrate = 0
        result.append(Candidate(url, 'model_main_url', bitrate))
    return sorted(result, key=lambda candidate: candidate.bitrate, reverse=True)


def parse_conversation(payload, conversation, endpoint='batch'):
    if not isinstance(payload, dict) or str(payload.get('status_code', 0)) != '0':
        raise RuntimeError('Dola từ chối đọc hội thoại (mã API không thành công).')
    down = payload.get('downlink_body') or {}
    if endpoint == 'batch':
        entry = (down.get('batch_pull_singe_chain_downlink_body', {}).get('message_map') or {}).get(conversation)
        if not isinstance(entry, dict):
            raise RuntimeError('API chưa trả đúng hội thoại yêu cầu.')
        messages = entry.get('messages') or []
    else:
        messages = down.get('pull_singe_chain_downlink_body', {}).get('messages') or []
    if not isinstance(messages, list):
        raise RuntimeError('Danh sách tin nhắn API không hợp lệ.')
    def number(value):
        try:return float(value or 0)
        except (ValueError, TypeError):return 0
    cutoff = max((number(m.get('create_time')) for m in messages if isinstance(m, dict) and str(m.get('user_type')) == '1'), default=0)
    replies = [m for m in messages if isinstance(m, dict) and str(m.get('user_type')) != '1'
               and (not cutoff or number(m.get('create_time')) >= cutoff)]
    replies.sort(key=lambda message: number(message.get('create_time')))
    state = dict(accepted=False, tool_invoked=False, clarifying=False, finished=False, loading=False,
                 texts=[], failures=[], candidates=[], fallback_apis=[], user_turn=cutoff)
    from dola_fallback import fallback_urls
    # Search only assistant messages from this turn, never user text or older tasks.
    for message in replies:
        ext = _object(message.get('ext') or {})
        ext = ext if isinstance(ext, dict) else {}
        state['accepted'] |= str(ext.get('has_video_gen', '')) == '1'
        state['clarifying'] = str(ext.get('is_creation_clarifying', '')) == '1'
        state['finished'] = str(ext.get('is_finish', '')) == '1'
        code = str(ext.get('ai_creation_res_code') or '')
        if code and code != '0':state['failures'].append(code)
        tools = _object(ext.get('ai_creation_tool_list') or [])
        for tool in tools if isinstance(tools, list) else []:
            if isinstance(tool, dict) and tool.get('fail_code') and str(tool['fail_code']) != '0':
                state['failures'].append(str(tool['fail_code']))
        content = _object(message.get('content') or [])
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):continue
            data = block.get('content') or {}
            text = (data.get('text_block') or {}).get('text')
            if isinstance(text, str):state['texts'].append(text)
            if block.get('block_type') == 10101:state['loading'] = True
            if block.get('block_type') == 10024:
                tool = data.get('generic_tool_block') or {}
                if tool.get('tool_name') in ('text_to_video', 'image_to_video'):
                    state['tool_invoked'] = True
            # Current Dola Agent Hub delivers an MP4 artifact instead of a
            # legacy creation_block. Only use its structured file URL, never
            # execute or scrape commands from file_operation/thinking blocks.
            if block.get('block_type') == 10020:
                file = data.get('file_block') or {}
                is_video = str(file.get('type', '')).lower() == 'mp4' or str(file.get('name', '')).lower().endswith('.mp4')
                url = media_url(file.get('url'))
                if is_video and url:
                    state['candidates'].append(Candidate(url, 'dola_download_url'))
            if block.get('block_type') != 2074:continue
            for creation in (data.get('creation_block') or {}).get('creations') or []:
                if not isinstance(creation, dict) or creation.get('type') != 2:continue
                video = creation.get('video') or {}
                state['candidates'].extend(model_candidates(video.get('video_model')))
                url = media_url(video.get('download_url'))
                if url:state['candidates'].append(Candidate(url, 'dola_download_url'))
        state['fallback_apis'].extend(fallback_urls(message))
    state['candidates'] = sorted(set(state['candidates']), key=lambda c: (c.source == 'dola_download_url', -c.bitrate))
    state['text'] = '\n'.join(state.pop('texts'))
    return state


class ConversationReader:
    def __init__(self, page, conversation):
        if not re.fullmatch(r'\d+', conversation):raise ValueError('Mã hội thoại không hợp lệ.')
        self.page, self.conversation = page, conversation
        self.endpoint = 'batch'

    def read(self):
        cookies = {c['name']:c['value'] for c in self.page.context.cookies()
                   if c.get('domain', '').lstrip('.') in ('dola.com', 'www.dola.com')}
        errors = []
        for endpoint in (self.endpoint, 'single' if self.endpoint == 'batch' else 'batch'):
            try:
                result = self.page.evaluate(READ_JS, dict(conversationId=self.conversation,
                    msToken=cookies.get('msToken', ''), fp=cookies.get('s_v_web_id', ''), endpoint=endpoint))
                status = result.get('http_status')
                if status in (401, 403):raise PermissionError('Phiên Dola bị từ chối khi đọc API.')
                if status != 200:raise RuntimeError(f'API đọc hội thoại trả HTTP {status}.')
                state = parse_conversation(result.get('payload'), self.conversation, endpoint)
                self.endpoint = endpoint
                return state
            except PermissionError:raise
            except Exception as error:
                if self.page.is_closed():raise RuntimeError('Tab theo dõi Dola đã đóng; không gửi lại prompt.') from None
                errors.append(type(error).__name__)
        raise RuntimeError('Chưa đọc được API hội thoại (' + ', '.join(errors) + ').')


def mp4_duration(path):
    size = Path(path).stat().st_size
    with open(path, 'rb') as stream:
        def boxes(start, end):
            position = start
            for _ in range(10000):
                if position + 8 > end:return
                stream.seek(position)
                length, kind = struct.unpack('>I4s', stream.read(8)); header = 8
                if length == 1:
                    if position + 16 > end:return
                    length = struct.unpack('>Q', stream.read(8))[0]; header = 16
                if length == 0:length = end - position
                if length < header or position + length > end:return
                yield kind, position + header, position + length
                position += length
        for kind, start, end in boxes(0, size):
            if kind != b'moov':continue
            for child, data, limit in boxes(start, end):
                if child != b'mvhd':continue
                stream.seek(data)
                version = stream.read(1)
                offset, length, pattern = (20, 12, '>IQ') if version == b'\x01' else (12, 8, '>II')
                if data + offset + length > limit:return 0
                stream.seek(data + offset)
                scale, duration = struct.unpack(pattern, stream.read(length))
                return duration / scale if scale else 0
    return 0


async def _stream(url, target, proxy, referer, cancel, settings):
    import aiohttp
    target = Path(target)
    part = target.with_name(target.name + '.part')
    created = False
    if target.exists():raise VideoDownloadError('File đích đã tồn tại; không ghi đè.')
    proxy_url = proxy.get('server') if proxy else None
    auth = aiohttp.BasicAuth(proxy.get('username', ''), proxy.get('password', '')) if proxy and proxy.get('username') else None
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=20, sock_read=30)
    try:
        async with aiohttp.ClientSession(timeout=timeout, trust_env=False, cookie_jar=aiohttp.DummyCookieJar()) as session:
            current = url
            for redirect in range(4):
                if not media_url(current):raise VideoDownloadError('URL video không hợp lệ.')
                async with session.get(current, proxy=proxy_url, proxy_auth=auth,
                    headers={'Referer':referer, 'Accept':'*/*'}, allow_redirects=False) as response:
                    if response.status in (301,302,303,307,308):
                        location = response.headers.get('Location')
                        if not location or redirect == 3:raise VideoDownloadError('Chuyển hướng video không hợp lệ.')
                        current = urljoin(current, location);continue
                    response.raise_for_status()
                    expected = response.content_length
                    total = 0
                    prefix = bytearray()
                    with part.open('xb') as file:
                        created = True
                        async for chunk in response.content.iter_chunked(128*1024):
                            if cancel.is_set():raise Cancelled('Đã dừng tải video.')
                            ensure_task_time(settings)
                            if len(prefix) < 64:prefix.extend(chunk[:64-len(prefix)])
                            file.write(chunk);total += len(chunk)
                    if not total or (expected is not None and total != expected):
                        raise VideoDownloadError('Video tải thiếu dữ liệu.')
                    if b'ftyp' not in prefix:raise VideoDownloadError('Phản hồi tải không phải MP4.')
                    duration = mp4_duration(part)
                    part.rename(target)
                    return duration
            raise VideoDownloadError('Không nhận được file video.')
    finally:
        if created and part.exists():part.unlink()


def download(url, target, proxy, referer, cancel, settings):
    async def controlled():
        job = asyncio.create_task(_stream(url, target, proxy, referer, cancel, settings))
        try:
            while not job.done():
                await asyncio.wait({job}, timeout=.2)
                if cancel.is_set():raise Cancelled('Đã dừng tải video.')
                ensure_task_time(settings)
            return await job
        finally:
            if not job.done():
                job.cancel()
                try:await job
                except asyncio.CancelledError:pass
    return _run_io(controlled)


def resolve_fallbacks(endpoints, proxy, conversation, cancel, settings):
    if not endpoints:
        return []
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
    from dola_fallback import clean_sources, fallback_urls
    async def resolve_all():
        import aiohttp
        results = []
        auth = aiohttp.BasicAuth(proxy.get('username', ''), proxy.get('password', '')) if proxy and proxy.get('username') else None
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20), trust_env=False,
                                        cookie_jar=aiohttp.DummyCookieJar()) as session:
            for endpoint in list(dict.fromkeys(endpoints))[:4]:
                if cancel.is_set():raise Cancelled('Đã dừng lấy nguồn video.')
                ensure_task_time(settings)
                if endpoint not in fallback_urls({'fallback_api':endpoint}):continue
                parsed = urlsplit(endpoint)
                query = dict(parse_qsl(parsed.query, keep_blank_values=True))
                query.update(logo_type='unwatermarked', codec_type='8', channel='no')
                target = urlunsplit(parsed._replace(query=urlencode(query)))
                try:
                    async with session.get(target, proxy=(proxy or {}).get('server'), proxy_auth=auth,
                                           allow_redirects=False) as response:
                        if response.status != 200:continue
                        body = await response.content.read(8*1024*1024 + 1)
                        if len(body)>8*1024*1024:continue
                        for source in clean_sources(json.loads(body), conversation):
                            results.append(Candidate(source.url, 'fallback_unwatermarked'))
                except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                    continue
        return results
    return _run_io(resolve_all)


def _ui_reply_finished(page, baseline):
    """Accept only a completed assistant turn created after ``baseline``."""
    from dola_browser import action_bar_count, current_assistant_reply, latest_turn
    if isinstance(baseline, dict):
        assistant = tuple(baseline.get('assistant') or (0, ''))
        old_shares = int(baseline.get('shares') or 0)
    else:
        assistant = tuple(baseline or (0, ''))
        old_shares = 0
    finished, text = latest_turn(page)
    fresh_assistant, fresh_text = current_assistant_reply(page, assistant)
    fresh_share = action_bar_count(page) > old_shares
    return bool(finished and (fresh_share or fresh_assistant)), text or fresh_text


def _ui_baseline(page):
    from dola_browser import action_bar_count, assistant_snapshot
    return {'assistant': assistant_snapshot(page), 'shares': action_bar_count(page)}


def follow_conversation(page, context, url, source, settings, cancel, report, steps, *, resume=False,
                        ui_baseline=None):
    """Yield between polls. Never create/resubmit a video during recovery."""
    settings = dict(settings)
    if settings.get('_task_deadline') is None:
        settings['_task_deadline'] = time.monotonic() + task_timeout_seconds(settings)
    from dola_browser import policy_blocked
    from task_runtime import PolicyBlocked
    conversation = url.rsplit('/', 1)[-1]
    reader = ConversationReader(page, conversation)
    proxy = settings.get('_browser_proxy', getattr(context, '_seedance_proxy', {})) or {}
    accepted = False
    attempts = 0
    clarified = False
    replied_since = None
    announced = None
    started = time.monotonic()
    last_report = 0
    ui_complete = ui_baseline is None
    ui_finished_at = None
    ui_text = ''
    if ui_complete:
        report('Theo dõi kết quả bằng API hội thoại; không tự F5.')
    else:
        report('Chờ nút Share của lượt trả lời mới; sau khi hoàn tất mới đọc API để phân loại video hoặc chữ.')
    report(f"Giới hạn task theo cấu hình: {task_timeout_seconds(settings)} giây; còn khoảng {max(0, int(settings['_task_deadline'] - time.monotonic()))} giây. Không áp dụng mốc chờ 180 giây riêng.")
    while True:
        yield
        if cancel.is_set():raise Cancelled('Đã dừng theo dõi video.')
        ensure_task_time(settings, report)
        if not ui_complete:
            ui_complete, ui_text = _ui_reply_finished(page, ui_baseline)
            if not ui_complete:
                if time.monotonic() - last_report >= 20:
                    report(f'Chưa thấy nút Share của lượt mới; Dola vẫn đang trả lời. ({int(time.monotonic() - started)}s)')
                    last_report = time.monotonic()
                steps.wait(2)
                continue
            ui_finished_at = time.monotonic()
            report('Đã thấy nút Share của lượt mới; câu trả lời đã xong. Đang đọc API hội thoại để phân loại kết quả.')
        try:
            state = reader.read()
            attempts = 0
        except PermissionError:
            raise RuntimeError(f'Phiên Dola bị từ chối khi đọc {url}; đã gửi prompt nên không gửi lại.') from None
        except Exception as error:
            if page.is_closed():raise RuntimeError('Tab theo dõi đã đóng; dùng Lấy video, không gửi lại prompt.') from None
            attempts += 1
            delay = min(30, 2 ** min(attempts, 5))
            report(f'API đọc hội thoại tạm lỗi; chờ {delay}s rồi đọc lại, không gửi lại prompt.')
            steps.wait(delay)
            continue
        if state.get('user_turn', 0) <= settings.get('_after_user_turn', -1):
            steps.wait(3)
            continue
        if policy_blocked(state['text']):
            raise PolicyBlocked('Dola từ chối nội dung; không gửi lại prompt.')
        if state['candidates']:
            kinds = ', '.join(dict.fromkeys(candidate.source for candidate in state['candidates']))
            report(f'API xác nhận câu trả lời có video ({kinds}); đang lấy nguồn tải.')
            candidates = resolve_fallbacks(state['fallback_apis'], proxy, conversation, cancel, settings)
            candidates.extend(state['candidates'])
            # Always prefer a confirmed clean source. By default, if Dola only
            # returns its service MP4, keep the job usable and fall back to it.
            strict = (settings.get('video_source') == 'no_logo'
                      and not settings.get('fallback_to_dola_link', True))
            if strict:
                candidates = [c for c in candidates if c.source == 'fallback_unwatermarked' or watermark_status(c.url) == 'clean']
            if not candidates:
                raise VideoDownloadError('Video đã tạo nhưng chưa có nguồn được xác nhận không logo. Dùng Lấy video hoặc chọn ưu tiên không logo có dự phòng.')
            seen = set()
            for candidate in candidates:
                if candidate.url in seen:continue
                seen.add(candidate.url)
                status = watermark_status(candidate.url)
                if candidate.source == 'dola_download_url':
                    report('Dùng link tải Dola trả về; video có thể có logo.')
                else:
                    report('Đang tải nguồn video ưu tiên từ metadata Dola qua proxy của profile.')
                try:
                    duration = download(candidate.url, source, proxy, url, cancel, settings)
                    report(f'Đã tải MP4 hoàn chỉnh ({duration:.2f}s); chuyển sang kiểm tra/xuất file.')
                    return dict(mode='conversation_api', evidence=candidate.source, watermark_status=status,
                                duration_seconds=duration)
                except Cancelled:raise
                except Exception as error:
                    ensure_task_time(settings, report)
                    status_code = getattr(error, 'status', None)
                    detail = f' HTTP {status_code}' if status_code else ''
                    report(f'Nguồn tải lỗi {type(error).__name__}{detail}; thử nguồn kế tiếp trong cùng kết quả API.')
            raise VideoDownloadError(f'Dola đã tạo video nhưng các nguồn tải đều lỗi. Dùng Lấy video từ {url}; không tạo lại.')
        accepted = accepted or state['accepted'] or state.get('tool_invoked', False)
        if state['failures'] and state['finished'] and not state['loading']:
            raise RuntimeError('Dola báo tạo video thất bại (mã ' + ','.join(dict.fromkeys(state['failures'])) + '); không gửi lại prompt.')
        if state['clarifying'] and not accepted:
            from seedance_ui import clarification, EDITOR
            answer = clarification(state['text'], settings)
            if resume or clarified or not answer:
                raise RuntimeError('Dola hỏi thêm thay vì bắt đầu tạo video; không tự gửi lại yêu cầu.')
            report('API cho biết Dola hỏi thời lượng; trả lời một lần bằng thông số đã chọn.')
            editor = page.locator(EDITOR).first
            ui_baseline = _ui_baseline(page)
            ui_complete = False
            ui_finished_at = None
            steps.type_prompt(editor, answer)
            steps.submit(editor)
            clarified = True
            steps.wait(3)
            continue
        # Share proves the assistant turn is complete. Allow a short API sync
        # window, then report a real text-only result instead of hanging until
        # the full task timeout. A started video tool still keeps waiting.
        if (ui_finished_at is not None and time.monotonic() - ui_finished_at >= 12
                and not accepted and not state.get('tool_invoked') and not state['loading']):
            summary = ' '.join((state.get('text') or ui_text or '').split())
            from dola_limits import is_work_limit_reached
            if str(settings.get('dola_mode', 'pro')).strip().lower() == 'pro' and not settings.get('_work_fallback_done') and is_work_limit_reached(summary):
                from dola_limits import retry_after
                until = retry_after(summary)
                report(f'[Khóa Work] {until or ""}')
                report('Chế độ Work đã đạt giới hạn; tự động chuyển sang chế độ Chat bình thường và tiếp tục tạo video…')
                settings['dola_mode'] = 'fast'
                settings['_work_fallback_done'] = True
                from seedance_ui import select_mode as ui_select_mode, EDITOR
                from dola_browser import build_prompt
                ui_select_mode(page, 'fast', steps)
                editor = page.locator(EDITOR).first
                steps.type_prompt(editor, build_prompt(dict(prompt=state.get('text') or ''), settings))
                steps.submit(editor)
                ui_baseline = _ui_baseline(page)
                ui_complete = False
                ui_finished_at = None
                accepted = False
                report('Đã gửi lại yêu cầu bằng chế độ Chat; tiếp tục chờ video…')
                steps.wait(5)
                continue
            if len(summary) > 180:
                summary = summary[:177] + '...'
            detail = f' Nội dung: {summary}' if summary else ''
            raise RuntimeError('Dola đã trả lời xong (có nút Share) nhưng chỉ trả lời chữ, không có video trong API; không gửi lại prompt.' + detail)
        # The Pro extension keeps waiting after a text turn: Dola can publish
        # the video tool/result later. Stop only on explicit failure/policy or
        # the task's configured deadline, never after an arbitrary 12 seconds.
        label = ('Dola đang thực hiện công cụ tạo video; đang chờ kết quả API.'
                 if state.get('tool_invoked') and not state['accepted'] else
                 'API xác nhận Dola đã nhận tạo video; đang chờ kết quả.'
                 if accepted else 'Đã gửi prompt; đang chờ API xác nhận tạo video.')
        if announced != label or time.monotonic() - last_report >= 20:
            report(label + f' ({int(time.monotonic() - started)}s)')
            announced = label
            last_report = time.monotonic()
        steps.wait(8 if accepted else 3)
