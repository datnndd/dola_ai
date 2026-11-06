import unittest
from unittest.mock import MagicMock, patch

from proxyvn import proxy_source, gpm_proxy_account
from auto_jobs import AutoJobs, ReportJournal


class TestAutoProxyAndReport(unittest.TestCase):

    def test_proxy_source_fallback_to_manual(self):
        settings_pvn_no_keys = {
            'proxy_source': 'pvn',
            'proxyvn_keys_protected': '',
            'default_proxies': [{'server': 'http://1.2.3.4:8080'}]
        }
        self.assertEqual(proxy_source(settings_pvn_no_keys), 'manual')

        settings_pvn_with_keys = {
            'proxy_source': 'pvn',
            'proxyvn_keys_protected': 'some_key',
            'default_proxies': [{'server': 'http://1.2.3.4:8080'}]
        }
        with patch('proxy_pool._decode_keys', return_value=['key1']):
            self.assertEqual(proxy_source(settings_pvn_with_keys), 'pvn')

    def test_gpm_proxy_account_manual_fallback(self):
        settings = {
            'proxy_source': 'pvn',
            'proxyvn_keys_protected': '',
            'default_proxies': [{'server': 'http://160.250.166.14:10583'}],
            'manual_proxy_slots': {'acc_1': 0}
        }
        account = {'id': 'acc_1', 'name': 'TestAcc', 'proxy_mode': 'default'}
        runtime = gpm_proxy_account(settings, account)
        self.assertIn('proxy', runtime)
        self.assertEqual(runtime['proxy']['server'], 'http://160.250.166.14:10583')

    def test_acknowledge_result_lease_expired_fallback(self):
        runner = MagicMock()
        client = MagicMock()
        client.legacy_report.return_value = {'ok': True}
        runner.client = client
        runner.journal = MagicMock()
        runner.emit = MagicMock()
        runner.log = MagicMock()

        tid = 'task_123'
        row = {'stt': '332', 'task_code': 'v2912-1', 'lease': 'lease_old', 'status': 'done'}
        item = {'job_id': 'v2912-1', 'lease': 'lease_old', 'status': 'done'}
        result = {'ok': False, 'error': 'LEASE_EXPIRED', 'message': 'Lease het han'}

        AutoJobs.acknowledge_result(runner, tid, row, item, result)

        client.legacy_report.assert_called_once_with(item)
        runner.journal.acknowledge.assert_called_once_with(tid)
        runner.emit.assert_called_with('auto_report', tid, 'done')


if __name__ == '__main__':
    unittest.main()
