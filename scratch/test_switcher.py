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

        switcher = page.locator('[data-testid="qrcode_switcher"]')
        print('Switcher count:', switcher.count(), 'vis:', switcher.first.is_visible() if switcher.count() else False)
        if switcher.count() and switcher.first.is_visible():
            print('Clicking qrcode_switcher...')
            switcher.first.click()
            page.wait_for_timeout(1500)

            print('\nAfter switcher click:')
            dialog = page.locator('[role="dialog"]').first
            print('Dialog text:\n', repr(dialog.inner_text()))
            for b in dialog.locator('button, [role="button"], [data-testid]').all():
                if b.is_visible():
                    txt = ' '.join(str(b.inner_text() or '').split())
                    tid = b.get_attribute('data-testid') or ''
                    html = b.evaluate('e => e.outerHTML')[:120]
                    print(f'  BTN: text={txt!r} testid={tid!r}\n       html={html}')
    finally:
        browser.close()
