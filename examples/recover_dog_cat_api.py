"""Read/download the already-created test conversation. Sending is forbidden."""
import json
from pathlib import Path
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dola_browser import BrowserSteps
from dola_tabs import run_profile_tabs
from seedance_browser import migrate_settings
from studio_core import DATA, HOME, defaults


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    previous = json.loads((HOME/'samples'/'dog_cat_live_test.json').read_text('utf-8'))
    workspace = json.loads((DATA/'workspace.json').read_text('utf-8'))
    settings = {**defaults(), **workspace.get('settings', {})}
    accounts = workspace.get('accounts', [])
    migrate_settings(settings, accounts, DATA)
    account = next(a for a in accounts if a['id'] == previous['account_id'])
    settings.update(timeout=300, tabs_per_profile=1, show_browser=True, upscale=False,
                    download_pipeline='seedance_api', video_source='api_preview',
                    output=str(HOME/'downloads'/'dog_cat_api_test'))
    task = dict(id='dog-cat-api-recovery', name='Tải lại chó mèo qua API', run_action='resume',
                conversation_url=previous['conversation_url'], conversation_account=account['id'])
    def never_send(*args, **kwargs):
        raise AssertionError('Read-only recovery must never submit a prompt')
    BrowserSteps.submit = never_send
    result = {}
    def report(message):print(message, flush=True)
    def settle(task, path, error):
        result.update(video=str(path) if path else None, error=str(error) if error else None,
                      conversation_url=task['conversation_url'], new_submissions=0)
        report('Recovery result: '+json.dumps(result, ensure_ascii=False))
    run_profile_tabs([task], account, settings, threading.Event(), lambda *args:report, settle, browser_report=report)
    (HOME/'samples'/'dog_cat_api_recovery.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if result.get('video') and not result.get('error') else 1


if __name__ == '__main__':raise SystemExit(main())
