import sys, os
sys.path.insert(0, os.path.abspath('.'))
from browser_runtime import browser_runtime
from dola_browser import find_3big_browser

with browser_runtime({}) as runtime:
    browser = runtime.chromium.launch(executable_path=find_3big_browser(), headless=True)
    try:
        page = browser.new_page(locale='vi-VN')
        page.goto('https://www.dola.com/chat', wait_until='domcontentloaded', timeout=45000)
        page.wait_for_timeout(3000)
        html = page.locator('[data-testid="login_content"]').inner_html()
        print('login_content HTML:\n', html)
    finally:
        browser.close()
