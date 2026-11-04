"""Find the File Explorer folder under a screen point, for drag-to-move export."""
import ctypes
from ctypes import wintypes
from pathlib import Path


def _top_window_at(x, y):
    try:
        pt = wintypes.POINT(int(x), int(y))
        user32 = ctypes.windll.user32
        hwnd = user32.WindowFromPoint(pt)
        if not hwnd:
            return 0
        return user32.GetAncestor(hwnd, 2)  # GA_ROOT
    except Exception:
        return 0


def explorer_folder_at(x, y):
    """Return the Path of the File Explorer window under (x, y) screen coords, or
    None when the drop is not over a real Explorer folder window."""
    top = _top_window_at(x, y)
    if not top:
        return None
    try:
        import win32com.client
    except Exception:
        return None
    try:
        shell = win32com.client.Dispatch('Shell.Application')
        windows = shell.Windows()
    except Exception:
        return None
    for window in windows:
        try:
            if int(window.HWND) != int(top):
                continue
            # Internet Explorer windows share this list but have no filesystem folder.
            path = window.Document.Folder.Self.Path
        except Exception:
            continue
        try:
            if path and Path(path).is_dir():
                return Path(path)
        except OSError:
            continue
    return None
