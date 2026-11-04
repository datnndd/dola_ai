"""Independent local renderer and explicit Python adapter protocol."""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid

ORIGINAL = Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs' / '3 BIG Studio'
CREATE_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


class Cancelled(Exception):
    pass


def app_dir():
    """Thư mục chứa EXE thật (PyInstaller/Nuitka onefile), hoặc thư mục mã nguồn khi chạy dev."""
    main = sys.modules.get('__main__')
    containing = getattr(getattr(main, '__compiled__', None), 'containing_dir', None)
    if containing:
        return Path(containing)
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


def find_ffmpeg(configured=''):
    home = app_dir()
    bundled = [home / 'ffmpeg.exe', home / 'browser' / 'ffmpeg.exe']
    for path in bundled:
        if path.is_file():
            from bundle_integrity import verify_component
            verify_component('ffmpeg')
            return str(path.resolve())
    candidates = [configured, shutil.which('ffmpeg'), str(ORIGINAL / 'ffmpeg.exe')]
    return next((str(Path(p).resolve()) for p in candidates if p and Path(p).is_file()), '')


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def run_process(args, cancel, timeout=1800, stdin_text=None):
    """Spool pipes to files to prevent deadlocks with verbose child processes."""
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        with tempfile.TemporaryFile() as inp:
            if stdin_text is not None:
                inp.write(stdin_text.encode('utf-8'))
                inp.seek(0)
            proc = subprocess.Popen(args, stdin=inp, stdout=out, stderr=err,
                                    creationflags=CREATE_NO_WINDOW)
            start = time.monotonic()
            try:
                while proc.poll() is None:
                    if cancel.wait(.1):
                        raise Cancelled('Đã hủy bởi người dùng')
                    if time.monotonic() - start > timeout:
                        raise TimeoutError('Quá thời gian xử lý tác vụ')
            finally:
                if proc.poll() is None:
                    if os.name == 'nt':
                        subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'],
                                       capture_output=True, creationflags=CREATE_NO_WINDOW)
                    else:
                        proc.kill()
                    proc.wait(timeout=15)
            if cancel.is_set():
                raise Cancelled('Đã hủy bởi người dùng')
            out.seek(0)
            err.seek(0, 2)
            err.seek(max(0, err.tell() - 3000))
            error = err.read().decode('utf-8', 'replace')
            if proc.returncode:
                raise RuntimeError(error[-1500:] or f'Tiến trình trả mã {proc.returncode}')
            return out.read(1024 * 1024).decode('utf-8', 'replace')


def video_resolution(path, ffmpeg=''):
    """(width, height) of a video, or None. Diagnostic only; never raises."""
    exe = find_ffmpeg(ffmpeg)
    if not exe:
        return None
    try:
        probe = Path(exe).with_name('ffprobe' + Path(exe).suffix)
        if probe.is_file():
            out = subprocess.run([str(probe), '-v', 'error', '-select_streams', 'v:0',
                                  '-show_entries', 'stream=width,height', '-of', 'csv=p=0:s=x', str(path)],
                                 capture_output=True, text=True, timeout=30, creationflags=CREATE_NO_WINDOW)
            match = re.search(r'(\d{2,5})x(\d{2,5})', out.stdout)
            if match:
                return int(match.group(1)), int(match.group(2))
        # Fall back to parsing `ffmpeg -i` stream info from stderr.
        out = subprocess.run([exe, '-hide_banner', '-i', str(path)],
                             capture_output=True, text=True, timeout=30, creationflags=CREATE_NO_WINDOW)
        match = re.search(r'Video:.*?[,\s](\d{2,5})x(\d{2,5})', out.stderr)
        if match:
            return int(match.group(1)), int(match.group(2))
    except Exception:
        return None
    return None


def dimensions(ratio, upscale):
    short = 1080 if upscale else 720
    long = 1920 if upscale else 1280
    return {'9:16': (short, long), '16:9': (long, short),
            '1:1': (short, short), '4:3': (1440 if upscale else 960, short),
            '3:4': (short, 1440 if upscale else 960)}[ratio]


