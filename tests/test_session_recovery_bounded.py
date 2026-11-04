import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import dola_browser as d

class RecoveryTests(unittest.TestCase):
    def test_two_imports_then_dead(self):
        context=Mock(); page=Mock(); page.is_closed.return_value=False
        steps=Mock(); steps.wait_until.side_effect=TimeoutError('not logged in')
        cookie={'domain':'.dola.com','name':'session','value':'test'}
        with patch.object(d,'session_snapshot_path',return_value=Path('missing-test-snapshot')), patch.object(d,'load_cookies',return_value=[cookie]), patch.object(d,'BrowserSteps',return_value=steps), patch.object(d,'dola_account_present',return_value=False), patch.object(d.random,'uniform',return_value=0):
            with self.assertRaises(d.SessionDead):
                d.restore_cookie_session(context,{}, {'id':'a'},threading.Event(),lambda _:None,page=page)
        self.assertEqual(context.add_cookies.call_count,2)
        self.assertEqual(steps.navigate.call_count,2)

    def test_task_never_waits_for_manual_login(self):
        with self.assertRaises(d.SessionDead):
            d.wait_for_task_login(Mock(),Mock())
