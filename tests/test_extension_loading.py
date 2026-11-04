"""Verify the actual bundled extension in temporary Chrome profiles."""
import tempfile
from pathlib import Path
import unittest

from browser_runtime import browser_runtime
from dola_browser import launch_context
from seedance_browser import migrate_settings
from studio_extension import extension_dir, launch_options


class ExtensionTests(unittest.TestCase):
    def test_enable_once_and_preserve_user_disable(self):
        settings = dict(load_extension=False)
        migrate_settings(settings, [], Path('unused'))
        self.assertTrue(settings['load_extension'])
        settings['load_extension'] = False
        migrate_settings(settings, [], Path('unused'))
        self.assertFalse(settings['load_extension'])
        self.assertEqual(launch_options(True, False), {'args':['--disable-extensions']})

    def test_loaded_in_login_and_video_sessions_with_content_panel(self):
        self.assertEqual(extension_dir().name, 'pro')
        for video in (False, True):
            with self.subTest(video=video), tempfile.TemporaryDirectory() as folder:
                settings = dict(use_3big_browser=True, use_gpm=False, load_extension=True)
                account = dict(id='offline', profile=folder, proxy_mode='off')
                with browser_runtime(settings) as runtime:
                    context = launch_context(runtime, settings, account, visible=False, video_session=video)
                    try:
                        workers = [worker for worker in context.service_workers
                                   if worker.url.startswith('chrome-extension://')]
                        self.assertEqual(len(workers), 1)
                        manifest = workers[0].evaluate('chrome.runtime.getManifest()')
                        self.assertEqual(manifest['name'], 'Seedance Studio — Pro · Tạo nhiều video')
                        state = workers[0].evaluate('SeedanceExtensionLicense.snapshot()')
                        self.assertFalse(state['ok'])
                        self.assertFalse(state['configured'])
                    finally:
                        context.close()


if __name__ == '__main__':
    unittest.main()
