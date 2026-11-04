import sys, os
sys.path.insert(0, os.path.abspath('.'))
from browser_runtime import browser_runtime
from dola_browser import find_3big_browser

with browser_runtime({}) as runtime:
    browser = runtime.chromium.launch(executable_path=find_3big_browser(), headless=True)
    try:
        page = browser.new_page()
        page.goto('https://www.dola.com/chat', wait_until='domcontentloaded', timeout=45000)
        page.wait_for_timeout(3000)
        dialog = page.locator('[role="dialog"]').first
        print('Dialog full text:\n', repr(dialog.inner_text()))
        print('\nAll elements inside dialog with testid or tag:')
        for el in dialog.locator('*').all():
            tid = el.get_attribute('data-testid')
            tag = el.evaluate('e => e.tagName')
            cls = el.get_attribute('class') or ''
            txt = el.inner_text()[:40] if el.is_visible() else ''
            html = el.evaluate('e => e.outerHTML')[:150]
            if tid or tag in ('BUTTON', 'A', 'IMG', 'SVG', 'DIV') and any(k in html.lower() for k in ['facebook', 'google', 'more', 'third', 'login', 'icon']):
                print(f'tag={tag} testid={tid} class={cls[:40]!r} text={txt!r} html={html}')
    finally:
        browser.close()
