"""Smart Dola account selection, load balancing (LRU / Round-Robin), and mode fallbacks."""
import time
from account_health import is_account_available, credit_locked, work_locked, rate_limited, run_cooldown_locked


def sort_accounts_by_lru(accounts, mode='chat', now=None):
    """Filter accounts available for `mode` and sort by least-recently-used (last_used_at asc)."""
    now = time.time() if now is None else now
    available = [a for a in accounts if is_account_available(a, mode=mode, now=now)]
    return sorted(available, key=lambda a: a.get('last_used_at', 0))


def select_best_account(accounts, busy_ids=None, mode='chat', exclude_ids=None, allow_fallback=True, now=None):
    """Select the best available account for `mode`.
    Returns tuple: (account, fallback_to_chat).
    If mode=='work' and no accounts have Work quota, will fallback to Chat if allow_fallback is True."""
    now = time.time() if now is None else now
    busy = set(busy_ids or ())
    excluded = set(exclude_ids or ())

    normalized_mode = str(mode or 'chat').strip().lower()
    is_work = normalized_mode in ('work', 'pro')

    # 1. First attempt: Find account for requested mode
    target_mode = 'work' if is_work else 'chat'
    candidates = [
        a for a in sort_accounts_by_lru(accounts, mode=target_mode, now=now)
        if a.get('id') not in busy and a.get('id') not in excluded
    ]

    if candidates:
        chosen = candidates[0]
        chosen['last_used_at'] = now
        return chosen, False

    # 2. If Work was requested but all accounts are work-limited, try fallback to Chat
    if is_work and allow_fallback:
        chat_candidates = [
            a for a in sort_accounts_by_lru(accounts, mode='chat', now=now)
            if a.get('id') not in busy and a.get('id') not in excluded
        ]
        if chat_candidates:
            chosen = chat_candidates[0]
            chosen['last_used_at'] = now
            return chosen, True

    return None, False


def get_earliest_recovery_time(accounts, mode='chat', now=None):
    """Find the earliest timestamp when an account will become available for `mode`."""
    now = time.time() if now is None else now
    normalized_mode = str(mode or 'chat').strip().lower()
    is_work = normalized_mode in ('work', 'pro')

    unlock_times = []
    for account in accounts:
        if account.get('live_state') == 'die':
            continue
        # If already available right now
        if is_account_available(account, mode=normalized_mode, now=now):
            return now

        times = []
        if credit_locked(account, now):
            times.append(account.get('credit_locked_until', 0))
        if rate_limited(account, now):
            times.append(account.get('rate_limit_until', 0))
        if run_cooldown_locked(account, now):
            times.append(account.get('run_cooldown_until', 0))
        if is_work and work_locked(account, now):
            times.append(account.get('work_locked_until', 0))

        if times:
            # An account recovers when ALL its active blocks expire (the max of its blocks)
            unlock_times.append(max(times))

    return min(unlock_times) if unlock_times else None
