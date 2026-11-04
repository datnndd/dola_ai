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

        # Close the auto-popup dialog
        close_btn = page.locator('[data-testid="modal_close"], button[aria-label="close"]').first
        if close_btn.is_visible():
            print('Closing initial modal...')
            close_btn.click()
            page.wait_for_timeout(1000)

        # Now click header login button
        login_btn = page.locator('[data-testid="to_login_button"]').first
        if login_btn.is_visible():
            print('Clicking header to_login_button...')
            login_btn.click()
            page.wait_for_timeout(2000)

            print('After clicking header login button:')
            dialog = page.locator('[role="dialog"]').first
            if dialog.is_visible():
                print('Dialog text:\n', repr(dialog.inner_text()))
                for b in dialog.locator('button, [role="button"], a').all():
                    if b.is_visible():
                        print('  BTN:', b.inner_text()[:40], b.get_attribute('data-testid'), b.evaluate('e => e.outerHTML')[:100])
    finally:
        browser.close()
