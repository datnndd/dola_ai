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
        self.assertEqual(names['c_user']['sameSite'], 'Lax')
        self.assertTrue(names['c_user']['secure'])
        self.assertTrue(names['c_user']['domain'].startswith('.'))
        self.assertEqual(names['xs']['sameSite'], 'Lax')
        self.assertTrue(names['xs']['secure'])

    def test_parse_pipe_format(self):
        line = "100012345678|mypassword|2fa_secret|c_user=100012345678; xs=32%3Aabc123xyz|email@example.com"
        cookies = facebook_cookie_from_line(line)
        self.assertEqual(len(cookies), 2)
        names = {c['name']: c for c in cookies}
        self.assertEqual(names['c_user']['value'], '100012345678')
        self.assertEqual(names['c_user']['sameSite'], 'Lax')

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

    def test_handle_facebook_oauth_tab_error_body(self):
        tab = MagicMock()
        tab.url = 'https://www.facebook.com/dialog/oauth?client_id=123'
        tab.inner_text.return_value = 'Sorry, something went wrong. Please try closing and re-opening your browser window.'
        reports = []
        handled = _handle_facebook_oauth_tab(tab, report=reports.append)
        self.assertFalse(handled)
        self.assertTrue(any('Sorry, something went wrong' in r for r in reports))

    def test_handle_facebook_oauth_tab_password_prompt(self):
        tab = MagicMock()
        tab.url = 'https://www.facebook.com/dialog/oauth?client_id=123'
        tab.inner_text.return_value = 'Log in to Facebook'
        pwd_locator = MagicMock()
        pwd_locator.count.return_value = 1
        pwd_input = MagicMock()
        pwd_input.is_visible.return_value = True
        pwd_locator.nth.return_value = pwd_input

        def locator_side_effect(selector):
            loc = MagicMock()
            if 'password' in selector or 'pass' in selector:
                return pwd_locator
            loc.count.return_value = 0
            return loc

        tab.locator.side_effect = locator_side_effect
        reports = []
        handled = _handle_facebook_oauth_tab(tab, report=reports.append)
        self.assertFalse(handled)
        self.assertTrue(any('yêu cầu nhập mật khẩu' in r for r in reports))

    def test_facebook_cookie_domain_normalization(self):
        line = '[{"name": "c_user", "value": "123", "domain": "www.facebook.com"}, {"name": "xs", "value": "abc", "domain": "facebook.com"}]'
        cookies = parse_facebook_cookie_line(line)
        for c in cookies:
            self.assertEqual(c['domain'], '.facebook.com')

    def test_parse_multi_segment_pipe_format(self):
        line = "61596205320801|RISATUL@03|RKUJDI754SALVOV3|dpr=2; datr=abc|sb=def; m_pixel_ratio=2|c_user=123|xs=456|fr=ghi"
        cookies = facebook_cookie_from_line(line)
        names = {c['name']: c['value'] for c in cookies}
        self.assertIn('datr', names)
        self.assertIn('sb', names)
        self.assertIn('c_user', names)
        self.assertIn('xs', names)
        self.assertIn('fr', names)
        self.assertEqual(names['c_user'], '123')
        self.assertEqual(names['xs'], '456')
        self.assertEqual(names['datr'], 'abc')

    def test_parse_pipe_prefixed_cookie_name(self):
        line = "61596104491667|sakibvai10@31|BUMZMOUGSHVILODY|datr=wYnn; sb=abc; c_user=123; xs=456"
        cookies = facebook_cookie_from_line(line)
        names = {c['name']: c['value'] for c in cookies}
        self.assertIn('datr', names)
        self.assertEqual(names['datr'], 'wYnn')
        self.assertNotIn('61596104491667|sakibvai10@31|BUMZMOUGSHVILODY|datr', names)

    def test_parse_quoted_and_whitespace_values(self):
        line = 'c_user= "1000123" ; xs="abc-456" '
        cookies = parse_facebook_cookie_line(line)
        names = {c['name']: c['value'] for c in cookies}
        self.assertEqual(names['c_user'], '1000123')
        self.assertEqual(names['xs'], 'abc-456')

    def test_build_facebook_cookie_accounts_default_mode(self):
        from studio_core import build_facebook_cookie_accounts
        lines = ["c_user=123; xs=456", "c_user=789; xs=012"]
        built_off = build_facebook_cookie_accounts(lines, [], default_mode='off')
        self.assertEqual(len(built_off), 2)
        self.assertEqual(built_off[0][2], 'off')
        self.assertEqual(built_off[0][1], {})
        self.assertEqual(built_off[1][2], 'off')

        built_default = build_facebook_cookie_accounts(lines, ["127.0.0.1:8080"], default_mode='default')
        self.assertEqual(built_default[0][2], 'manual')
        self.assertEqual(built_default[1][2], 'default')


if __name__ == '__main__':
    unittest.main()
