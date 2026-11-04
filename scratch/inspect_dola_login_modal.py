import sys, os
sys.path.insert(0, os.path.abspath('.'))
import json
from browser_runtime import browser_runtime
from dola_browser import find_3big_browser

settings = json.load(open('data/workspace.json', encoding='utf-8'))['settings']

with browser_runtime(settings) as runtime:
    # Launch clean browser without cookies
    browser = runtime.chromium.launch(
        executable_path=find_3big_browser(), headless=True,
        proxy=settings.get('default_proxy') or {'server': 'http://160.250.166.14:10583'}
    )
    try:
        page = browser.new_page()
        page.goto('https://www.dola.com/chat', wait_until='domcontentloaded', timeout=45000)
        page.wait_for_timeout(3000)

        # Look for login button on home page
        login_selectors = [
            '[data-testid="to_login_button"]',
            'button:has-text("Đăng nhập")',
            'button:has-text("Log in")',
            'button:has-text("Sign in")',
            '[data-testid="login_button"]'
        ]
        for sel in login_selectors:
            loc = page.locator(sel)
            print(f'Selector {sel!r}: count={loc.count()}')
            for i in range(loc.count()):
                b = loc.nth(i)
                print(f'  [{i}] vis={b.is_visible()} text={b.inner_text()!r} attrs={b.evaluate("e => ({id: e.id, class: e.className, testid: e.getAttribute(\"data-testid\")})")}')

        login_btn = page.locator('[data-testid="to_login_button"], button:has-text("Đăng nhập"), button:has-text("Log in"), button:has-text("Sign in")').first
        if login_btn.is_visible():
            print('\nClicking login button...')
            login_btn.click()
            page.wait_for_timeout(2000)

            # Dump all buttons and elements inside the login modal / dialog
            print('\n--- ALL ELEMENTS IN LOGIN MODAL ---')
            dialogs = page.locator('[role="dialog"], [data-slot="dialog-content"], [class*="dialog"], [class*="modal"], [class*="login"]').all()
            print('Dialog count:', len(dialogs))
            for i, d in enumerate(dialogs):
                if d.is_visible():
                    print(f'Dialog [{i}]:\n{d.evaluate("e => e.outerHTML")[:500]}\n')

            print('\n--- LOOKING FOR FACEBOOK BUTTONS ---')
            fb_selectors = [
                '[data-testid="login_third_facebook"]',
                '[data-testid*="facebook"]',
                '[data-testid*="fb"]',
                'button:has-text("Facebook")',
                '[aria-label*="Facebook" i]',
                '[title*="Facebook" i]',
                'button:has(svg)',
                'button:has(img)'
            ]
            for sel in fb_selectors:
                loc = page.locator(sel)
                cnt = loc.count()
                if cnt:
                    print(f'FB Selector {sel!r}: count={cnt}')
                    for j in range(min(cnt, 5)):
                        b = loc.nth(j)
                        print(f'  [{j}] vis={b.is_visible()} tag={b.evaluate("e => e.tagName")} text={b.inner_text()!r} html={b.evaluate("e => e.outerHTML")[:250]}')
    finally:
        browser.close()
