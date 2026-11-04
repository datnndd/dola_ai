import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from seedance_30s import enabled, prompts, follow
from task_runtime import task_settings
from auto_jobs import AutoJobs


def drain(gen):
    try:
        while True: next(gen)
    except StopIteration as result:
        return result.value


class ThirtyTests(unittest.TestCase):
    def test_web_auto_supports_both_durations_and_optional_override(self):
        runner = object.__new__(AutoJobs)
        base = {'stt': 1, 'job_id': 'job', 'lease': 'lease', 'prompt_video': 'scene',
                'index': 1, 'count': 1}
        runner.settings = {'dola_mode': 'skill30', 'duration': '15', 'auto_duration_policy': 'web'}
        self.assertEqual(runner.build_task(dict(base, duration='15'))['options'],
                         {'duration': '15', 'dola_mode': 'pro'})
        self.assertEqual(runner.build_task(dict(base, duration='30'))['options'],
                         {'duration': '30', 'dola_mode': 'pro'})
        runner.settings['auto_duration_policy'] = '15'
        self.assertEqual(runner.build_task(dict(base, duration='30'))['options']['duration'], '15')
        runner.settings['auto_duration_policy'] = '30'
        self.assertEqual(runner.build_task(dict(base, duration='15'))['options']['duration'], '30')

    def test_route_and_explicit_parts(self):
        self.assertTrue(enabled({'duration': '30', 'dola_mode': 'pro'}))
        self.assertFalse(enabled({'duration': '15', 'dola_mode': 'skill30'}))
        self.assertFalse(enabled({'duration': '15'}))
        legacy = task_settings({'duration': '15', 'dola_mode': 'skill30'}, {})
        self.assertEqual((legacy['duration'], legacy['dola_mode']), ('15', 'pro'))
        first, second = prompts('mở cửa || bước ra ngoài')
        self.assertIn('mở cửa', first)
        self.assertNotIn('bước ra ngoài', first)
        self.assertIn('bước ra ngoài', second)
        with self.assertRaises(ValueError): prompts('x || ')

    def test_two_turns_checkpoint_and_recovery_never_resends(self):
        with tempfile.TemporaryDirectory() as tmp:
            events = []
            def poll(page, context, url, source, settings, *args, **kwargs):
                events.append(('poll', settings.get('_after_user_turn')))
                source.write_bytes(b'video')
                yield
                return ({'evidence':'dola_download_url','watermark_status':'unknown'}
                        if len([event for event in events if event[0] == 'poll']) == 1
                        else {'evidence':'fallback_unwatermarked','watermark_status':'clean'})
            def submit(editor, should_send):
                self.assertTrue(should_send())
                events.append(('send', None))
            steps = SimpleNamespace(type_prompt=lambda *a: None, submit=submit, wait=lambda *a: None)
            page = SimpleNamespace(locator=lambda *a: SimpleNamespace(first=object()))
            reader = SimpleNamespace(read=lambda: {'user_turn': 100})
            args = (page, None, 'https://www.dola.com/chat/123', Path(tmp)/'out.mp4',
                    {'output':tmp,'duration':'15','dola_mode':'pro','seedance_flow':'pro_chat'},
                    {'prompt':'chó mèo'}, threading.Event(), lambda *a: None, steps)
            with patch('seedance_video.follow_conversation', poll), patch('seedance_video.ConversationReader', return_value=reader), patch('seedance_30s.merge'):
                result = drain(follow(*args))
                self.assertEqual(events, [('poll',None),('send',None),('poll',100)])
                self.assertEqual(result['watermark_status'], 'marked')
                events.clear()
                drain(follow(*args, resume=True))
                self.assertEqual(events, [])


if __name__ == '__main__': unittest.main()
