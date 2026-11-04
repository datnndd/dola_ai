import sys, os
sys.path.insert(0, os.path.abspath('.'))
from browser_runtime import browser_runtime
from dola_browser import find_3big_browser

with browser_runtime({}) as runtime:
    browser = runtime.chromium.launch(
        executable_path=find_3big_browser(), headless=True
    )
    try:
        page = browser.new_page()
        page.goto('https://www.dola.com/chat', wait_until='domcontentloaded', timeout=45000)
        page.wait_for_timeout(3000)

        # Check if login dialog is already open!
        overlay = page.locator('[data-slot="dialog-overlay"], [data-state="open"]')
        print('Overlay count:', overlay.count(), 'visible:', any(o.is_visible() for o in overlay.all()))

        dialog = page.locator('[role="dialog"], [data-slot="dialog-content"]').first
        print('Dialog visible:', dialog.is_visible() if dialog.count() else False)

        if dialog.count() and dialog.is_visible():
            print('\nDialog HTML:')
            print(dialog.evaluate('e => e.outerHTML')[:1500])

            print('\nButtons inside dialog:')
            for b in dialog.locator('button, [role="button"], a').all():
                if b.is_visible():
                    txt = ' '.join(str(b.inner_text() or '').split())
                    tid = b.get_attribute('data-testid') or ''
                    aria = b.get_attribute('aria-label') or ''
                    html = b.evaluate('e => e.outerHTML')[:200]
                    print(f'  BTN: text={txt!r} testid={tid!r} aria={aria!r}\n       html={html}')
    finally:
        browser.close()
