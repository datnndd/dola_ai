"""One authorized live Dola generation. A durable marker prevents resubmission."""
import json
from pathlib import Path
import sys
import threading
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dola_browser import BrowserSteps, ProfileLease
from dola_tabs import run_profile_tabs
from seedance_browser import migrate_settings
from studio_core import DATA, HOME, defaults


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    marker = HOME / 'samples' / 'dog_cat_live_test.json'
    if marker.exists():
        previous = json.loads(marker.read_text('utf-8'))
        if previous.get('submission_attempted'):
            print('A submission was already attempted. Refusing to generate again. Inspect samples/dog_cat_live_test.json.')
            return 2
    workspace = json.loads((DATA/'workspace.json').read_text('utf-8'))
    settings = {**defaults(), **workspace.get('settings', {})}
    accounts = workspace.get('accounts', [])
    migrate_settings(settings, accounts, DATA)
    account = next((a for a in accounts if a.get('selected')), None)
    if account is None:
        raise RuntimeError('Không có tài khoản được chọn để kiểm thử.')
    # Do not interfere with a Chrome/profile already owned by the running app.
    with ProfileLease(account['profile'], settings['lock_root']):
        pass
    settings.update(duration='15', model='Seedance 2.5', dola_mode='pro', seedance_flow='pro_chat',
                    tabs_per_profile=1, concurrency=1, show_browser=True, timeout=1800,
                    load_extension=True, auto_retry=False, upscale=False,
                    output=str(HOME/'downloads'/'dog_cat_test'))
    task = dict(id=uuid.uuid4().hex, name='Kiểm thử chó mèo',
                prompt=('Một chú chó Shiba và một chú mèo mướp đánh nhau kiểu hoạt hình vui nhộn trong sân vườn: '
                        'cả hai né đòn, vờn nhau và tạo một đám bụi hài hước, rồi dừng lại, nhìn nhau ngạc nhiên '
                        'và cùng ngồi xuống. Hoạt hình 3D điện ảnh, chuyển động rõ ràng, không máu me, không thương tích.'), images=[])
    state = dict(task_id=task['id'], account_id=account['id'], profile=account['profile'], prompt=task['prompt'],
                 submission_attempted=False, status='starting', started_at=time.strftime('%Y-%m-%d %H:%M:%S'))
    def save():
        temp = marker.with_suffix('.tmp')
        temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(marker)
    def report(message):
        print(time.strftime('[%H:%M:%S]'), message, flush=True)
        if message.startswith('[Hội thoại] '):
            state['conversation_url'] = message[len('[Hội thoại] '):]
            save()
    original_submit = BrowserSteps.submit
    def submit_once(self, editor, should_send=None):
        if state['submission_attempted']:
            raise RuntimeError('Kiểm thử chỉ cho phép một lần gửi; không gửi thêm xác nhận hay prompt.')
        def commit():
            if should_send is not None and not should_send():
                return False
            state['submission_attempted'] = True
            state['status'] = 'submission_attempted'
            save()  # After trial/hover; immediately before the real click/key.
            return True
        result = original_submit(self, editor, commit)
        state['status'] = 'submitted' if result else 'send_cancelled'
        save()
        return result
    BrowserSteps.submit = submit_once
    def settle(task, path, error):
        state['status'] = 'done' if error is None else 'failed'
        if error is None:
            state['video'] = str(path)
            report('Đã tạo và lưu video: '+str(path))
        else:
            state['error'] = str(error)
            report('Kiểm thử dừng: '+str(error))
        save()
    save()
    try:
        run_profile_tabs([task], account, settings, threading.Event(), lambda *args: report, settle,
                         browser_report=report)
    finally:
        BrowserSteps.submit = original_submit
    return 0 if state['status'] == 'done' else 1


if __name__ == '__main__':
    raise SystemExit(main())
