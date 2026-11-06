"""Unit tests for Dola mode selection (Work vs Chat) and ensuring Create Videos button is not clicked,
plus automatic fallback to Chat mode when Work mode is limited."""
import unittest
from unittest.mock import MagicMock, call

from seedance_ui import select_mode, prepare, form_flow
from dola_limits import is_work_limit_reached
from dola_browser import raise_if_credit_refused
from account_health import CreditExhausted


class MockLocator:
    def __init__(self, text="", aria_label="", visible=True, test_id="", aria_disabled=None):
        self._text = text
        self._aria_label = aria_label
        self._visible = visible
        self._test_id = test_id
        self._aria_disabled = aria_disabled

    def is_visible(self):
        return self._visible

    def inner_text(self, timeout=None):
        return self._text

    def get_attribute(self, name):
        if name == 'aria-label':
            return self._aria_label
        if name == 'data-testid':
            return self._test_id
        if name == 'aria-disabled':
            return self._aria_disabled
        return None

    def all(self):
        return [self] if self._visible else []

    def count(self):
        return 1 if self._visible else 0

    @property
    def first(self):
        return self

    def locator(self, *args, **kwargs):
        return MockLocator(visible=False)


class MockSteps:
    def __init__(self):
        self.clicked = []
        self.waited = []

    def click(self, elem):
        self.clicked.append(elem)

    def wait(self, duration):
        self.waited.append(duration)

    def wait_until(self, predicate, timeout=5, message=''):
        if not predicate():
            raise TimeoutError(message)


