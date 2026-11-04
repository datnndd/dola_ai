import unittest
from unittest.mock import patch

from cookie_accounts import parse_proxy
from hidemium_browser import (HidemiumClient, HidemiumError, normalize_url,
                              proxy_string)
from seedance_browser import migrate_settings


class HidemiumTests(unittest.TestCase):
    def test_new_profile_is_created_once_and_receipt_survives_login_retry(self):
        import tempfile
        from pathlib import Path
        from hidemium_browser import create_account_profile
        with tempfile.TemporaryDirectory() as root:
            settings = {'lock_root': str(Path(root) / 'locks')}
            with patch.object(HidemiumClient, 'create_profile', return_value='local-new') as create:
                first = create_account_profile(settings, {'id': 'new-account'}, None, None)
                retry = create_account_profile(settings, {'id': 'new-account'}, None, None)
            create.assert_called_once()
            self.assertEqual(first['hidemium_profile_id'], retry['hidemium_profile_id'])

    def test_account_ownership_ignores_opposite_global_default(self):
        from account_browser_backend import account_settings
        chrome = {'profile': 'chrome-profile', 'browser_backend': 'chrome'}
        hide = {'profile': 'lock', 'browser_backend': 'hidemium', 'hidemium_profile_id': 'local-test'}
        self.assertFalse(account_settings({'use_hidemium': True}, chrome)['use_hidemium'])
        self.assertTrue(account_settings({'use_hidemium': False}, hide)['use_hidemium'])
        with self.assertRaises(ValueError):
            account_settings({}, dict(hide, hidemium_profile_id=''))

    def test_new_hidemium_registration_never_launches_chrome(self):
        import threading
        from unittest.mock import MagicMock
        import dola_browser as d
        account = {'id': 'new', 'profile': 'unused', 'browser_backend': 'hidemium',
                   'hidemium_profile_id': 'local-test'}
        context = MagicMock()
        with patch.object(d, 'ProfileLease'), patch.object(d, 'browser_runtime'), \
             patch.object(d, 'launch_context', return_value=context) as launch, \
             patch.object(d, '_launch_context') as chrome, patch.object(d, 'BrowserSteps'), \
             patch.object(d, 'wait_for_dola_identity', return_value='Test'), patch.object(d, 'save_live_session'):
            result = d.register_dola_account({'lock_root': 'unused'}, account, threading.Event(), lambda _: None, 'unused')
        self.assertTrue(launch.call_args.args[1]['use_hidemium'])
        self.assertEqual(result['hidemium_profile_id'], 'local-test')
        chrome.assert_not_called()
        context.close.assert_called_once()

    def test_api_root_is_loopback_only(self):
        self.assertEqual(normalize_url('2222'), 'http://127.0.0.1:2222')
        self.assertEqual(normalize_url('http://localhost:2222/'), 'http://localhost:2222')
        for value in ('https://127.0.0.1:2222', 'http://example.com:2222',
                      'http://127.0.0.1:2222/path'):
            with self.assertRaises(HidemiumError):
                normalize_url(value)

    def test_profile_can_be_explicit_or_exact_name(self):
        client = HidemiumClient('2222')
        self.assertEqual(client.resolve_profile({'hidemium_profile_id': 'local-abcd'}), 'local-abcd')
        with patch.object(client, 'profiles', side_effect=[
                [{'name': 'Nick One', 'uuid': 'local-one'}], []]):
            self.assertEqual(client.resolve_profile({'name': 'nick one'}), 'local-one')

    def test_open_profile_uses_local_debug_port(self):
        client = HidemiumClient('2222')
        with patch.object(client, 'request', return_value={
                'status': 'successfully', 'data': {'remote_port': 4000}}) as request:
            self.assertEqual(client.open_profile('uuid-1', command='--x', proxy='HTTP|a|1||'),
                             'http://127.0.0.1:4000')
        query = request.call_args.kwargs['query']
        self.assertEqual(query['uuid'], 'uuid-1')
        self.assertEqual(query['command'], '--x')

    def test_proxy_is_converted_to_hidemium_format(self):
        account = {'proxy': parse_proxy('1.2.3.4:8080:user:pass')}
        self.assertEqual(proxy_string(account), 'HTTP|1.2.3.4|8080|user|pass')

    def test_migration_preserves_hidemium_choice(self):
        settings = {'browser_backend': 'hidemium', 'use_hidemium': True,
                    'extension_bundle_version': 2, 'load_extension': False}
        migrate_settings(settings, [], '.')
        self.assertTrue(settings['use_hidemium'])
        self.assertFalse(settings['use_3big_browser'])
        self.assertEqual(settings['browser_backend'], 'hidemium')


if __name__ == '__main__':
    unittest.main()
