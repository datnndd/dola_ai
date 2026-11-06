"""End-to-end tests for Prompt-to-Video workflow and Account Management in Studio Qt6."""
import os
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox
import studio_qt


def _app():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
        app.setStyleSheet(studio_qt.STYLE)
    return app


def _pump(win, timeout=3.0, predicate=None):
    app = _app()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        win._drain_events()
        app.processEvents()
        if predicate is not None and predicate():
            return True
        time.sleep(0.01)
    win._drain_events()
    app.processEvents()
    return bool(predicate and predicate())


class PromptAndAccountsUITests(unittest.TestCase):
    def setUp(self):
        _app()
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name) / 'data'
        self.out_dir = Path(self.tmp.name) / 'output'
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.p_data_qt = patch.object(studio_qt, 'DATA', self.data_dir)
        self.p_data_core = patch.object(studio_qt.studio_core if hasattr(studio_qt, 'studio_core') else studio_qt, 'DATA', self.data_dir)
        self.p_discover = patch.object(studio_qt, 'discover_accounts', return_value=[])
        self.p_data_qt.start()
        self.p_discover.start()
        self.win = studio_qt.Studio()
        self.win.settings['output'] = str(self.out_dir)

    def tearDown(self):
        try:
            self.win._alive = False
            self.win.close()
        except Exception:
            pass
        self.p_discover.stop()
        self.p_data_qt.stop()
        self.tmp.cleanup()

    def test_facebook_batch_updates_list_and_summary_as_each_login_finishes(self):
        import copy
        built = [([{'name': 'c_user', 'value': str(i)}], {}, 'off') for i in range(2)]
        second_login = threading.Event()
        def completed(_settings, account, _cancel, _report, _root, initial_cookies=None):
            if initial_cookies[0]['value'] == '1':
                second_login.wait(5)
            return dict(copy.deepcopy(account), name='FB ' + str(initial_cookies[0]['value']),
                        selected=True, connection='Phiên hợp lệ', session_verified_at=time.time())
        dlg = studio_qt.AccountsDialog(self.win)
        try:
            with patch.object(studio_qt, 'register_dola_account', side_effect=completed), \
                 patch.object(studio_qt, 'save_cookies', return_value=str(self.data_dir / 'cookie.dpapi')):
                self.win.start_facebook_cookie_batch(built)
                self.assertTrue(_pump(self.win, predicate=lambda: self.win.account_card.total.text() == '1'))
                self.assertEqual(len(self.win.accounts), 1)
                self.assertEqual(len(dlg.rows), 1)
                self.assertIn('Live <b>1</b>', self.win.account_card.counts['live'][0].text())
                second_login.set()
                self.assertTrue(_pump(self.win, predicate=lambda: self.win.account_card.total.text() == '2'))
        finally:
            second_login.set()
        self.assertEqual(len(dlg.rows), 2)
        self.assertIn('Live <b>2</b>', self.win.account_card.counts['live'][0].text())
        dlg.close()

    def test_account_action_failure_or_cancel_releases_busy_state(self):
        for login, failure, expected in (
            (True, RuntimeError('network unavailable'), 'Lỗi kết nối'),
            (False, studio_qt.Cancelled('stopped'), 'Chưa kiểm tra'),
        ):
            with self.subTest(login=login):
                account = {'id': 'action-' + str(login), 'name': 'Test',
                           'profile': str(self.data_dir / 'profiles' / str(login)),
                           'connection': 'Phiên hợp lệ', 'live_state': 'live'}
                self.win.accounts.append(account)
                with patch.object(studio_qt, 'account_browser', side_effect=failure):
                    self.win.run_account_action(account, login=login)
                    self.assertTrue(_pump(self.win, timeout=1.0,
                                          predicate=lambda: account['id'] not in self.win.account_jobs))
                self.assertEqual(account['connection'], expected)
                self.assertNotEqual(account['live_state'], 'live')

    def test_quick_prompt_single_and_multiline_and_quick_settings(self):
        # Initially has 1 untouched placeholder task
        self.assertEqual(len(self.win.tasks), 1)
        self.assertEqual(self.win.tasks[0]['prompt'], '')

        # Change quick settings combos
        self.win.quick_model.setCurrentText('Seedance 2.5')
        self.win.quick_duration.setCurrentText('30s · ghép 2 × 15s')
        self.win.quick_ratio.setCurrentText('16:9')
        self.win.quick_mode.setCurrentText('Skill 30s')
        self.assertEqual(self.win.settings['model'], 'Seedance 2.5')
        self.assertEqual(self.win.settings['duration'], '30')
        self.assertEqual(self.win.settings['ratio'], '16:9')
        self.assertEqual(self.win.settings['dola_mode'], 'skill30')

        # Adding first prompt reuses the initial untouched Task #1
        self.win.quick_prompt.setPlainText('Cảnh hoàng hôn trên biển 4K')
        added = self.win.add_quick_task()
        self.assertEqual(len(added), 1)
        self.assertEqual(len(self.win.tasks), 1)
        self.assertEqual(self.win.tasks[0]['name'], 'Task #1')
        self.assertEqual(self.win.tasks[0]['prompt'], 'Cảnh hoàng hôn trên biển 4K')
        self.assertEqual(self.win.quick_prompt.toPlainText(), '')

        # Adding multi-line prompts creates 1 task per non-empty line
        self.win.quick_prompt.setPlainText('Mèo con chạy trên đồng cỏ\n\nPhi hành gia đi bộ trên sao Hỏa\n')
        added_multi = self.win.add_quick_task()
        self.assertEqual(len(added_multi), 2)
        self.assertEqual(len(self.win.tasks), 3)
        self.assertEqual(self.win.tasks[1]['name'], 'Task #2')
        self.assertEqual(self.win.tasks[1]['prompt'], 'Mèo con chạy trên đồng cỏ')
        self.assertEqual(self.win.tasks[2]['name'], 'Task #3')
        self.assertEqual(self.win.tasks[2]['prompt'], 'Phi hành gia đi bộ trên sao Hỏa')

    def test_prompt_to_video_dola_execution_does_not_cooldown_lock_account(self):
        # Set up a live Dola account
        profile_dir = self.data_dir / 'profiles' / 'acc1'
        profile_dir.mkdir(parents=True, exist_ok=True)
        acc = {
            'id': 'acc1',
            'name': 'Nick Dola Test 1',
            'profile': str(profile_dir),
            'selected': True,
            'live_state': 'live',
            'connection': 'Phiên hợp lệ',
            'session_verified_at': time.time(),
            'proxy_mode': 'off',
            'browser_backend': 'chrome',
        }
        self.win.accounts.append(acc)
        self.win.draw_accounts()

        def fake_run_profile_tabs(group, account, cfg, cancel_event, report_for, settle_account,
                                  session_cache, session_cache_lock, submit_video, task_cancels,
                                  intake, browser_report=None, accepting=None):
            for idx, task in enumerate(group, 1):
                rep = report_for(task, account)
                rep('[Hội thoại] https://www.dola.com/chat/123456')
                rep('Đang tạo video trên Dola…')
                out_file = Path(cfg['output']) / f"video_{task['id']}.mp4"
                out_file.write_bytes(b'\x00\x00\x00\x18ftypmp42' + b'\x00' * 128)
                settle_account(task, str(out_file), None)

        with patch('dola_tabs.run_profile_tabs', side_effect=fake_run_profile_tabs), \
             patch.object(studio_qt, 'batch_blockers', return_value=([], '', False)):
            self.win.quick_prompt.setPlainText('Prompt video số 1\nPrompt video số 2')
            self.win.add_and_run_quick_task()
            finished = _pump(self.win, timeout=5.0, predicate=lambda: (
                not self.win.running and not self.win._batch_preparing
                and all('hoàn thành' in t.get('status', '').casefold() for t in self.win.tasks)
            ))
            self.assertTrue(finished, f"Tasks did not finish: {[t.get('status') for t in self.win.tasks]}")

        self.assertEqual(len(self.win.tasks), 2)
        for t in self.win.tasks:
            self.assertIn('Hoàn thành', t['status'])
            self.assertTrue(Path(t['output_path']).is_file())
            self.assertEqual(t['conversation_url'], 'https://www.dola.com/chat/123456')
            self.assertEqual(t['conversation_account'], 'acc1')

        # Account must still be selected=True and NOT locked into 7-day cooldown after manual run
        self.assertTrue(self.win.accounts[0]['selected'])
        self.assertFalse(studio_qt.run_cooldown_locked(self.win.accounts[0]))

    def test_account_management_dialogs_and_actions(self):
        # 1. Import Dola JSON cookies via DolaCookieDialog
        dola_dlg = studio_qt.DolaCookieDialog(self.win)
        sample_cookie = json.dumps({
            'profile': 'Dola Cookie User',
            'cookies': [{'name': 'sessionid', 'value': 'abc123xyz', 'domain': '.dola.com'}]
        })
        dola_dlg.box.setPlainText(sample_cookie)
        dola_dlg.proxy.setText('127.0.0.1:8080:user1:pass1')
        dola_dlg.submit(start=False)
        ok = _pump(self.win, timeout=3.0, predicate=lambda: len(self.win.accounts) == 1)
        self.assertTrue(ok)
        acc = self.win.accounts[0]
        self.assertEqual(acc['name'], 'Dola Cookie User')
        self.assertEqual(acc['proxy_mode'], 'manual')
        self.assertTrue(acc.get('cookie_file'))

        # 2. Open AccountsDialog and verify row rendering, filter, rename, toggle run, check live, proxy, and delete
        dlg = studio_qt.AccountsDialog(self.win)
        self.assertIn(acc['id'], dlg.rows)
        row = dlg.rows[acc['id']]
        self.assertEqual(row.name.text(), 'Dola Cookie User')

        # Toggle Dùng chạy off and back on
        row.run.setChecked(False)
        self.assertFalse(acc['selected'])
        row.run.setChecked(True)
        self.assertTrue(acc['selected'])

        # Rename account
        with patch.object(QInputDialog, 'getText', return_value=('Dola VIP 01', True)):
            self.win.rename_account_dialog(acc)
        _pump(self.win, timeout=1.0)
        self.assertEqual(acc['name'], 'Dola VIP 01')
        dlg.redraw()
        self.assertEqual(dlg.rows[acc['id']].name.text(), 'Dola VIP 01')

        # Change proxy via ProxyDialog to 'off'
        proxy_dlg = studio_qt.ProxyDialog(self.win, acc)
        _pump(self.win, timeout=1.0, predicate=lambda: proxy_dlg.save_button.isEnabled())
        proxy_dlg.proxy.combo.setCurrentText(studio_qt.label_for_mode('off'))
        proxy_dlg.save_proxy()
        _pump(self.win, timeout=1.0, predicate=lambda: acc.get('proxy_mode') == 'off')
        self.assertEqual(acc['proxy_mode'], 'off')

        # Check live on selected account
        with patch.object(studio_qt, 'account_browser', return_value=True):
            self.win.check_selected_accounts([acc])
            checked = _pump(self.win, timeout=3.0, predicate=lambda: acc.get('live_state') == 'live')
            self.assertTrue(checked)
        self.assertEqual(acc['live_state'], 'live')
        self.assertIn('Live', acc['connection'])

        # Filter in AccountsDialog
        dlg.redraw()
        dlg.set_filter('live')
        self.assertFalse(dlg.rows[acc['id']].isHidden())
        dlg.set_filter('die')
        self.assertTrue(dlg.rows[acc['id']].isHidden())
        dlg.set_filter('all')
        self.assertFalse(dlg.rows[acc['id']].isHidden())

        # Remove account
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes):
            dlg.remove([acc])
        removed = _pump(self.win, timeout=3.0, predicate=lambda: len(self.win.accounts) == 0)
        self.assertTrue(removed)
        dlg.close()

    def test_prompt_dialog_config_dialog_and_fetch_saved_conversation(self):
        # Add a task and edit via PromptDialog + attach reference image
        self.win.quick_prompt.setPlainText('Prompt gốc')
        self.win.add_quick_task()
        task = self.win.tasks[0]
        img_path = self.out_dir / 'ref.png'
        img_path.write_bytes(b'\x89PNG\r\n\x1a\n' + b'\x00' * 32)

        pdlg = studio_qt.PromptDialog(self.win, task)
        pdlg.edit.setPlainText('Prompt đã chỉnh sửa kèm ảnh tham chiếu')
        task['images'] = [str(img_path)]
        pdlg.accept_save()
        self.assertEqual(task['prompt'], 'Prompt đã chỉnh sửa kèm ảnh tham chiếu')
        self.assertEqual(task['images'], [str(img_path)])

        # Configure task options via ConfigDialog
        cdlg = studio_qt.ConfigDialog(self.win, [task], shared=False)
        cdlg.combos['duration'].setCurrentText('30 giây · ghép 2 × 15s')
        cdlg.combos['model'].setCurrentText('Seedance 2.5')
        cdlg.combos['dola_mode'].setCurrentText('Skill 30s')
        cdlg.combos['ratio'].setCurrentText('16:9')
        cdlg.timeout.setText('1200')
        cdlg.save()
        self.assertEqual(task['options']['duration'], '30')
        self.assertEqual(task['options']['model'], 'Seedance 2.5')
        self.assertEqual(task['options']['dola_mode'], 'skill30')
        self.assertEqual(task['options']['ratio'], '16:9')
        self.assertEqual(task['options']['timeout'], 1200)

        # Fetch video from saved conversation
        acc = {
            'id': 'acc_conv',
            'name': 'Nick Conv',
            'profile': str(self.data_dir / 'profiles' / 'acc_conv'),
            'selected': True,
            'live_state': 'live',
            'connection': 'Phiên hợp lệ',
            'proxy_mode': 'off',
            'browser_backend': 'chrome',
        }
        self.win.accounts.append(acc)
        task['conversation_url'] = 'https://www.dola.com/chat/998877'
        task['conversation_account'] = 'acc_conv'

        recovered_mp4 = self.out_dir / 'recovered.mp4'
        recovered_mp4.write_bytes(b'\x00\x00\x00\x18ftypmp42' + b'\x00' * 64)
        with patch.object(studio_qt, 'render_dola', return_value=str(recovered_mp4)):
            self.win.fetch_table_video(task['id'])
            done = _pump(self.win, timeout=3.0, predicate=lambda: (
                task['id'] not in self.win.fetching_ids and bool(task.get('output_path'))
            ))
            self.assertTrue(done)
        self.assertEqual(Path(task['output_path']).resolve(), recovered_mp4.resolve())
        self.assertIn('Hoàn thành', task['status'])

    def test_google_cookie_import_and_rest_credit_filters(self):
        gdlg = studio_qt.GoogleCookieDialog(self.win)
        gdlg.box.setPlainText('SID=google_sid_val; HSID=google_hsid_val; SSID=google_ssid_val')
        gdlg.submit(start=False)
        ok = _pump(self.win, timeout=3.0, predicate=lambda: len(self.win.accounts) == 1)
        self.assertTrue(ok)
        acc = self.win.accounts[0]

        # Lock account for 7-day cooldown and verify 'rest' filter in AccountsDialog
        studio_qt.lock_for_run_cooldown(acc)
        dlg = studio_qt.AccountsDialog(self.win)
        self.assertEqual(dlg.rows[acc['id']].key, 'rest')
        dlg.set_filter('rest')
        self.assertFalse(dlg.rows[acc['id']].isHidden())
        # Bulk enable 'Dùng chạy' clears both credit lock and 7-day cooldown
        dlg._pick_all(True)
        dlg._set_run(True)
        self.assertTrue(acc['selected'])
        self.assertFalse(studio_qt.run_cooldown_locked(acc))
        dlg.close()


if __name__ == '__main__':
    unittest.main()
