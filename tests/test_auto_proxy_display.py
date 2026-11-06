import unittest
from unittest.mock import MagicMock
from proxyvn import resolve_proxy_server


class TestAutoProxyDisplay(unittest.TestCase):
    def test_resolve_proxy_server_direct_proxy(self):
        acc = {'id': 'acc1', 'name': 'Acc 1', 'proxy': {'server': 'http://160.250.166.13:10987'}}
        self.assertEqual(resolve_proxy_server({}, acc), '160.250.166.13:10987')

    def test_resolve_proxy_server_off(self):
        acc = {'id': 'acc2', 'name': 'Acc 2', 'proxy_mode': 'off'}
        self.assertEqual(resolve_proxy_server({}, acc), 'Không proxy')

    def test_resolve_proxy_server_manual_source(self):
        settings = {
            'proxy_source': 'manual',
            'default_proxies': [{'server': 'http://1.2.3.4:8080'}, {'server': 'http://5.6.7.8:8080'}],
            'manual_proxy_slots': {'acc3': 1}
        }
        acc = {'id': 'acc3', 'name': 'Acc 3', 'proxy': {}}
        self.assertEqual(resolve_proxy_server(settings, acc), '5.6.7.8:8080')

    def test_resolve_proxy_server_pvn_source(self):
        settings = {'proxy_source': 'pvn'}
        acc = {'id': 'acc4', 'name': 'Acc 4', 'proxy': {}}
        self.assertEqual(resolve_proxy_server(settings, acc), 'ProxyVN (Tự động)')

    def test_resolve_proxy_server_proxyno1_source(self):
        settings = {'proxy_source': 'proxyno1'}
        acc = {'id': 'acc5', 'name': 'Acc 5', 'proxy': {}}
        self.assertEqual(resolve_proxy_server(settings, acc), 'ProxyNo1 (Tự động)')

    def test_auto_jobs_report_for_emits_proxy_server(self):
        from auto_jobs import AutoJobs
        import queue

        settings = {
            'output': 'downloads',
            'proxy_source': 'pvn',
            'duration': '15',
            'model': 'Seedance 2.5',
            'concurrency': 1,
        }
        accounts = [{'id': 'acc1', 'name': 'Sakio Hasab', 'proxy': {}, 'proxy_mode': 'default'}]
        events = queue.Queue()
        cancel = MagicMock()
        task_cancels = {}

        # Mock AutoJobs without filesystem dependencies
        aj = AutoJobs.__new__(AutoJobs)
        aj.settings = settings
        aj.accounts = accounts
        aj.events = events
        aj.lock = MagicMock()
        aj.journal = None
        aj.log = MagicMock()

        task = {'id': 'task_1', 'name': 'Task #1', 'prompt': 'test'}
        report = aj.report_for(task, accounts[0])

        emitted = []
        while not events.empty():
            emitted.append(events.get_nowait())

        # Check task_execution event
        exec_events = [e for e in emitted if e[0] == 'task_execution']
        self.assertTrue(len(exec_events) >= 1)
        _, task_id, payload = exec_events[0]
        self.assertEqual(task_id, 'task_1')
        self.assertEqual(payload.get('proxy_server'), 'ProxyVN (Tự động)')

        # Simulate proxy resolved message
        accounts[0]['proxy'] = {'server': 'http://160.250.166.38:10726'}
        report('ProxyVN • 160.250.166.38:10726 • gắn vào GPM.')

        emitted_after = []
        while not events.empty():
            emitted_after.append(events.get_nowait())

        re_exec = [e for e in emitted_after if e[0] == 'task_execution']
        self.assertTrue(len(re_exec) >= 1)
        self.assertEqual(re_exec[-1][2].get('proxy_server'), '160.250.166.38:10726')


if __name__ == '__main__':
    unittest.main()
