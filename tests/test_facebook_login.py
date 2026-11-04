import unittest
from unittest.mock import MagicMock, patch
from cookie_accounts import parse_facebook_cookie_line, facebook_cookie_from_line
from dola_browser import _find_facebook_button, _handle_facebook_oauth_tab, _trigger_facebook_login, _handle_age_confirmation

class TestFacebookCookie(unittest.TestCase):
    def test_parse_simple_cookie_string(self):
        line = "c_user=100012345678; xs=32%3Aabc123xyz"
        cookies = parse_facebook_cookie_line(line)
        self.assertEqual(len(cookies), 2)
        names = {c['name']: c for c in cookies}
        self.assertIn('c_user', names)
        self.assertIn('xs', names)
        self.assertEqual(names['c_user']['sameSite'], 'None')
        self.assertTrue(names['c_user']['secure'])
        self.assertTrue(names['c_user']['domain'].startswith('.'))
        self.assertEqual(names['xs']['sameSite'], 'None')
        self.assertTrue(names['xs']['secure'])

    def test_parse_pipe_format(self):
        line = "100012345678|mypassword|2fa_secret|c_user=100012345678; xs=32%3Aabc123xyz|email@example.com"
        cookies = facebook_cookie_from_line(line)
        self.assertEqual(len(cookies), 2)
        names = {c['name']: c for c in cookies}
        self.assertEqual(names['c_user']['value'], '100012345678')
        self.assertEqual(names['c_user']['sameSite'], 'None')

    def test_parse_json_cookie_format(self):
        import json
        json_str = json.dumps([
            {"name": "c_user", "value": "100012345678", "domain": "facebook.com", "sameSite": "no_restriction"},
            {"name": "xs", "value": "32%3Aabc123xyz", "domain": ".facebook.com", "sameSite": "Lax"}
        ])
        cookies = parse_facebook_cookie_line(json_str)
        names = {c['name']: c for c in cookies}
        self.assertEqual(names['c_user']['sameSite'], 'None')
        self.assertEqual(names['c_user']['domain'], '.facebook.com')
        self.assertEqual(names['xs']['sameSite'], 'Lax')