class TestDolaModeWorkChat(unittest.TestCase):
    def test_select_mode_pro_matches_work_and_pro_variants(self):
        steps = MockSteps()
        work_variants = ['Work', 'Work >', '⚛ Work >', 'Work • Mới', 'Pro', 'Pro • Mới', 'Chuyên nghiệp']
        for variant in work_variants:
            elem = MockLocator(text=variant, visible=True)
            page = MagicMock()
            page.locator.side_effect = lambda sel: elem if 'mode-select-action-btn' in sel or 'chat_input_action_mode' in sel else MockLocator(visible=False)
            page.get_by_test_id.return_value = MockLocator(visible=False)
            page.get_by_role.return_value = MockLocator(visible=False)
            steps.clicked.clear()

            select_mode(page, 'pro', steps)
            self.assertEqual(len(steps.clicked), 0, f"Variant '{variant}' should match Work/Pro without clicking")

    def test_select_mode_non_pro_matches_chat_and_fast_variants(self):
        steps = MockSteps()
        chat_variants = ['Chat', 'Chat >', '⚡ Chat >', 'Chat • Mới', 'Fast', 'Nhanh', 'Trò chuyện']
        for mode in ['fast', 'default', 'chat']:
            for variant in chat_variants:
                elem = MockLocator(text=variant, visible=True)
                page = MagicMock()
                page.locator.side_effect = lambda sel: elem if 'mode-select-action-btn' in sel or 'chat_input_action_mode' in sel else MockLocator(visible=False)
                page.get_by_test_id.return_value = MockLocator(visible=False)
                page.get_by_role.return_value = MockLocator(visible=False)
                steps.clicked.clear()

                select_mode(page, mode, steps)
                self.assertEqual(len(steps.clicked), 0, f"Variant '{variant}' for mode '{mode}' should match Chat without clicking")

    def test_select_mode_switches_chat_to_work_when_pro_selected(self):
        trigger = MockLocator(text='⚡ Chat >', visible=True)
        work_option = MockLocator(text='Work\nFor complex, longer tasks', visible=True)

        class SwitchingSteps:
            def __init__(self):
                self.clicked = []

            def click(self, elem):
                self.clicked.append(elem)
                if elem == work_option:
                    trigger._text = '⚛ Work >'

            def wait_until(self, predicate, timeout=5, message=''):
                if not predicate():
                    raise TimeoutError(message)

        steps = SwitchingSteps()
        page = MagicMock()

        def locator_handler(sel):
            if 'mode-select-action-btn' in sel or 'chat_input_action_mode' in sel:
                return trigger
            if 'menuitem' in sel or 'role="menuitem"' in sel:
                return work_option
            return MockLocator(visible=False)

        page.locator.side_effect = locator_handler
        page.get_by_test_id.return_value = MockLocator(visible=False)
        page.get_by_role.return_value = MockLocator(visible=False)
        page.get_by_text.return_value = MockLocator(visible=False)

        select_mode(page, 'pro', steps)
        self.assertIn(trigger, steps.clicked, "Should click trigger button to open menu")
        self.assertIn(work_option, steps.clicked, "Should click Work option in menu")
        self.assertEqual(trigger._text, '⚛ Work >')

    def test_select_mode_switches_work_to_chat_when_non_pro_selected(self):
        trigger = MockLocator(text='⚛ Work >', visible=True)
        chat_option = MockLocator(text='Chat\nFor quick answers', visible=True)

        class SwitchingSteps:
            def __init__(self):
                self.clicked = []

            def click(self, elem):
                self.clicked.append(elem)
                if elem == chat_option:
                    trigger._text = '⚡ Chat >'

            def wait_until(self, predicate, timeout=5, message=''):
                if not predicate():
                    raise TimeoutError(message)

        steps = SwitchingSteps()
        page = MagicMock()

        def locator_handler(sel):
            if 'mode-select-action-btn' in sel or 'chat_input_action_mode' in sel:
                return trigger
            if 'menuitem' in sel or 'role="menuitem"' in sel:
                return chat_option
            return MockLocator(visible=False)

        page.locator.side_effect = locator_handler
        page.get_by_test_id.return_value = MockLocator(visible=False)
        page.get_by_role.return_value = MockLocator(visible=False)
        page.get_by_text.return_value = MockLocator(visible=False)

        select_mode(page, 'fast', steps)
        self.assertIn(trigger, steps.clicked, "Should click trigger button to open menu")
        self.assertIn(chat_option, steps.clicked, "Should click Chat option in menu")
        self.assertEqual(trigger._text, '⚡ Chat >')

    def test_prepare_pro_does_not_click_create_videos(self):
        reports = []
        steps = MockSteps()
        trigger = MockLocator(text='Work >', visible=True)
        create_video_btn = MockLocator(text='Create Videos', visible=True)

        page = MagicMock()
        def locator_handler(sel):
            if 'mode-select-action-btn' in sel or 'chat_input_action_mode' in sel:
                return trigger
            if 'skill_bar_button_17' in sel or 'Create Videos' in sel:
                return create_video_btn
            return MockLocator(visible=False)

        page.locator.side_effect = locator_handler
        page.get_by_test_id.return_value = MockLocator(visible=False)
        page.get_by_role.return_value = MockLocator(visible=False)

        settings = {'dola_mode': 'pro', 'duration': '15', 'seedance_flow': 'auto'}
        prepare(page, settings, steps, lambda msg: reports.append(msg))

        self.assertNotIn(create_video_btn, steps.clicked, "Should NOT click Create Videos button")
        self.assertTrue(any('Chế độ Work đã sẵn sàng' in r for r in reports))

    def test_prepare_non_pro_does_not_click_create_videos(self):
        reports = []
        steps = MockSteps()
        trigger = MockLocator(text='Chat >', visible=True)
        create_video_btn = MockLocator(text='Create Videos', visible=True)

        page = MagicMock()
        def locator_handler(sel):
            if 'mode-select-action-btn' in sel or 'chat_input_action_mode' in sel:
                return trigger
            if 'skill_bar_button_17' in sel or 'Create Videos' in sel:
                return create_video_btn
            return MockLocator(visible=False)

        page.locator.side_effect = locator_handler
        page.get_by_test_id.return_value = MockLocator(visible=False)
        page.get_by_role.return_value = MockLocator(visible=False)

        settings = {'dola_mode': 'fast', 'duration': '15', 'seedance_flow': 'auto'}
        prepare(page, settings, steps, lambda msg: reports.append(msg))

        self.assertNotIn(create_video_btn, steps.clicked, "Should NOT click Create Videos button")
        self.assertTrue(any('Chế độ Chat đã sẵn sàng' in r for r in reports))

    def test_is_work_limit_reached(self):
        self.assertTrue(is_work_limit_reached('Bạn đã đạt giới hạn chế độ Work. Vui lòng chuyển sang Chat'))
        self.assertTrue(is_work_limit_reached('You have reached the daily limit for Work mode. Switch to Chat mode'))
        self.assertTrue(is_work_limit_reached('Bạn đã đạt giới hạn 7 ngày miễn phí. Hãy quay lại vào 22:32, 14 tháng Mười, 2026 (GMT+07:00) hoặc sử dụng chế độ nhanh'))
        self.assertTrue(is_work_limit_reached('Đã hết lượt sử dụng Pro. Bạn có thể tiếp tục với chế độ Chat.'))
        self.assertFalse(is_work_limit_reached('Không đủ điểm tín dụng để tạo video. Cần 10 điểm nhưng bạn còn 0 điểm.'))
        self.assertFalse(is_work_limit_reached('Video đang được tạo, vui lòng đợi...'))

    def test_select_mode_falls_back_to_chat_when_work_is_disabled(self):
        trigger = MockLocator(text='⚡ Chat >', visible=True)
        # Work option is marked as disabled or having limit
        work_option = MockLocator(text='Work\nĐạt giới hạn 0/5', visible=True, aria_disabled='true')
        chat_option = MockLocator(text='Chat\nFor quick answers', visible=True)

        class SwitchingSteps:
            def __init__(self):
                self.clicked = []

            def click(self, elem):
                self.clicked.append(elem)
                if elem == chat_option:
                    trigger._text = '⚡ Chat >'

            def wait_until(self, predicate, timeout=5, message=''):
                if not predicate():
                    raise TimeoutError(message)

        steps = SwitchingSteps()
        page = MagicMock()

        def locator_handler(sel):
            if 'mode-select-action-btn' in sel or 'chat_input_action_mode' in sel:
                return trigger
            if 'role="menuitem"' in sel or 'role="menuitemradio"' in sel or '[role="option"]' in sel or 'menuitem' in sel:
                # Return both options when queried
                holder = MagicMock()
                holder.all.return_value = [work_option, chat_option]
                return holder
            return MockLocator(visible=False)

        page.locator.side_effect = locator_handler
        page.get_by_test_id.return_value = MockLocator(visible=False)
        page.get_by_role.return_value = MockLocator(visible=False)
        page.get_by_text.return_value = MockLocator(visible=False)

        reports = []
        result = select_mode(page, 'pro', steps, report=lambda msg: reports.append(msg))
        self.assertEqual(result, 'fast')
        self.assertIn(chat_option, steps.clicked)
        self.assertTrue(any('Chế độ Work đang bị khóa/hết hạn mức' in r for r in reports))

    def test_prepare_updates_settings_when_work_fallback_occurs(self):
        trigger = MockLocator(text='⚡ Chat >', visible=True)
        work_option = MockLocator(text='Work', visible=True, aria_disabled='true')
        chat_option = MockLocator(text='Chat', visible=True)

        class SwitchingSteps:
            def __init__(self):
                self.clicked = []

            def click(self, elem):
                self.clicked.append(elem)

            def wait_until(self, predicate, timeout=5, message=''):
                return True

        steps = SwitchingSteps()
        page = MagicMock()

        def locator_handler(sel):
            if 'mode-select-action-btn' in sel or 'chat_input_action_mode' in sel:
                return trigger
            if 'menuitem' in sel:
                holder = MagicMock()
                holder.all.return_value = [work_option, chat_option]
                return holder
            return MockLocator(visible=False)

        page.locator.side_effect = locator_handler
        page.get_by_test_id.return_value = MockLocator(visible=False)
        page.get_by_role.return_value = MockLocator(visible=False)
        page.get_by_text.return_value = MockLocator(visible=False)

        settings = {'dola_mode': 'pro', 'duration': '15', 'seedance_flow': 'auto'}
        reports = []
        prepare(page, settings, steps, lambda msg: reports.append(msg))

        self.assertEqual(settings['dola_mode'], 'fast')
        self.assertTrue(settings.get('_work_fallback_done'))
        self.assertTrue(any('Chế độ Chat đã sẵn sàng' in r for r in reports))

    def test_raise_if_credit_refused_defers_for_work_fallback(self):
        page = MagicMock()
        task = {'prompt': 'create a cat video'}
        limit_text = 'Bạn đã đạt giới hạn chế độ Work hôm nay. Vui lòng chuyển sang Chat'

        with unittest.mock.patch('dola_browser.is_generation_in_progress', return_value=False), \
             unittest.mock.patch('dola_browser.genuine_video_url', return_value=None), \
             unittest.mock.patch('dola_browser.credit_response_text', return_value=limit_text):

            # Case 1: Pro mode without fallback done -> does NOT raise CreditExhausted
            settings_pro = {'dola_mode': 'pro'}
            reports = []
            # Should not raise exception
            raise_if_credit_refused(page, task, lambda msg: reports.append(msg), settings=settings_pro)

            # Case 2: Pro mode with fallback already done -> raises CreditExhausted
            settings_done = {'dola_mode': 'pro', '_work_fallback_done': True}
            with self.assertRaises(CreditExhausted):
                raise_if_credit_refused(page, task, lambda msg: reports.append(msg), settings=settings_done)

    def test_is_generation_in_progress_with_stop_button(self):
        from dola_browser import is_generation_in_progress
        page = MagicMock()
        page.is_closed.return_value = False

        def locator_side_effect(sel):
            mock = MagicMock()
            if 'Stop' in sel or 'Dừng' in sel or 'stop' in sel:
                mock.count.return_value = 1
                mock.first.is_visible.return_value = True
            else:
                mock.count.return_value = 0
            return mock

        page.locator.side_effect = locator_side_effect
        self.assertTrue(is_generation_in_progress(page))

    def test_is_generation_in_progress_historic_planning_text_not_blocking(self):
        from dola_browser import is_generation_in_progress
        page = MagicMock()
        page.is_closed.return_value = False

        # Long conversation where first paragraph had 'Tôi sẽ tạo video' but tail is completed
        historic_text = "Tôi sẽ tạo video theo kịch bản của bạn. Trước tiên tôi lên ý tưởng... " + ("Đoạn văn bản chi tiết khác. " * 30) + "Video đã sẵn sàng để tải xuống."

        def locator_side_effect(sel):
            mock = MagicMock()
            if sel.startswith('main'):
                mock.count.return_value = 1
                elem = MagicMock()
                elem.inner_text.return_value = historic_text
                mock.first = elem
            else:
                mock.count.return_value = 0
            return mock

        page.locator.side_effect = locator_side_effect
        # Since tail text does not have active generating words, it should be False
        self.assertFalse(is_generation_in_progress(page))


if __name__ == '__main__':
    unittest.main()
