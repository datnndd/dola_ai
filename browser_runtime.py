"""Select the browser driver consistently for every Dola workflow."""
from playwright.sync_api import sync_playwright as playwright_runtime
from playwright.sync_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeout
from patchright.sync_api import sync_playwright as patchright_runtime
from patchright.sync_api import Error as PatchrightError, TimeoutError as PatchrightTimeout

BrowserError = (PlaywrightError, PatchrightError)
BrowserTimeout = (PlaywrightTimeout, PatchrightTimeout)


def browser_runtime(settings):
    if settings.get('use_3big_browser') and not settings.get('use_gpm') and not settings.get('use_hidemium'):
        return patchright_runtime()
    return playwright_runtime()