def render_local(task, settings, cancel, report):
    ffmpeg = find_ffmpeg(settings.get('ffmpeg', ''))
    if not ffmpeg:
        raise ValueError('Chưa tìm thấy FFmpeg. Chọn ffmpeg.exe trong Cài đặt.')
    images = task.get('images', [])
    if not images:
        raise ValueError('Xuất video từ ảnh cần ít nhất 1 ảnh tham chiếu. Prompt không tạo ảnh AI ở chế độ này.')
    if len(images) > 10 or any(not Path(p).is_file() for p in images):
        raise ValueError('Ảnh không tồn tại hoặc vượt giới hạn 10 ảnh.')
    w, h = dimensions(settings['ratio'], settings['upscale'])
    duration = int(settings['duration'])
    output = Path(settings['output'])
    output.mkdir(parents=True, exist_ok=True)
    result = output / f"local_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.mp4"
    # Every image is rendered at the exact same codec/timebase, then concatenated.
    # Integer frame distribution guarantees the requested total duration.
    frames, remainder = divmod(duration * 24, len(images))
    with tempfile.TemporaryDirectory(prefix='studio_render_') as temp:
        folder = Path(temp)
        for i, source in enumerate(images):
            if cancel.is_set():
                raise Cancelled()
            report(f'Đang dựng ảnh {i + 1}/{len(images)}')
            vf = (f'scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos,'
                  f'pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=0x0B1019,setsar=1')
            run_process([ffmpeg, '-hide_banner', '-loglevel', 'error', '-y',
                         '-loop', '1', '-framerate', '24', '-i', source, '-vf', vf,
                         '-frames:v', str(frames + (i < remainder)), '-an', '-c:v',
                         'libx264', '-preset', 'veryfast', '-crf', '20', '-threads', '2',
                         '-pix_fmt', 'yuv420p', str(folder / f'{i}.mp4')], cancel)
        (folder / 'concat.txt').write_text(''.join(f"file '{i}.mp4'\n" for i in range(len(images))), encoding='utf-8')
        temp_result = folder / 'final.mp4'
        run_process([ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-f', 'concat',
                     '-safe', '1', '-i', str(folder / 'concat.txt'), '-c', 'copy',
                     '-movflags', '+faststart', str(temp_result)], cancel)
        if cancel.is_set():
            raise Cancelled()
        shutil.copy2(temp_result, result)
    atomic_json(result.with_suffix('.json'), {'task': task, 'settings': settings,
                'engine': 'local-slideshow', 'note': 'Video ghép ảnh cục bộ, không phải video AI.'})
    return str(result)


def render_adapter(task, settings, account, cancel, report):
    script = Path(settings.get('adapter', ''))
    interpreter = settings.get('python', '')
    if not script.is_file() or script.suffix.lower() != '.py':
        raise ValueError('Chưa cấu hình adapter Python cho dịch vụ AI trong Cài đặt.')
    if not interpreter or not Path(interpreter).is_file():
        raise ValueError('Cần chọn python.exe để chạy adapter.')
    report(f"Gửi task tới adapter • {account['name']}")
    payload = {'protocol': 1, 'task': task, 'settings': settings,
               'account': {'id': account['id'], 'name': account['name']}}
    raw = run_process([interpreter, str(script.resolve())], cancel,
                      timeout=int(settings.get('timeout', 1800)),
                      stdin_text=json.dumps(payload, ensure_ascii=False))
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ValueError('Adapter phải trả một JSON duy nhất trên stdout.') from exc
    if data.get('error'):
        raise RuntimeError(str(data['error']))
    video = Path(data.get('video_path', ''))
    if not video.is_absolute() or not video.is_file() or video.suffix.lower() != '.mp4' or video.stat().st_size == 0:
        raise ValueError('Adapter chưa trả đường dẫn tuyệt đối tới tệp MP4 hợp lệ.')
    output = Path(settings['output'])
    output.mkdir(parents=True, exist_ok=True)
    if video.parent.resolve() != output.resolve():
        copied = output / f'ai_{uuid.uuid4().hex[:10]}.mp4'
        shutil.copy2(video, copied)
        video = copied
    return str(video)
