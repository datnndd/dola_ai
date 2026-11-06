import time
import unittest
from unittest.mock import MagicMock, patch

from account_health import (
    CreditExhausted, WorkLimitReached, RateLimitExceeded,
    is_credit_error, is_work_limit_error, is_rate_limit_error,
    credit_locked, work_locked, rate_limited, run_cooldown_locked,
    lock_for_credits, lock_for_work, lock_for_rate_limit,
    release_account_locks, is_account_available, account_status_badge,
    work_lock_text, rate_limit_text,
)
from account_selector import (
    sort_accounts_by_lru, select_best_account, get_earliest_recovery_time
)
from dola_limits import is_message_rate_limited, is_work_limit_reached


class TestAccountAvailabilityAndLimits(unittest.TestCase):

    def setUp(self):
        self.now = time.time()

    def test_rate_limit_detection(self):
        self.assertTrue(is_message_rate_limited("You have reached the message limit. Please try again later."))
        self.assertTrue(is_message_rate_limited("Too many requests, please slow down."))
        self.assertTrue(is_message_rate_limited("Bạn đang gửi tin nhắn quá nhanh, vui lòng đợi."))
        self.assertTrue(is_message_rate_limited("Lỗi 429: Too Many Requests"))
        self.assertFalse(is_message_rate_limited("Video generation started"))
        self.assertFalse(is_message_rate_limited("Hello, how can I help you today?"))

    def test_work_limit_detection(self):
        self.assertTrue(is_work_limit_reached("Bạn đã đạt giới hạn chế độ Work hôm nay."))
        self.assertTrue(is_work_limit_reached("Work mode daily limit exceeded. Switch to Chat mode."))
        self.assertFalse(is_work_limit_reached("Chế độ Chat đang hoạt động"))

    def test_exception_types_and_predicates(self):
        exc_credit = CreditExhausted(until=self.now + 3600)
        exc_work = WorkLimitReached(until=self.now + 1800)
        exc_rate = RateLimitExceeded(until=self.now + 300)

        self.assertTrue(is_credit_error(exc_credit))
        self.assertFalse(is_credit_error(exc_work))
        self.assertFalse(is_credit_error(exc_rate))

        self.assertTrue(is_work_limit_error(exc_work))
        self.assertFalse(is_work_limit_error(exc_credit))
        self.assertFalse(is_work_limit_error(exc_rate))

        self.assertTrue(is_rate_limit_error(exc_rate))
        self.assertFalse(is_rate_limit_error(exc_credit))
        self.assertFalse(is_rate_limit_error(exc_work))

    def test_is_account_available_modes(self):
        # 1. Normal active account
        acc = {'id': '1', 'name': 'Acc1', 'selected': True, 'live_state': 'live'}
        self.assertTrue(is_account_available(acc, mode='work', now=self.now))
        self.assertTrue(is_account_available(acc, mode='chat', now=self.now))

        # 2. Work locked account: available for chat, NOT available for work
        acc_work = {'id': '2', 'name': 'Acc2', 'selected': True, 'live_state': 'live',
                    'work_locked_until': self.now + 600}
        self.assertFalse(is_account_available(acc_work, mode='work', now=self.now))
        self.assertTrue(is_account_available(acc_work, mode='chat', now=self.now))

        # 3. Credit locked account: neither work nor chat
        acc_credit = {'id': '3', 'name': 'Acc3', 'selected': True, 'live_state': 'live',
                      'credit_locked_until': self.now + 600}
        self.assertFalse(is_account_available(acc_credit, mode='work', now=self.now))
        self.assertFalse(is_account_available(acc_credit, mode='chat', now=self.now))

        # 4. Rate limited account: neither work nor chat
        acc_rate = {'id': '4', 'name': 'Acc4', 'selected': True, 'live_state': 'live',
                    'rate_limit_until': self.now + 300}
        self.assertFalse(is_account_available(acc_rate, mode='work', now=self.now))
        self.assertFalse(is_account_available(acc_rate, mode='chat', now=self.now))

        # 5. Dead account: neither
        acc_dead = {'id': '5', 'name': 'Acc5', 'selected': True, 'live_state': 'die'}
        self.assertFalse(is_account_available(acc_dead, mode='work', now=self.now))
        self.assertFalse(is_account_available(acc_dead, mode='chat', now=self.now))

        # 6. Unselected account: neither when check_selected=True
        acc_unsel = {'id': '6', 'name': 'Acc6', 'selected': False, 'live_state': 'live'}
        self.assertFalse(is_account_available(acc_unsel, mode='work', check_selected=True, now=self.now))
        self.assertFalse(is_account_available(acc_unsel, mode='chat', check_selected=True, now=self.now))
        # But if check_selected=False, it is eligible
        self.assertTrue(is_account_available(acc_unsel, mode='chat', check_selected=False, now=self.now))

    def test_release_account_locks(self):
        past = self.now - 100
        future = self.now + 1000

        accounts = [
            # Expired locks -> should be released
            {'id': '1', 'name': 'Acc1', 'selected': False,
             'credit_locked_until': past, 'work_locked_until': past, 'rate_limit_until': past},
            # Active locks -> should NOT be released
            {'id': '2', 'name': 'Acc2', 'selected': True,
             'credit_locked_until': future, 'work_locked_until': future, 'rate_limit_until': future},
        ]

        res = release_account_locks(accounts, now=self.now)
        self.assertEqual(len(res['credits']), 1)
        self.assertEqual(res['credits'][0]['id'], '1')
        self.assertEqual(len(res['work']), 1)
        self.assertEqual(res['work'][0]['id'], '1')
        self.assertEqual(len(res['rate_limits']), 1)
        self.assertEqual(res['rate_limits'][0]['id'], '1')

        # Check that expired fields were removed
        self.assertNotIn('credit_locked_until', accounts[0])
        self.assertNotIn('work_locked_until', accounts[0])
        self.assertNotIn('rate_limit_until', accounts[0])
        self.assertTrue(accounts[0]['selected'])

        # Check that future fields remain
        self.assertEqual(accounts[1]['credit_locked_until'], future)
        self.assertEqual(accounts[1]['work_locked_until'], future)
        self.assertEqual(accounts[1]['rate_limit_until'], future)

    def test_sort_accounts_by_lru(self):
        a1 = {'id': '1', 'last_used_at': 100, 'selected': True}
        a2 = {'id': '2', 'last_used_at': 50, 'selected': True}
        a3 = {'id': '3', 'selected': True}  # never used (0)

        sorted_accs = sort_accounts_by_lru([a1, a2, a3])
        self.assertEqual([a['id'] for a in sorted_accs], ['3', '2', '1'])

    def test_select_best_account(self):
        accounts = [
            {'id': '1', 'name': 'WorkLocked', 'selected': True, 'live_state': 'live',
             'work_locked_until': self.now + 600, 'last_used_at': 10},
            {'id': '2', 'name': 'AvailableAll', 'selected': True, 'live_state': 'live',
             'last_used_at': 20},
            {'id': '3', 'name': 'RateLimited', 'selected': True, 'live_state': 'live',
             'rate_limit_until': self.now + 300, 'last_used_at': 5},
        ]

        # For 'work' mode: Acc 1 is work locked, Acc 3 is rate limited -> Acc 2 selected
        best_work, fallback_work = select_best_account(accounts, mode='work', now=self.now)
        self.assertIsNotNone(best_work)
        self.assertEqual(best_work['id'], '2')
        self.assertFalse(fallback_work)

        # For 'chat' mode: Acc 1 is work locked but available for chat, and LRU (10 < 20) -> Acc 1 selected!
        best_chat, fallback_chat = select_best_account(accounts, mode='chat', now=self.now)
        self.assertIsNotNone(best_chat)
        self.assertEqual(best_chat['id'], '1')
        self.assertFalse(fallback_chat)

        # If all accounts are work locked, request 'work' with allow_fallback=True -> fallback to chat!
        accounts_work_locked = [
            {'id': '1', 'name': 'WorkLocked1', 'selected': True, 'live_state': 'live',
             'work_locked_until': self.now + 600, 'last_used_at': 10},
        ]
        chosen, fallback = select_best_account(accounts_work_locked, mode='work', allow_fallback=True, now=self.now)
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen['id'], '1')
        self.assertTrue(fallback)

        # But with allow_fallback=False -> None
        chosen_no_fb, _ = select_best_account(accounts_work_locked, mode='work', allow_fallback=False, now=self.now)
        self.assertIsNone(chosen_no_fb)

    def test_get_earliest_recovery_time(self):
        t1 = self.now + 300
        t2 = self.now + 600
        accounts = [
            {'id': '1', 'work_locked_until': t2, 'rate_limit_until': t1, 'selected': True},
            {'id': '2', 'credit_locked_until': self.now + 1000, 'selected': True},
        ]

        # For work mode: Acc 1 must clear both rate_limit (t1) and work_lock (t2) -> recovers at t2
        earliest_work = get_earliest_recovery_time(accounts, mode='work', now=self.now)
        self.assertEqual(earliest_work, t2)

        # For chat mode: Acc 1 only needs rate_limit (t1) to clear -> recovers at t1
        earliest_chat = get_earliest_recovery_time(accounts, mode='chat', now=self.now)
        self.assertEqual(earliest_chat, t1)

    def test_account_status_badge(self):
        # Work locked badge
        acc_work = {'work_locked_until': self.now + 500}
        badge = account_status_badge(acc_work, now=self.now)
        self.assertFalse(badge['can_work'])
        self.assertTrue(badge['can_chat'])
        self.assertIn('Khóa Work (Chat OK)', badge['text'])

        # Rate limited badge
        acc_rate = {'rate_limit_until': self.now + 300}
        badge_rate = account_status_badge(acc_rate, now=self.now)
        self.assertFalse(badge_rate['can_work'])
        self.assertFalse(badge_rate['can_chat'])
        self.assertIn('Quá tải', badge_rate['text'])

        # Normal active badge
        acc_ok = {}
        badge_ok = account_status_badge(acc_ok, now=self.now)
        self.assertTrue(badge_ok['can_work'])
        self.assertTrue(badge_ok['can_chat'])
        self.assertIn('Sẵn sàng', badge_ok['text'])


if __name__ == '__main__':
    unittest.main()
