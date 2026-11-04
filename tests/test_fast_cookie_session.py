import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from dola_browser import check_task_session, ensure_chrome_session


class Steps:
    def wait_until(self, predicate, timeout, message):
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() >= deadline:
                raise TimeoutError(message)
            time.sleep(.005)


class FastCookieSessionTests(unittest.TestCase):
    def test_loaded_cookie_page_without_name_or_avatar_restores_quickly(self):
        reports = []
        account = {'id': 'a', 'name': 'Saved User', 'cookie_kind': 'dola', 'cookie_file': 'vault'}
        with TemporaryDirectory() as folder:
            snapshot = Path(folder) / 'session.dpapi'
            snapshot.touch()
            with patch('dola_browser.session_snapshot_path', return_value=snapshot), \
                 patch('dola_browser.is_login_required', return_value=False), \
                 patch('dola_browser.dola_account_present', return_value=False), \
                 patch('dola_browser.dola_shell_loaded', return_value=True), \
                 patch('dola_browser.restore_cookie_session', return_value='Restored User') as restore, \
                 patch('dola_browser.save_live_session'):
                started = time.monotonic()
                name = check_task_session(object(), object(), Steps(),
                    {'cookie_identity_grace': .05}, account, threading.Event(), reports.append)
            self.assertLess(time.monotonic() - started, .5)
            self.assertEqual(name, 'Restored User')
            restore.assert_called_once()
            self.assertTrue(any('không thấy tên/avatar' in line for line in reports))

    def test_avatar_without_visible_name_keeps_live_session(self):
        account = {'id': 'a', 'name': 'Saved User'}
        with patch('dola_browser.session_snapshot_path', return_value=Path('missing')), \
             patch('dola_browser.is_login_required', return_value=False), \
             patch('dola_browser.dola_account_present', return_value=True), \
             patch('dola_browser.dola_identity', return_value=''), \
             patch('dola_browser.restore_cookie_session') as restore, \
             patch('dola_browser.save_live_session'):
            name = check_task_session(object(), object(), Steps(), {}, account,
                                      threading.Event(), lambda message: None)
        self.assertEqual(name, 'Saved User')
        restore.assert_not_called()

    def test_cached_chrome_session_is_rechecked_for_each_cookie_job(self):
        state = {'valid': True, 'name': 'Old User'}
        account = {'id': 'a', 'name': 'Saved User', 'cookie_kind': 'dola', 'cookie_file': 'vault'}
        reports = []
        with patch('dola_browser.session_snapshot_path') as snapshot, \
             patch('dola_browser.dola_account_present', return_value=False), \
             patch('dola_browser.dola_shell_loaded', return_value=True), \
             patch('dola_browser.check_task_session', return_value='Restored User') as check:
            snapshot.return_value.exists.return_value = True
            name = ensure_chrome_session(state, object(), object(), Steps(), {}, account,
                                         threading.Event(), reports.append)
        self.assertEqual(name, 'Restored User')
        self.assertEqual(state, {'valid': True, 'name': 'Restored User'})
        check.assert_called_once()
        self.assertTrue(any('kiểm tra lại phiên cookie ngay' in line for line in reports))


if __name__ == '__main__':
    unittest.main()