class TestFacebookBrowserLogin(unittest.TestCase):
    def test_find_facebook_button_found(self):
        page = MagicMock()
        btn = MagicMock()
        btn.is_visible.return_value = True

        def locator_side_effect(selector):
            loc = MagicMock()
            if selector == '[data-testid="login_third_facebook"]':
                loc.count.return_value = 1
                loc.first = btn
            else:
                loc.count.return_value = 0
            return loc

        page.locator.side_effect = locator_side_effect
        found = _find_facebook_button(page)
        self.assertEqual(found, btn)

    def test_handle_facebook_oauth_tab_clicks_continue(self):
        tab = MagicMock()
        tab.url = 'https://www.facebook.com/dialog/oauth?client_id=123'
        confirm_btn = MagicMock()
        confirm_btn.is_visible.return_value = True

        def locator_side_effect(selector):
            loc = MagicMock()
            if 'Tiếp tục dưới tên' in selector:
                loc.count.return_value = 1
                loc.first = confirm_btn
            else:
                loc.count.return_value = 0
            return loc

        tab.locator.side_effect = locator_side_effect
        reports = []
        handled = _handle_facebook_oauth_tab(tab, report=reports.append)
        self.assertTrue(handled)
        confirm_btn.click.assert_called_once()
        self.assertTrue(any('xác nhận quyền' in r for r in reports))

    def test_trigger_facebook_login_clicks_existing_fb_button(self):
        page = MagicMock()
        page.is_closed.return_value = False
        fb_btn = MagicMock()
        fb_btn.is_visible.return_value = True

        def locator_side_effect(selector):
            loc = MagicMock()
            if selector == '[data-testid="login_third_facebook"]':
                loc.count.return_value = 1
                loc.first = fb_btn
            else:
                loc.count.return_value = 0
            return loc

        page.locator.side_effect = locator_side_effect
        reports = []
        result = _trigger_facebook_login(MagicMock(), page, report=reports.append)
        self.assertTrue(result)
        fb_btn.click.assert_called_once()

    def test_handle_age_confirmation_clicks_age_button(self):
        tab = MagicMock()
        tab.is_closed.return_value = False
        tab.inner_text.return_value = 'Vui lòng xác nhận độ tuổi để tiếp tục sử dụng Dola AI. Bạn từ 18 tuổi trở lên?'
        btn = MagicMock()
        btn.is_visible.return_value = True

        def locator_side_effect(selector):
            loc = MagicMock()
            if '18 tuổi trở lên' in selector:
                loc.count.return_value = 1
                loc.first = btn
            else:
                loc.count.return_value = 0
            return loc

        tab.locator.side_effect = locator_side_effect
        reports = []
        handled = _handle_age_confirmation(tab, report=reports.append)
        self.assertTrue(handled)
        btn.click.assert_called_once()
        self.assertTrue(any('đủ 18 tuổi' in r for r in reports))

    def test_handle_age_confirmation_checks_box_and_confirms(self):
        tab = MagicMock()
        tab.is_closed.return_value = False
        tab.inner_text.return_value = 'Xác nhận tuổi: Tôi xác nhận tôi đủ 18 tuổi và đồng ý với điều khoản dịch vụ.'
        cb = MagicMock()
        cb.is_visible.return_value = True
        cb.is_checked.return_value = False
        confirm_btn = MagicMock()
        confirm_btn.is_visible.return_value = True
        confirm_btn.inner_text.return_value = 'Xác nhận'

        def locator_side_effect(selector):
            loc = MagicMock()
            if 'checkbox' in selector:
                loc.count.return_value = 1
                loc.nth.return_value = cb
            elif 'Xác nhận' in selector and not '18 tuổi' in selector:
                loc.count.return_value = 1
                loc.first = confirm_btn
                loc.nth.return_value = confirm_btn
            else:
                loc.count.return_value = 0
            return loc

        tab.locator.side_effect = locator_side_effect
        reports = []
        handled = _handle_age_confirmation(tab, report=reports.append)
        self.assertTrue(handled)
        cb.check.assert_called_once()
        confirm_btn.click.assert_called_once()

    def test_handle_age_confirmation_ignores_unrelated_page(self):
        tab = MagicMock()
        tab.is_closed.return_value = False
        tab.inner_text.return_value = 'Dola Chat - Nhập tin nhắn vào đây để trò chuyện.'

        def locator_side_effect(selector):
            loc = MagicMock()
            loc.count.return_value = 0
            return loc

        tab.locator.side_effect = locator_side_effect
        handled = _handle_age_confirmation(tab)
        self.assertFalse(handled)

    def test_handle_age_confirmation_exact_dola_dialog(self):
        tab = MagicMock()
        tab.is_closed.return_value = False
        tab.inner_text.return_value = 'Xác nhận tuổi của bạn\nĐể tiếp tục, vui lòng xác nhận bạn đã đủ 18 tuổi trở lên.\nKhông, tôi không phải\nXác nhận'
        cancel_btn = MagicMock()
        cancel_btn.is_visible.return_value = True
        cancel_btn.inner_text.return_value = 'Không, tôi không phải'

        confirm_btn = MagicMock()
        confirm_btn.is_visible.return_value = True
        confirm_btn.inner_text.return_value = 'Xác nhận'

        def locator_side_effect(selector):
            loc = MagicMock()
            if 'Xác nhận' in selector and not '18 tuổi' in selector:
                # Returns 2 buttons: "Không, tôi không phải" (if fuzzy) or "Xác nhận"
                loc.count.return_value = 1
                loc.nth.return_value = confirm_btn
            else:
                loc.count.return_value = 0
            return loc

        tab.locator.side_effect = locator_side_effect
        reports = []
        handled = _handle_age_confirmation(tab, report=reports.append)
        self.assertTrue(handled)
        confirm_btn.click.assert_called_once()
        self.assertTrue(any('Xác nhận' in r for r in reports))

    def test_trigger_facebook_login_clears_age_dialog_first(self):
        page = MagicMock()
        page.is_closed.return_value = False
        page.inner_text.return_value = 'Xác nhận tuổi của bạn\nĐể tiếp tục, vui lòng xác nhận bạn đã đủ 18 tuổi trở lên.'
        confirm_btn = MagicMock()
        confirm_btn.is_visible.return_value = True
        confirm_btn.inner_text.return_value = 'Xác nhận'

        fb_btn = MagicMock()
        fb_btn.is_visible.return_value = True

        def locator_side_effect(selector):
            loc = MagicMock()
            if selector == '[data-testid="login_third_facebook"]':
                loc.count.return_value = 1
                loc.first = fb_btn
            elif 'Xác nhận' in selector and not '18 tuổi' in selector:
                loc.count.return_value = 1
                loc.nth.return_value = confirm_btn
            else:
                loc.count.return_value = 0
            return loc

        page.locator.side_effect = locator_side_effect
        reports = []
        result = _trigger_facebook_login(MagicMock(), page, report=reports.append)
        self.assertTrue(result)
        confirm_btn.click.assert_called_once()
        fb_btn.click.assert_called_once()


if __name__ == '__main__':
    unittest.main()
