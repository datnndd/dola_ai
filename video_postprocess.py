"""Optional, non-destructive FFmpeg crop/blur of downloaded videos."""
from pathlib import Path
import math
import uuid

from engine import Cancelled, dimensions, find_ffmpeg, run_process


def default_video_edit():
    return dict(mode='none', top=0, bottom=8, left=0, right=0,
                x=75, y=85, width=25, height=15)


def validate_video_edit(value=None):
    cfg = default_video_edit()
    cfg.update(value or {})
    if cfg['mode'] not in ('none', 'crop', 'blur'):
        raise ValueError('Chế độ xử lý video không hợp lệ.')
    for key in ('top', 'bottom', 'left', 'right', 'x', 'y', 'width', 'height'):
        cfg[key] = float(cfg[key])
        if not math.isfinite(cfg[key]) or not 0 <= cfg[key] <= 100:
            raise ValueError('Tọa độ và kích thước phải từ 0 đến 100%.')
    if cfg['mode'] == 'crop':
        if cfg['top'] + cfg['bottom'] > 90 or cfg['left'] + cfg['right'] > 90:
            raise ValueError('Phải giữ ít nhất 10% chiều rộng và chiều cao video.')
    if cfg['mode'] == 'blur':
        if min(cfg['width'], cfg['height']) < 1:
            raise ValueError('Vùng làm mờ phải rộng và cao ít nhất 1%.')
        if cfg['x'] + cfg['width'] > 100 or cfg['y'] + cfg['height'] > 100:
            raise ValueError('Vùng làm mờ vượt ra ngoài khung hình.')
    return cfg


def video_filter(settings):
    cfg = validate_video_edit(settings.get('video_edit'))
    def px(axis, percent):
        return f'trunc({axis}*{percent / 100:.8f}/2)*2'
    graph, head = '', '[0:v:0]'
    if cfg['mode'] == 'crop':
        graph = (head + 'crop=' + px('iw', 100-cfg['left']-cfg['right']) + ':'
                 + px('ih', 100-cfg['top']-cfg['bottom']) + ':'
                 + px('iw', cfg['left']) + ':' + px('ih', cfg['top']) + '[edited];')
        head = '[edited]'
    elif cfg['mode'] == 'blur':
        graph = (head + 'split[base][region];[region]crop='
                 + f"'max(2,{px('iw', cfg['width'])})':'max(2,{px('ih', cfg['height'])})':"
                 + px('iw', cfg['x']) + ':' + px('ih', cfg['y'])
                 + ",boxblur=lr='min(20,min(w,h)/4)':lp=2:cr='min(10,min(cw,ch)/4)':cp=2[blur];"
                 + '[base][blur]overlay=' + px('main_w', cfg['x']) + ':'
                 + px('main_h', cfg['y']) + ':shortest=1[edited];')
        head = '[edited]'
    filters = []
    if settings.get('upscale'):
        w, h = dimensions(settings.get('ratio', '9:16'), True)
        # Giống hệt tool 3 BIG: scale lanczos thẳng tới kích thước đích + unsharp 0.8/0.4.
        filters.extend([f'scale={w}:{h}:flags=lanczos', 'unsharp=5:5:0.8:5:5:0.4'])
    else:
        filters.append('scale=trunc(iw/2)*2:trunc(ih/2)*2')
    filters.extend(['setsar=1', 'format=yuv420p'])
    return graph + head + ','.join(filters) + '[out]'


def export_video(source, final, settings, cancel, report):
    """Publish only a completed export; never change or overwrite the source."""
    source, final = Path(source), Path(final)
    if source.resolve() == final.resolve() or final.exists():
        raise ValueError('Chọn tên file xuất mới, khác video gốc và chưa tồn tại.')
    cfg = validate_video_edit(settings.get('video_edit'))
    ffmpeg = find_ffmpeg(settings.get('ffmpeg', ''))
    if not ffmpeg:
        raise ValueError('Chưa tìm thấy FFmpeg. Chọn ffmpeg.exe trong Cài đặt.')
    if cancel.is_set():
        raise Cancelled()
    final.parent.mkdir(parents=True, exist_ok=True)
    part = final.with_name(final.stem + '.' + uuid.uuid4().hex[:8] + '.export.mp4')
    report({'none': 'Đang xuất video bằng FFmpeg…', 'crop': 'FFmpeg đang cắt mép video…',
            'blur': 'FFmpeg đang làm mờ vùng đã chọn…'}[cfg['mode']])
    try:
        run_process([ffmpeg, '-hide_banner', '-loglevel', 'error', '-n', '-i', str(source),
                     '-filter_complex_threads', '1', '-filter_complex', video_filter(settings),
                     '-map', '[out]', '-map', '0:a?', '-c:v', 'libx264', '-preset', 'fast',
                     '-crf', '20', '-threads', '2', '-c:a', 'aac', '-movflags', '+faststart',
                     str(part)], cancel, timeout=int(settings.get('timeout', 1800)))
        if cancel.is_set():
            raise Cancelled()
        # On Windows rename refuses an existing destination, avoiding overwrite races.
        part.rename(final)
    finally:
        if part.exists():
            part.unlink()
    return str(final)
