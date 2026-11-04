"""Load and verify the local Seedance Pro bundle (with legacy fallbacks)."""
import json
from pathlib import Path
import sys
import time

from engine import Cancelled, app_dir


def extension_dir():
    if getattr(sys, 'frozen', False) or '__compiled__' in globals():  # PyInstaller hoặc Nuitka
        roots = [app_dir(), Path(sys.executable).parent, Path(getattr(sys, '_MEIPASS', Path(__file__).parent)),
                 Path(__file__).resolve().parent]
    else:
        roots = [Path(__file__).resolve().parent]
    folders = [root / 'extensions' / name for root in roots for name in ('pro', 'dola30', 'StudioRelay')]
    for folder in folders:
        if not (folder / 'manifest.json').is_file():
            continue
        try:
            manifest = json.loads((folder / 'manifest.json').read_text('utf-8-sig'))
            if not manifest.get('name') or manifest.get('manifest_version') != 3:
                raise ValueError()
            files = [manifest['background']['service_worker']]
            for script in manifest.get('content_scripts', []):
                files.extend(script.get('js', []) + script.get('css', []))
            for filename in files:
                path = (folder / filename).resolve()
                if not path.is_relative_to(folder.resolve()) or not path.is_file():
                    raise ValueError()
        except (OSError, ValueError, KeyError, TypeError):
            raise RuntimeError(f'Extension thiếu/hỏng file trong {folder}. Chưa gửi prompt.') from None
        if folder.resolve() == (app_dir() / 'extensions' / 'pro').resolve():
            from bundle_integrity import verify_component
            verify_component('extension')
        return folder.resolve()
    raise RuntimeError('Thiếu extension: cần thư mục extensions/pro cạnh mã nguồn/EXE.')


def launch_options(video_session, enabled=True):
    """Caller supplies whether this browser session should load extensions."""
    if not (video_session and enabled):
        return {'args': ['--disable-extensions']}
    folder = extension_dir()
    return {'args': [f'--disable-extensions-except={folder}', f'--load-extension={folder}'],
            'ignore_default_args': ['--disable-extensions']}


def verify_loaded(context, cancel=None, report=None, timeout=20):
    folder = extension_dir()
    expected = json.loads((folder / 'manifest.json').read_text('utf-8-sig'))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cancel is not None and cancel.is_set():
            raise Cancelled('Đã dừng mở phiên video.')
        for worker in context.service_workers:
            if not worker.url.startswith('chrome-extension://'):
                continue
            try:
                manifest = worker.evaluate('chrome.runtime.getManifest()')
            except Exception:
                continue
            if manifest.get('name') == expected['name'] and manifest.get('version') == expected['version']:
                if report:
                    report(f"Đã xác nhận nạp {manifest['name']} {manifest['version']} trong Chrome.")
                return
        if not context.pages:
            raise RuntimeError('Chrome đã đóng trước khi nạp extension.')
        context.pages[0].wait_for_timeout(50)
    raise RuntimeError(f"Chrome chưa nạp {expected['name']} sau {timeout} giây; chưa gửi prompt.")
