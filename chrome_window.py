"""'Hidden' Chrome: a normal window parked off-screen, brought back for manual verification."""

OFFSCREEN_ARG = '--window-position=-32000,-32000'
OFFSCREEN = {'left': -32000, 'top': -32000}
ONSCREEN = {'left': 60, 'top': 40, 'width': 1280, 'height': 900}


def set_window_visible(page, visible):
    """Move this page's Chrome window on/off screen. False when the browser cannot (e.g. GPM, closed page)."""
    try:
        cdp = page.context.new_cdp_session(page)
        try:
            window = cdp.send('Browser.getWindowForTarget')['windowId']
            # Bounds cannot change while minimized/maximized; normalize first.
            cdp.send('Browser.setWindowBounds', {'windowId': window, 'bounds': {'windowState': 'normal'}})
            cdp.send('Browser.setWindowBounds', {'windowId': window, 'bounds': ONSCREEN if visible else OFFSCREEN})
        finally:
            try: cdp.detach()
            except Exception: pass
        if visible:
            page.bring_to_front()
        return True
    except Exception:
        return False
