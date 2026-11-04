"""Run this file from VS Code; reuse the prepared Python environment if needed.

Mở giao diện Qt6 (studio_qt.py) — giao diện duy nhất của app, không dùng tkinter."""
from pathlib import Path
import importlib.util
import runpy
import subprocess
import sys

TARGET = 'studio_qt.py'


def main():
    folder = Path(__file__).resolve().parent
    missing = [name for name in ('PySide6', 'playwright', 'patchright')
               if importlib.util.find_spec(name) is None]
    if missing:
        candidates = [folder / '.venv' / 'Scripts' / 'python.exe',
                      folder.parents[1] / 'work' / '.venv' / 'Scripts' / 'python.exe']
        for python in candidates:
            if python.is_file() and python.resolve() != Path(sys.executable).resolve():
                return subprocess.call([str(python), str(folder / TARGET)], cwd=str(folder))
        print('Thiếu gói: ' + ', '.join(missing) + '. Cài bằng interpreter này:')
        print(f'"{sys.executable}" -m pip install PySide6 -r "{folder / "requirements.txt"}"')
        return 1
    sys.path.insert(0, str(folder))
    runpy.run_path(str(folder / TARGET), run_name='__main__')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
