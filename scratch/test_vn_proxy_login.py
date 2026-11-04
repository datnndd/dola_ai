import sys, os
sys.path.insert(0, os.path.abspath('.'))
from browser_runtime import browser_runtime
from dola_browser import find_3big_browser

with browser_runtime({}) as runtime:
    browser = runtime.chromium.launch(
        executable_path=find_3big_browser(), headless=True,
        proxy={'server': 'http://160.250.166.14:10583'}
    )
    try:
        page = browser.new_page(locale='vi-VN', viewport={'width': 1280, 'height': 900})
        page.goto('https://www.dola.com/chat', wait_until='domcontentloaded', timeout=45000)
        page.wait_for_timeout(3000)
        dialog = page.locator('[role="dialog"]').first
        if dialog.is_visible():
            print('Dialog text (WITH VN PROXY):\n', repr(dialog.inner_text()))
            for b in dialog.locator('button, [role="button"], a').all():
                if b.is_visible():
                    print('  BTN:', b.inner_text()[:40], b.get_attribute('data-testid'), b.evaluate('e => e.outerHTML')[:120])
    finally:
        browser.close()
