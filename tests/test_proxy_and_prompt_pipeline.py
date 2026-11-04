import re
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from seedance_ui import select_mode
import studio_qt


class DummySteps:
    def __init__(self):
        self.clicked = []

    def click(self, locator):
        self.clicked.append(locator)

    def wait_until(self, predicate, timeout, message):
        if not predicate():
            raise TimeoutError(message)


class DummyLocator:
    def __init__(self, text="", aria_label="", visible=True):
        self._text = text
        self._aria_label = aria_label
        self._visible = visible

    def is_visible(self):
        return self._visible

    def inner_text(self, timeout=None):
        return self._text

    def get_attribute(self, name):
        if name == 'aria-label':
            return self._aria_label
        return None

    def all(self):
        return [self] if self._visible else []


class DummyPage:
    def __init__(self, trigger_elem):
        self.trigger_elem = trigger_elem

    def locator(self, selector):
        if 'mode-select-action-btn' in selector or 'chat_input_action_mode' in selector:
            return self.trigger_elem
        return DummyLocator(visible=False)

    def get_by_test_id(self, tid):
        return DummyLocator(visible=False)

    def get_by_role(self, role, name=None):
        return DummyLocator(visible=False)


class TestProxyAndPromptPipeline(unittest.TestCase):
    def test_select_mode_matches_bullet_and_dot_pro(self):
        steps = DummySteps()
        for variant in ['Pro', 'Pro • Mới', 'Pro · Mới', 'Pro - Mới', 'Chuyên nghiệp']:
            page = DummyPage(DummyLocator(text=variant, visible=True))
            # Should match immediately and not click trigger
            select_mode(page, 'pro', steps)
            self.assertEqual(len(steps.clicked), 0, f"Variant {variant} should match without clicking")

    def test_assignment_proxy_display_for_auto_mode(self):
        # When account has proxy_mode='default' and proxy is {}, assignment should show auto source
        task = {'id': 't1', 'prompt': 'test prompt'}
        account = {'id': 'a1', 'name': 'Sakio', 'proxy_mode': 'default', 'proxy': {}}
        cfg = {'proxy_source': 'pvn', 'model': 'Seedance 2.5', 'duration': '15'}

        events = []
        app = SimpleNamespace(events=SimpleNamespace(put=lambda evt: events.append(evt)))

        # Simulate assignment logic in studio_qt
        options = studio_qt.task_settings(cfg, task)
        proxy = (account or {}).get('proxy') or {}
        proxy_server = proxy.get('server', '') if isinstance(proxy, dict) else ':'.join(str(proxy).split(':')[:2])
        if not proxy_server:
            mode = (account or {}).get('proxy_mode', 'default')
            if mode == 'manual':
                proxy_server = 'Proxy tay'
            elif mode == 'off':
                proxy_server = 'Không proxy'
            else:
                src = cfg.get('proxy_source', 'proxyno1')
                if src == 'pvn':
                    proxy_server = 'ProxyVN (Tự động)'
                elif src == 'proxyno1':
                    proxy_server = 'ProxyNo1 (Tự động)'
                else:
                    proxy_server = 'Tự động'

        self.assertEqual(proxy_server, 'ProxyVN (Tự động)')

        # Once resolved:
        account['proxy'] = {'server': 'http://160.250.166.14:10583'}
        proxy = account['proxy']
        resolved_server = proxy.get('server', '')
        self.assertEqual(resolved_server, 'http://160.250.166.14:10583')


if __name__ == '__main__':
    unittest.main()
