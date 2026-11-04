"""Browser ownership belongs to the account, never to the current UI default."""
def account_backend(account):
    backend = account.get('browser_backend')
    if backend is None:
        backend = 'hidemium' if account.get('hidemium_profile_id') else 'chrome'
    if backend not in ('chrome', 'hidemium'):
        raise ValueError('Tài khoản chưa xác định trình duyệt; cần liên kết lại profile.')
    return backend


def account_settings(settings, account, *, require_profile=True):
    backend = account_backend(account)
    if require_profile and backend == 'hidemium' and not account.get('hidemium_profile_id'):
        raise ValueError('Tài khoản Hidemium chưa có UUID profile. Liên kết profile trước khi mở/chạy.')
    return dict(settings, browser_backend=backend, use_hidemium=backend == 'hidemium',
                use_gpm=False, use_3big_browser=backend == 'chrome', seedance_browser=backend == 'chrome')


def prepare_account_settings(settings, account, cancel=None, report=None):
    if account_backend(account) == 'hidemium' and not account.get('hidemium_profile_id'):
        from hidemium_browser import create_account_profile
        create_account_profile(settings, account, cancel, report)
    return account_settings(settings, account)
