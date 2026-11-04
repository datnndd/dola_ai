"""Integrity checks for external runtime files shipped beside the compiled EXE."""
from functools import lru_cache
import hashlib
from pathlib import Path
import sys

from bundle_integrity_data import DIGESTS


def _release_root():
    main = sys.modules.get('__main__')
    containing = getattr(getattr(main, '__compiled__', None), 'containing_dir', None)
    if containing:
        return Path(containing)
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


def _is_customer_binary():
    main = sys.modules.get('__main__')
    return bool(getattr(main, '__compiled__', None) or getattr(sys, 'frozen', False))


def tree_digest(path):
    """Stable digest of a file or directory, including names and sizes."""
    path = Path(path)
    files = [path] if path.is_file() else sorted(
        (item for item in path.rglob('*') if item.is_file()),
        key=lambda item: item.relative_to(path).as_posix().casefold())
    digest = hashlib.sha256()
    for item in files:
        relative = item.name if path.is_file() else item.relative_to(path).as_posix()
        digest.update(relative.encode('utf-8'))
        digest.update(b'\0')
        digest.update(str(item.stat().st_size).encode('ascii'))
        digest.update(b'\0')
        with item.open('rb') as stream:
            while True:
                block = stream.read(1024 * 1024)
                if not block:
                    break
                digest.update(block)
        digest.update(b'\0')
    return digest.hexdigest()


COMPONENTS = {
    'browser': Path('browser'),
    'extension': Path('extensions') / 'pro',
    'ffmpeg': Path('ffmpeg.exe'),
    'skill': Path('SKILL.md'),
}


@lru_cache(maxsize=None)
def verify_component(name):
    """Reject missing or modified customer-bundle files; stay inert in source runs."""
    if not _is_customer_binary():
        return True
    expected = DIGESTS.get(name)
    if not expected:
        raise RuntimeError(f'Bản phát hành thiếu chữ ký toàn vẹn cho {name}.')
    relative = COMPONENTS.get(name)
    if relative is None:
        raise RuntimeError('Thành phần kiểm tra không hợp lệ.')
    target = (_release_root() / relative).resolve()
    if not target.exists():
        raise RuntimeError(f'Bản phát hành thiếu {relative}; hãy giải nén lại đầy đủ bộ cài.')
    if tree_digest(target) != expected:
        raise RuntimeError(f'{relative} đã bị thay đổi hoặc hỏng; từ chối chạy để bảo vệ tài khoản.')
    return True
