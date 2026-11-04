"""Reference Pro 30s mode: two sequential 15s turns in the same chat."""
import hashlib
import json
import re
from pathlib import Path

from engine import atomic_json, find_ffmpeg, run_process, video_resolution


def enabled(settings):
    return (str(settings.get('duration', '15')).strip().rstrip('s') == '30'
            and settings.get('thirty_strategy', 'merge') == 'merge')


def prompts(raw):
    raw = re.sub(r'^(?:tạo\s*video|create\s*videos?)\s*[:：]\s*', '', str(raw).strip(), flags=re.I)
    pieces = re.split(r'\s*\|{2,}\s*', raw, maxsplit=1)
    first = pieces[0].strip()
    second = pieces[1].strip() if len(pieces) > 1 else first
    if not first or not second:
        raise ValueError('Prompt 30s thiếu nội dung một trong hai phần; chưa gửi.')
    return (first + '. Đây là phần 1/2 (nửa đầu) của một cảnh liền mạch: mở đầu cảnh và bắt đầu hành động, chưa kết thúc.',
            'Phần 2/2 (nửa sau) của cùng cảnh, tiếp nối liền mạch ngay sau video vừa tạo ở trên, giữ nguyên nhân vật, trang phục, bối cảnh, ánh sáng, màu sắc và phong cách quay: '
            + second + '. Tiếp tục hành động và kết thúc cảnh trọn vẹn.')


def merge(parts, output, settings, cancel):
    ffmpeg = find_ffmpeg(settings.get('ffmpeg', ''))
    if not ffmpeg:
        raise RuntimeError('Cần FFmpeg để ghép 30s; hai phần được giữ lại.')
    size = video_resolution(parts[0], settings.get('ffmpeg', ''))
    if not size:
        raise RuntimeError('Không đọc được kích thước phần 1.')
    # Normalize frame size/timebase; require audio on both inputs before
    # concatenating audio. Missing audio is filled with silence.
    import subprocess
    from engine import CREATE_NO_WINDOW
    audio = []
    for part in parts:
        probe = subprocess.run([ffmpeg, '-hide_banner', '-i', str(part)], capture_output=True,
                               creationflags=CREATE_NO_WINDOW, timeout=30)
        audio.append(b'Audio:' in probe.stderr)
    args = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-y']
    for part in parts: args += ['-i', str(part)]
    filters = []
    for i, part in enumerate(parts):
        from seedance_video import mp4_duration
        duration = mp4_duration(part)
        if not 13 <= duration <= 17:
            raise RuntimeError(f'Phần {i+1} dài {duration:.2f}s, không phải video 15s; giữ file để kiểm tra.')
        w, h = size
        filters.append(f'[{i}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,setpts=PTS-STARTPTS[v{i}]')
        filters.append((f'[{i}:a]aresample=48000,aformat=channel_layouts=stereo,apad,atrim=duration={duration},asetpts=PTS-STARTPTS[a{i}]'
                        if audio[i] else f'anullsrc=r=48000:cl=stereo,atrim=duration={duration},asetpts=PTS-STARTPTS[a{i}]'))
    filters.append('[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]')
    temporary = output.with_suffix('.merging.mp4')
    try:
        run_process(args + ['-filter_complex', ';'.join(filters), '-map', '[v]', '-map', '[a]',
                           '-c:v', 'libx264', '-preset', 'fast', '-crf', '18', '-c:a', 'aac',
                           '-movflags', '+faststart', str(temporary)], cancel)
        run_process([ffmpeg, '-v', 'error', '-xerror', '-i', str(temporary), '-f', 'null', '-'], cancel)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)


def follow(page, context, url, source, settings, task, cancel, report, steps, resume=False,
           ui_baseline=None):
    from seedance_video import ConversationReader, follow_conversation
    from seedance_ui import prompt_text, EDITOR
    key = hashlib.sha256(url.encode()).hexdigest()[:20]
    folder = Path(settings['output']) / '_30s_parts' / key
    folder.mkdir(parents=True, exist_ok=True)
    checkpoint = folder / 'state.json'
    if resume and not checkpoint.exists():
        raise RuntimeError('Hội thoại này chưa có mốc hai phần 30s. Không thể xác định phần nào đã gửi; không tự gửi lại hoặc nhận nhầm video 15s thành 30s.')
    state = json.loads(checkpoint.read_text('utf-8')) if checkpoint.exists() else {'phase': 'part1'}
    atomic_json(checkpoint, state)
    parts = [folder / 'part1.mp4', folder / 'part2.mp4']
    if state['phase'] == 'part1':
        report('30s: chờ và tải phần 1/2 (15 giây).')
        if not parts[0].exists():
            state['part1_provenance'] = yield from follow_conversation(
                page, context, url, parts[0], settings, cancel, report, steps, resume=resume,
                ui_baseline=ui_baseline)
        state['phase'] = 'part1_done'
        atomic_json(checkpoint, state)
    if state['phase'] == 'part1_done':
        if resume:
            raise RuntimeError('Đã lưu phần 1. Lấy kết quả cũ không tự gửi phần 2; cần tiếp tục tạo phần 2 trong hội thoại này.')
        state['after_turn'] = ConversationReader(page, url.rsplit('/', 1)[-1]).read()['user_turn']
        editor = page.locator(EDITOR).first
        from dola_browser import action_bar_count, assistant_snapshot
        state['part2_ui_baseline'] = {
            'assistant': list(assistant_snapshot(page)),
            'shares': action_bar_count(page),
        }
        steps.type_prompt(editor, prompt_text({'prompt': prompts(task['prompt'])[1]}, settings))
        def mark():
            state['phase'] = 'part2_sent'
            atomic_json(checkpoint, state)
            return True
        report('30s: phần 1 xong; gửi phần 2/2 trong cùng hội thoại.')
        steps.submit(editor, should_send=mark)
        steps.wait(5)
    if state['phase'] == 'part2_sent':
        if not parts[1].exists():
            state['part2_provenance'] = yield from follow_conversation(
                page, context, url, parts[1], dict(settings, _after_user_turn=state['after_turn']),
                cancel, report, steps, resume=True,
                ui_baseline=state.get('part2_ui_baseline', {'assistant': (0, ''), 'shares': 0}))
        state['phase'] = 'downloaded'
        atomic_json(checkpoint, state)
    if not all(part.is_file() for part in parts):
        raise RuntimeError('Thiếu file thành phần 30s; không tạo lại tự động.')
    report('30s: đã tải đủ hai phần; đang ghép và kiểm tra MP4.')
    merge(parts, source, settings, cancel)
    from dola_media import provenance_has_logo
    sources = [state.get('part1_provenance', {}), state.get('part2_provenance', {})]
    marked = any(provenance_has_logo(item) for item in sources)
    clean = all(item.get('watermark_status') == 'clean' for item in sources)
    return {'mode': 'merge_2x15', 'parts': 2,
            'watermark_status': 'marked' if marked else 'clean' if clean else 'unknown',
            'evidence': 'dola_download_url' if marked else 'merge_2x15'}
