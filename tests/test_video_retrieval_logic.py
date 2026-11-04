import unittest
from unittest.mock import MagicMock, patch
from dola_browser import is_generation_in_progress, raise_if_credit_refused
from account_health import CreditExhausted


class TestVideoRetrievalLogic(unittest.TestCase):
    def test_generation_in_progress_by_selector(self):
        page = MagicMock()
        page.is_closed.return_value = False
        spinner = MagicMock()
        spinner.is_visible.return_value = True

        def locator_mock(selector):
            loc = MagicMock()
            if 'loading' in selector or 'spinner' in selector:
                loc.count.return_value = 1
                loc.nth.return_value = spinner
            else:
                loc.count.return_value = 0
            return loc

        page.locator.side_effect = locator_mock
        self.assertTrue(is_generation_in_progress(page))

    def test_generation_in_progress_by_text(self):
        page = MagicMock()
        page.is_closed.return_value = False

        def locator_mock(selector):
            loc = MagicMock()
            if selector.startswith('main'):
                loc.count.return_value = 1
                main_elem = MagicMock()
                main_elem.inner_text.return_value = 'Đang tạo video cho bạn, vui lòng đợi trong giây lát (45%)...'
                loc.first = main_elem
            else:
                loc.count.return_value = 0
            return loc

        page.locator.side_effect = locator_mock
        self.assertTrue(is_generation_in_progress(page))

    def test_raise_if_credit_refused_ignored_when_generation_in_progress(self):
        page = MagicMock()
        page.is_closed.return_value = False
        task = {'prompt': 'test video'}
        reports = []

        with patch('dola_browser.is_generation_in_progress', return_value=True), \
             patch('dola_browser.credit_response_text', return_value='Tài khoản không đủ điểm tín dụng Pro.'):
            # Should NOT raise CreditExhausted because generation has started!
            raise_if_credit_refused(page, task, reports.append)
            self.assertEqual(len(reports), 0)

    def test_raise_if_credit_refused_ignored_when_video_or_file_present(self):
        page = MagicMock()
        page.is_closed.return_value = False
        task = {'prompt': 'test video'}
        reports = []

        with patch('dola_browser.is_generation_in_progress', return_value=False), \
             patch('dola_browser.genuine_video_url', return_value='https://dola.com/video/123.mp4'), \
             patch('dola_browser.credit_response_text', return_value='Không đủ điểm tín dụng.'):
            # Should NOT raise CreditExhausted because video is already present!
            raise_if_credit_refused(page, task, reports.append)
            self.assertEqual(len(reports), 0)

    def test_raise_if_credit_refused_raises_when_turn_finished_with_no_generation(self):
        page = MagicMock()
        page.is_closed.return_value = False
        task = {'prompt': 'test video'}
        reports = []

        with patch('dola_browser.is_generation_in_progress', return_value=False), \
             patch('dola_browser.genuine_video_url', return_value=None), \
             patch('mp4_attachment.scan_files', return_value={'count': 0, 'urls': []}), \
             patch('dola_browser.latest_turn', return_value=(True, 'Không đủ điểm tín dụng')), \
             patch('dola_browser.credit_response_text', return_value='Không đủ điểm tín dụng để tạo video.'):
            with self.assertRaises(CreditExhausted):
                raise_if_credit_refused(page, task, reports.append)
            self.assertTrue(any('Không đủ điểm tín dụng' in r for r in reports))


if __name__ == '__main__':
    unittest.main()
