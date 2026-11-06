import unittest
from unittest.mock import MagicMock, patch
import time
from account_health import lock_for_work, work_locked


class TestWorkLockedPersistence(unittest.TestCase):
    def test_account_work_lock_detection(self):
        acc = {'id': 'acc_test', 'name': 'Tester'}
        self.assertFalse(work_locked(acc))
        future = time.time() + 3600
        lock_for_work(acc, until=future)
        self.assertTrue(work_locked(acc))

    def test_auto_jobs_converts_work_locked_account_to_fast(self):
        from auto_jobs import AutoJobs
        import queue

        settings = {'dola_mode': 'pro', 'output': 'downloads', 'model': 'Seedance 2.5', 'duration': '15'}
        acc = {'id': 'acc1', 'name': 'Sakio Hasab', 'proxy': {}, 'work_locked_until': time.time() + 3600}
        events = queue.Queue()

        aj = AutoJobs.__new__(AutoJobs)
        aj.settings = settings
        aj.accounts = [acc]
        aj.events = events
        aj.lock = MagicMock()
        aj.journal = None
        aj.log = MagicMock()

        task = {'id': 't1', 'name': 'T1', 'prompt': 'test'}
        report = aj.report_for(task, acc)

        # Check task_execution event
        emitted = []
        while not events.empty():
            emitted.append(events.get_nowait())

        exec_events = [e for e in emitted if e[0] == 'task_execution']
        self.assertTrue(len(exec_events) >= 1)
        # Should be converted to fast
        self.assertEqual(task.get('options', {}).get('dola_mode'), 'fast')

    def test_auto_jobs_emits_work_locked_on_message(self):
        from auto_jobs import AutoJobs
        import queue

        settings = {'dola_mode': 'pro', 'output': 'downloads', 'model': 'Seedance 2.5', 'duration': '15'}
        acc = {'id': 'acc2', 'name': 'Eva', 'proxy': {}}
        events = queue.Queue()

        aj = AutoJobs.__new__(AutoJobs)
        aj.settings = settings
        aj.accounts = [acc]
        aj.slots = [dict(account=acc, inbox=queue.Queue(), busy=0, blocked=False)]
        aj.events = events
        aj.lock = MagicMock()
        aj.journal = None
        aj.log = MagicMock()

        task = {'id': 't2', 'name': 'T2', 'prompt': 'test'}
        report = aj.report_for(task, acc)

        future = time.time() + 7200
        report(f'[Khóa Work] {future}')

        emitted = []
        while not events.empty():
            emitted.append(events.get_nowait())

        wl_events = [e for e in emitted if e[0] == 'work_locked']
        self.assertTrue(len(wl_events) >= 1)
        self.assertEqual(wl_events[0][1], 'acc2')
        self.assertTrue(work_locked(acc))


if __name__ == '__main__':
    unittest.main()
