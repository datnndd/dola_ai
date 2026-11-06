import unittest
from unittest.mock import MagicMock, patch
import time
import queue
import threading
from account_health import lock_for_work, work_locked


class TestWorkLockedPersistence(unittest.TestCase):
    def auto_runner(self, accounts, auto_retry=False):
        from auto_jobs import AutoJobs

        settings = {'dola_mode': 'pro', 'output': 'downloads', 'model': 'Seedance 2.5',
                    'duration': '15', 'concurrency': 1, 'auto_retry': auto_retry}
        runner = AutoJobs(settings, accounts, '.', queue.Queue(), threading.Event(), {},
                          client_factory=lambda key: MagicMock())
        runner.journal = MagicMock()
        runner.journal.lock = threading.RLock()
        runner.journal.rows = {'t': {}}
        runner.journal.pending.return_value = []
        return runner

    def test_auto_skips_already_work_locked_profile(self):
        future = time.time() + 3600
        runner = self.auto_runner([{'id': 'a', 'name': 'A', 'work_locked_until': future},
                                   {'id': 'b', 'name': 'B'}])

        self.assertIs(runner.reserve(), runner.slots[1])
        self.assertEqual(runner.excluded['a'], future)

    def test_auto_work_refusal_retries_same_lease_on_other_profile_without_auto_retry(self):
        from account_health import WorkLimitReached

        runner = self.auto_runner([{'id': 'a', 'name': 'A'}, {'id': 'b', 'name': 'B'}])
        first = runner.reserve()
        task = {'id': 't', 'name': 'Job T', 'lease': 'lease-t', 'stt': '1',
                'prompt': 'video', '_dola_submission_attempted': True}
        runner.settle(first, task, error=WorkLimitReached(time.time() + 3600))

        self.assertIn('t', runner.held)
        self.assertTrue(first['blocked'])
        self.assertTrue(work_locked(runner.accounts[0]))
        self.assertEqual(runner.journal.rows['t']['waiting_retry'], True)
        self.assertFalse(runner.defer_claims())
        second = runner.reserve()
        self.assertIs(second, runner.slots[1])
        retry = runner.take_claimed()
        self.assertEqual(retry['lease'], 'lease-t')
        self.assertNotIn('_dola_submission_attempted', retry)
        self.assertIs(runner.route_retry(second, retry), second)
        self.assertEqual(second['account']['id'], 'b')
        runner.journal.finish.assert_not_called()

    def test_auto_waits_when_every_profile_is_work_locked(self):
        from account_health import WorkLimitReached

        runner = self.auto_runner([{'id': 'a', 'name': 'A'}])
        first = runner.reserve()
        task = {'id': 't', 'name': 'Job T', 'lease': 'lease-t',
                '_dola_submission_attempted': True}
        runner.settle(first, task, error=WorkLimitReached(time.time() + 3600))

        self.assertIn('t', runner.held)
        self.assertIsNone(runner.reserve())
        self.assertTrue(runner.waiting_for_midnight())
        runner.journal.finish.assert_not_called()

    def test_account_work_lock_detection(self):
        acc = {'id': 'acc_test', 'name': 'Tester'}
        self.assertFalse(work_locked(acc))
        future = time.time() + 3600
        lock_for_work(acc, until=future)
        self.assertTrue(work_locked(acc))

    def test_auto_jobs_does_not_convert_work_locked_account_to_fast(self):
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
        # Should NOT be converted to fast
        self.assertNotEqual(task.get('options', {}).get('dola_mode'), 'fast')

    def test_auto_jobs_emits_work_locked_on_message(self):
        from auto_jobs import AutoJobs
        import queue

        settings = {'dola_mode': 'pro', 'output': 'downloads', 'model': 'Seedance 2.5', 'duration': '15'}
        acc = {'id': 'acc2', 'name': 'Eva', 'proxy': {}}
        events = queue.Queue()

        aj = AutoJobs.__new__(AutoJobs)
        aj.settings = settings
        aj.accounts = [acc]
        aj.excluded = {}
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
        self.assertTrue(aj.slots[0]['blocked'])
        self.assertGreater(aj.excluded['acc2'], time.time())


if __name__ == '__main__':
    unittest.main()
