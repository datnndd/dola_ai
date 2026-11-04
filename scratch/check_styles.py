import sys, os
sys.path.insert(0, os.path.abspath('.'))
from browser_runtime import browser_runtime
from dola_browser import find_3big_browser

with browser_runtime({}) as runtime:
    browser = runtime.chromium.launch(executable_path=find_3big_browser(), headless=True)
    try:
        page = browser.new_page(locale='vi-VN', viewport={'width': 1280, 'height': 900})
        page.goto('https://www.dola.com/chat', wait_until='domcontentloaded', timeout=45000)
        page.wait_for_timeout(3000)
        s = page.locator('[data-testid="qrcode_switcher"]').first
        print(s.evaluate('e => ({display: getComputedStyle(e).display, visibility: getComputedStyle(e).visibility, width: e.offsetWidth, height: e.offsetHeight, parent: e.parentElement.className, parentDisplay: getComputedStyle(e.parentElement).display})'))
        
        # Check all other elements in dialog
        dialog = page.locator('[role="dialog"]').first
        print('\nVisible elements in dialog:')
        for el in dialog.locator('button, a, div, span').all():
            if el.is_visible():
                txt = el.inner_text().strip()
                if txt and len(txt) < 80:
                    print(' ', el.evaluate('e => e.tagName'), repr(txt), el.get_attribute('data-testid'))
    finally:
        browser.close()
