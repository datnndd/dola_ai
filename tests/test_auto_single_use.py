import queue
import tempfile
import threading
import unittest
from pathlib import Path

from auto_jobs import AutoJobs, video_name


class Client:
    pass


class Journal:
    def __init__(self):
        self.rows = {}

    def finish(self, *args, **kwargs):
        return None


class SingleUseAccountTests(unittest.TestCase):
    def test_logo_suffix_is_added_once(self):
        self.assertEqual(video_name({'video_index':2, '_logo_output':True}), '2_logo')
        self.assertEqual(video_name({'task_code':'job_logo', '_logo_output':True}), 'job_logo')
        self.assertEqual(video_name({'video_index':2}), '2')

    def test_success_retires_account_and_forces_one_tab(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = {'concurrency': 1, 'tabs_per_profile': 5, 'output': folder,
                        'one_job_per_account': True, 'proxy_source': 'manual'}
            accounts = [{'id':'a','name':'Account A'}, {'id':'b','name':'Account B'}]
            runner = AutoJobs(settings, accounts, folder, queue.Queue(), threading.Event(), {},
                              client_factory=lambda key: Client())
            runner.journal = Journal()
            self.assertEqual(runner.capacity, 1)
            slot = runner.slots[0]
            slot.update(busy=1, runner_active=True)
            task = {'id':'t','stt':'1','task_code':'job','video_index':1,'auto_job':True}
            source = Path(folder) / 'source.mp4'
            source.write_bytes(b'video')
            runner.settle(slot, task, path=source)
            self.assertTrue(slot['blocked'])
            self.assertEqual(runner.excluded['a'], float('inf'))
            self.assertIn('a', runner.used_accounts)
            self.assertEqual(slot['busy'], 0)


if __name__ == '__main__':
    unittest.main()
