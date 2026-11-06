"""Opt-in video jobs from the web (API v1: claim / heartbeat / report),
bounded by free Chrome/tab slots, with durable reports.

Success reports done. Render errors retain the same lease and task locally;
the retry checkbox controls local retries. Stop releases unfinished leases.
Heartbeat keeps waiting/retrying jobs owned by this worker."""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import uuid

from account_health import is_credit_error, credit_until, credit_unlock_time, unlock_text, CREDIT_ERROR
from cookie_accounts import protect
from engine import atomic_json, Cancelled
from task_runtime import StartupTimeout, task_failure_status, task_timeout_seconds

API_BASE = 'https://view.botlc.pro'
# Cloudflare blocks the default "Python-urllib" signature (HTTP 403 / 1010).
USER_AGENT = 'StudioPython/1.0'
# The job no longer belongs to this machine (cancelled / reassigned / done elsewhere): drop, never resend.
JOB_GONE = frozenset({'LEASE_MISMATCH', 'LEASE_EXPIRED', 'LEASE_LOST', 'JOB_CANCELLED',
                      'JOB_ALREADY_DONE', 'JOB_NOT_FOUND', 'JOB_RESET'})
HEARTBEAT_SECONDS = 120
REPORT_BATCH = 50


def machine_code():
    """Mã máy cố định của PC này (băm MachineGuid của Windows). Web chỉ cho API key chạy trên máy
    admin đã duyệt (Quản trị → Máy chờ gắn API key). Cùng công thức với ContentStudio."""
    raw = ''
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Cryptography', 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            raw = str(winreg.QueryValueEx(key, 'MachineGuid')[0])
    except (ImportError, OSError):
        raw = ''
    if not raw.strip():
        raw = f'{uuid.getnode():012x}'
    digest = hashlib.sha256(('studiosheet-machine:' + raw.strip().lower()).encode('utf-8')).hexdigest().upper()
    return '-'.join(digest[i:i + 4] for i in range(0, 16, 4))


MACHINE_ID = machine_code()


def validate_machine(value):
    value = str(value or '').strip()
    if not value or len(value) > 80 or '|' in value or any(ord(c) < 32 for c in value):
        raise ValueError('Nhập mã máy (tối đa 80 ký tự, không chứa | hoặc xuống dòng).')
    return value


def stt_folder(stt):
    """The job's stt as a folder/file name; raises when the stt is not a plain number."""
    if isinstance(stt, bool):
        raise ValueError('stt không hợp lệ.')
    value = str(stt if stt is not None else '').strip()
    if not re.fullmatch(r'\d{1,9}', value):
        raise ValueError('stt không hợp lệ để đặt tên thư mục.')
    return str(int(value))


def output_for_job(folder, stt, task_code=None):
    """Keep each video in its STT folder with a distinct safe filename."""
    sub = stt_folder(stt)
    name = str(task_code or sub)
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,150}', name):
        raise ValueError('task_code không hợp lệ để đặt tên file.')
    return Path(folder).resolve() / sub / (name + '.mp4')


def video_name(task):
    """Tên file video Auto: số thứ tự video của STT (1, 2, 3…); job cũ không có số thì dùng mã job."""
    index = task.get('video_index')
    name = str(index) if isinstance(index, int) and not isinstance(index, bool) and 0 < index < 1000 else task.get('task_code')
    if task.get('_logo_output') and name and not str(name).endswith('_logo'):
        name = str(name) + '_logo'
    return name


def task_output_path(output, task):
    """<thư mục video>/[<tài khoản>/]<STT>/<số thứ tự>.mp4 — tài khoản chỉ có với key 'Mọi tài khoản'."""
    base = Path(output)
    if task.get('owner_folder'):
        base = base / task['owner_folder']
    return output_for_job(base, task['stt'], video_name(task))


def owner_folder(folder_name):
    """'cuong/631' (key Mọi tài khoản) → 'cuong', để STT trùng giữa các tài khoản không đè video."""
    match = re.fullmatch(r'([A-Za-z0-9_-]{1,64})/\d{1,9}', str(folder_name or ''))
    return match.group(1) if match else ''


class JobsError(RuntimeError):
    def __init__(self, message, status=0, code=''):
        super().__init__(message)
        self.status, self.code = status, code

    @property
    def temporary(self):
        """Network / rate limit / server busy / máy chờ admin duyệt: keep the report and try again later."""
        return self.status in (0, 408, 429) or self.status >= 500 or str(self.code).startswith('MACHINE_')


class ToolStopped(RuntimeError):
    """The Chrome/profile runner crashed before the job could finish (not the job's fault)."""


class DeadlineEvent(threading.Event):
    """Apply the job lease deadline to browser waits and FFmpeg alike."""
    def __init__(self, deadline):
        super().__init__()
        self.deadline = deadline

    def is_set(self):
        return super().is_set() or time.monotonic() >= self.deadline

    def wait(self, timeout=None):
        remaining = max(0, self.deadline - time.monotonic())
        super().wait(remaining if timeout is None else min(timeout, remaining))
        return self.is_set()


class JobsClient:
    """Web API v1 with the X-API-Key header (the key never goes into a URL)."""
    def __init__(self, key, machine_name=''):
        self.key = str(key or '').strip()
        self.machine_name = machine_name
        if not self.key:
            raise ValueError('Nhập API key của web (ss_…) trong Cài đặt trước khi bật Auto.')

    def request(self, path, payload=None):
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8') if payload is not None else None
        # Mã máy: key chỉ chạy trên máy admin đã duyệt. Tên máy (ô "Mã máy" trong Cài đặt) để admin dễ nhận; header phải ASCII.
        name = re.sub(r'[^\x20-\x7e]', '', str(self.machine_name or '')).strip()[:80] or os.environ.get('COMPUTERNAME', '')
        headers = {'Accept': 'application/json', 'X-API-Key': self.key, 'User-Agent': USER_AGENT,
                   'X-Machine-Id': MACHINE_ID, 'X-Machine-Name': name}
        if data is not None:
            headers['Content-Type'] = 'application/json'
        request = Request(API_BASE + path, data=data, headers=headers, method='POST' if data is not None else 'GET')
        try:
            with urlopen(request, timeout=20) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
                if len(raw) > 4 * 1024 * 1024:
                    raise JobsError('Web trả dữ liệu quá lớn.', 502)
                result = json.loads(raw.decode('utf-8'))
        except HTTPError as exc:
            code = message = ''
            try:
                body = json.loads(exc.read(65536).decode('utf-8'))
                code, message = str(body.get('error') or ''), str(body.get('message') or '')
            except Exception:
                pass
            hint = ' Kiểm tra API key trong Cài đặt.' if exc.code == 401 else ''
            text = f"Web trả HTTP {exc.code}{' ' + code if code else ''}: {message or 'từ chối yêu cầu'}.{hint}"
            raise JobsError(text[:300], exc.code, code) from None
        except (URLError, TimeoutError, OSError):
            raise JobsError('Không kết nối được web.') from None
        except (ValueError, UnicodeError):
            raise JobsError('Web không trả JSON hợp lệ.') from None
        if not isinstance(result, dict) or result.get('ok') is False:
            raise JobsError('Web không chấp nhận yêu cầu.', 400)
        return result

    def claim(self, limit=1, worker=''):
        """Claim up to `limit` video jobs (videos of one STT come together to this machine)."""
        body = {'type': 'video', 'limit': max(1, min(10, int(limit or 1))), 'same_row': True,
                'worker': str(worker or 'StudioPython')[:80]}
        jobs = self.request('/api/v1/jobs/claim', body).get('jobs')
        if not isinstance(jobs, list) or not all(isinstance(j, dict) for j in jobs):
            raise JobsError('Phản hồi nhận job thiếu danh sách jobs.', 502)
        return jobs

    def me(self):
        """GET /api/v1/me để kiểm tra key/máy (tài khoản, số dư). Lỗi -> JobsError kèm code."""
        return self.request('/api/v1/me')

    def heartbeat(self, job_id, lease):
        return self.request(f'/api/v1/jobs/{job_id}/heartbeat', {'lease': lease})

    def report(self, items):
        """Batch report; returns one result per item (ok / error code)."""
        results = self.request('/api/v1/jobs/report', {'items': list(items)}).get('results')
        if not isinstance(results, list) or len(results) != len(items):
            raise JobsError('Phản hồi báo kết quả không khớp số mục đã gửi.', 502)
        return results

    def legacy_report(self, item):
        """Old /api/jobs report for journal rows claimed before the API v1 switch (no lease)."""
        return self.request('/api/jobs', dict(item, action='render_report', render_key=self.key))


class ReportJournal:
    """Durable per-job results; failed report requests stay pending.
    status: 'done' | 'error' (report failed, retry=false) | 'release' (back to the web queue)."""
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.rows = json.loads(self.path.read_text('utf-8')) if self.path.exists() else {}
        if not isinstance(self.rows, dict):
            raise ValueError('Nhật ký Auto bị lỗi; chưa nhận job mới.')

    def save(self):
        with self.lock:
            atomic_json(self.path, self.rows)

    def claim(self, task, protected_key, machine):
        """Distinct videos with the same STT may run concurrently."""
        with self.lock:
            code = task['task_code']
            if any(r.get('task_code') == code and r['state'] in ('running', 'pending')
                   for r in self.rows.values()):
                return False
            self.rows[task['id']] = dict(stt=task['stt'], task_code=code, script_id=task.get('script_id', ''),
                lease=task.get('lease', ''), claimed_at=time.time(), state='running',
                render_key_protected=protected_key, machine=machine)
            self.save()
            return True

    def finish(self, tid, status, folder='', note='', path=''):
        with self.lock:
            row = self.rows[tid]
            row.update(state='pending', status=status, folder=folder, note=note, path=path)
            self.save()

    def recover(self):
        with self.lock:
            for row in self.rows.values():
                if row['state'] == 'running':
                    # Closed mid-run is not the job's fault: hand it back to the web queue.
                    row.update(state='pending', status='release' if row.get('lease') else 'error',
                               folder='', note='Auto bị đóng khi đang chạy.')
            self.save()

    def running(self):
        with self.lock:
            return [(tid, row['task_code'], row['lease']) for tid, row in self.rows.items()
                    if row['state'] == 'running' and row.get('lease')]

    def pending(self):
        with self.lock:
            return [(tid, copy.deepcopy(row)) for tid, row in self.rows.items() if row['state'] == 'pending']

    def acknowledge(self, tid, expired=False):
        with self.lock:
            self.rows[tid]['state'] = 'expired' if expired else 'reported'
            # Keep recent acknowledgements for diagnosis without growing forever.
            completed = [key for key, row in self.rows.items() if row['state'] in ('reported', 'expired')]
            for key in completed[:-500]:
                self.rows.pop(key)
            self.save()


class AutoJobs:
    def __init__(self, settings, accounts, data_dir, events, cancel, task_cancels,
                 client_factory=JobsClient, profile_runner=None, poll_seconds=5):
        self.settings = copy.deepcopy(settings)
        self.machine = str(settings.get('auto_machine_code') or '').strip()
        self.protected_key = settings.get('auto_render_key_protected', '')
        key = protect(self.protected_key, decrypt=True) if self.protected_key else ''
        self.client = client_factory(key)
        if isinstance(self.client, JobsClient):
            self.client.machine_name = self.machine
        self.client_factory = client_factory
        count = min(len(accounts), max(1, int(settings.get('concurrency', 2))))
        nkeys = None
        # Mỗi luồng dùng MỘT key proxy riêng → số luồng không vượt quá số key của nguồn đang chọn
        # (ProxyNo1 hoặc ProxyVN; không áp cho proxy nhập tay).
        if (settings.get('use_gpm') or settings.get('use_hidemium') or settings.get('seedance_browser')) and settings.get('proxy_source') != 'manual':
            try:
                import proxy_pool
                nkeys = len(proxy_pool.source_keys(settings))
                if nkeys:
                    proxy_pool.pool().reset()  # lần chạy mới: xoá lease cũ
                    count = min(count, nkeys)
            except Exception:
                nkeys = None
        if not count:
            raise ValueError('Chọn ít nhất một tài khoản rảnh cho Auto.')
        # Số Chrome CHẠY đồng thời = count. Nhưng tạo thêm SLOT DỰ PHÒNG (tài khoản rảnh, tối đa
        # bằng số key) để khi một Chrome lỗi + đang đóng, job lỗi được chạy lại NGAY trên tài khoản
        # + proxy còn trống, không phải chờ Chrome lỗi đóng xong.
        self.max_active = max(1, count)
        slot_limit = len(accounts) if nkeys is None else min(len(accounts), max(count, nkeys))
        slot_limit = max(self.max_active, min(slot_limit, len(accounts)))
        # All eligible accounts, top-down. Slots start on the first ones and move down
        # the list (wrapping to the top) when an account runs out of credits or fails.
        self.accounts = copy.deepcopy(list(accounts))
        self.excluded = {}  # account id -> time it may be used again (midnight for credits)
        self.waiting_logged = False
        self.slots = [dict(account=copy.deepcopy(a), inbox=queue.Queue(), busy=0, blocked=False)
                      for a in accounts[:slot_limit]]
        self.one_job_per_account = bool(settings.get('one_job_per_account', True))
        self.capacity = 1 if self.one_job_per_account else max(1, min(5, int(settings.get('tabs_per_profile', 3))))
        self.used_accounts = set()
        self.data_dir, self.events, self.cancel = Path(data_dir), events, cancel
        self.task_cancels = task_cancels
        self.stop_receiving = threading.Event()
        self.workers_exit = threading.Event()
        self.lock = threading.RLock()
        self.profile_runner = profile_runner
        self.poll_seconds = poll_seconds
        self.heartbeat_seconds = HEARTBEAT_SECONDS
        self.total = self.done = self.ok = 0
        self.completed = set()
        self.claimed = deque()  # jobs fetched from one GET, not yet handed to a slot
        self.held = {}  # Failed web jobs keep their lease and UI identity until retry/stop.
        self.lost_leases = set()
        self.journal = None

    def set_auto_retry(self, enabled):
        with self.lock:
            self.settings['auto_retry'] = bool(enabled)

    def explicitly_cancelled(self, tid):
        event = self.task_cancels.get(tid)
        # A per-attempt timeout is retryable; a user/lease cancellation is not.
        return event is not None and threading.Event.is_set(event)

    def hold_failure(self, slot, task, error):
        """Do not fail/release a lease that we intend to run again locally."""
        from dola_browser import SessionDead
        if task.get('_dola_submission_attempted'):
            # Submission may have reached Dola. Report the failed retrieval;
            # recovery uses the saved conversation, never a fresh generation.
            return False
        if isinstance(error, StartupTimeout):
            # Never reached Dola: hand the job straight back to the web queue so
            # another thread/account can run it, instead of holding it here.
            return False
        if (not task.get('lease') or task.get('sheet_job') or self.cancel.is_set()
                or self.stop_receiving.is_set() or self.explicitly_cancelled(task['id'])
                or task['id'] in self.lost_leases or task.get('_invalid_job')):
            return False
        again = dict(task, last_account_id=slot['account']['id'],
                     _retry_round=int(task.get('_retry_round', 0)) + 1,
                     _session_retry=isinstance(error, SessionDead))
        with self.journal.lock:
            self.journal.rows[task['id']].update(note=str(error)[:1500], waiting_retry=True)
            self.journal.save()
        self.held[task['id']] = again
        slot['busy'] -= 1
        slot['blocked'] = True  # Stop intake; close Chrome after its other active tabs finish.
        if isinstance(error, SessionDead):
            self.excluded[slot['account']['id']] = float('inf')
        self.emit('status', task['id'], task_failure_status(error) + ' • giữ job, chờ chạy lại')
        self.log(f"{task['name']}: giữ nguyên job; đóng Chrome lỗi khi các tab đang chạy kết thúc.")
        return True

    def release_held(self):
        with self.lock:
            for tid, task in list(self.held.items()):
                if (self.stop_receiving.is_set() or self.cancel.is_set()
                        or self.explicitly_cancelled(tid) or tid in self.lost_leases):
                    self.held.pop(tid)
                    self.abandon(task, 'Đã dừng; trả job chưa hoàn thành về web.')

    def emit(self, *event):
        self.events.put(event)

    def log(self, message):
        self.emit('log', 'Auto: ' + message)

    def pause(self):
        self.stop_receiving.set()

    def reserve(self):
        with self.lock:
            active = [s for s in self.slots if not s['blocked'] and not s.get('draining')
                      and (s.get('runner_active') or s['busy'] > 0)]
            # 1) Còn dưới số luồng (không tính Chrome đang ĐÓNG vì lỗi) → mở Chrome mới (slot rảnh),
            #    trải đều tài khoản; cũng là cách để job lỗi chạy lại ngay trên slot DỰ PHÒNG.
            if len(active) < self.max_active:
                idle = next((s for s in self.slots if not s['blocked'] and not s.get('draining')
                             and not s.get('runner_active') and s['busy'] == 0 and s['inbox'].empty()), None)
                if idle is not None:
                    idle['busy'] += 1
                    return idle
            # 2) Đã đủ số Chrome → thêm tab vào Chrome đang chạy còn chỗ (ít tab nhất trước).
            with_cap = [s for s in active if s['busy'] < self.capacity]
            if with_cap:
                slot = min(with_cap, key=lambda s: s['busy'])
                slot['busy'] += 1
                return slot
            return None

    def release(self, slot):
        with self.lock:
            slot['busy'] -= 1

    def free_slots(self):
        with self.lock:
            active = [s for s in self.slots if not s['blocked'] and not s.get('draining')
                      and (s.get('runner_active') or s['busy'] > 0)]
            free_in_active = sum(self.capacity - s['busy'] for s in active if s['busy'] < self.capacity)
            idle = sum(1 for s in self.slots if not s['blocked'] and not s.get('draining')
                       and not s.get('runner_active') and s['busy'] == 0 and s['inbox'].empty())
            can_start = min(max(0, self.max_active - len(active)), idle)
            return free_in_active + can_start * self.capacity

    def next_account(self, slot):
        """Next usable account below this slot's current one, wrapping to the top."""
        with self.lock:
            ids = [a['id'] for a in self.accounts]
            others = {s['account']['id'] for s in self.slots if s is not slot}
            start = ids.index(slot['account']['id']) + 1 if slot['account']['id'] in ids else 0
            now = time.time()
            for step in range(len(ids)):
                account = self.accounts[(start + step) % len(ids)]
                if account['id'] not in others and self.excluded.get(account['id'], 0) <= now:
                    return account
            return None

    def rotate(self, slot):
        with self.lock:
            account = self.next_account(slot)
            if account is None:
                return False
            old = slot['account']['name']
            slot['account'] = copy.deepcopy(account)
            slot['blocked'] = False
        self.log(f"Chrome {old} dừng; chuyển sang tài khoản kế tiếp {account['name']}.")
        return True

    def retire_account_after_job(self, slot):
        """One Auto job consumes this account for the rest of the current run."""
        if not self.one_job_per_account:
            return False
        with self.lock:
            account_id = slot['account']['id']
            first = account_id not in self.used_accounts
            self.used_accounts.add(account_id)
            self.excluded[account_id] = float('inf')
            slot['blocked'] = True
        if first:
            self.log(f"{slot['account']['name']}: đã chạy 1 job; đóng Chrome và không dùng lại trong lượt Auto này.")
        return first

    def lock_credits(self, slot, until=None):
        """Hết điểm / hết lượt: khóa tài khoản tới giờ Dola báo (không có thì 00:00 hôm sau)."""
        account = slot['account']
        with self.lock:
            now = time.time()
            current = self.excluded.get(account['id'], 0)
            first = current <= now
            target = credit_unlock_time(until, now, current if now < current < float('inf') else None)
            self.excluded[account['id']] = target
            slot['blocked'] = True
        if first or until:
            self.emit('credit_locked', account['id'], until)
            reason = 'hết lượt dùng (Dola báo giờ mở lại)' if until else 'hết điểm'
            self.log(f"{account['name']} {reason} • khóa đến {unlock_text(target)}, chuyển tài khoản kế tiếp.")

    def waiting_for_midnight(self):
        """Some account only waits for its credit lock to end: keep Auto on instead of stopping."""
        with self.lock:
            now = time.time()
            return any(now < until < float('inf') for until in self.excluded.values())

    def output_path(self, task):
        return task_output_path(self.settings['output'], task)

    def report_for(self, task, account):
        from task_runtime import task_settings
        from proxyvn import resolve_proxy_server
        with self.lock:
            if isinstance(self.journal, ReportJournal):
                with self.journal.lock:
                    self.journal.rows[task['id']]['account_id'] = account['id']
                    self.journal.save()
            elif task.get('sheet_job'):
                with self.journal.lock:
                    self.journal.data['tasks'][task['id']]['last_account_id'] = account['id']
                    self.journal.save()
        cfg = task_settings(self.settings, task)
        from account_health import work_locked
        if work_locked(account) and str(cfg.get('dola_mode', 'pro')).strip().lower() == 'pro':
            cfg['dola_mode'] = 'fast'
            if 'options' not in task or not isinstance(task.get('options'), dict):
                task['options'] = {}
            task['options']['dola_mode'] = 'fast'
        proxy_server = resolve_proxy_server(self.settings, account)
        self.emit('task_execution', task['id'], dict(model=cfg['model'],duration=cfg['duration'],
            account_id=account['id'],account_name=account['name'],proxy_server=proxy_server))
        def report(message):
            if message.startswith('[Khóa Work]'):
                val = message[len('[Khóa Work]'):].strip()
                try: until = float(val) if val else None
                except ValueError: until = None
                from account_health import lock_for_work
                reopen = lock_for_work(account, until=until)
                with self.lock:
                    for s in self.slots:
                        if s['account']['id'] == account['id']:
                            lock_for_work(s['account'], until=reopen)
                self.emit('work_locked', account['id'], reopen)
            elif message.startswith('[Tài khoản] '):
                self.emit('identity', account['id'], message[len('[Tài khoản] '):])
            elif message.startswith('[Phiên hợp lệ]'):
                self.emit('session_verified', account['id'], message[len('[Phiên hợp lệ]'):].strip())
            elif message.startswith('[Hội thoại] '):
                self.emit('conversation', task['id'], message[len('[Hội thoại] '):], account['id'])
            elif message.startswith(('ProxyNo1', 'ProxyVN', 'Proxy tay', 'Proxy sẵn', 'Chrome Seedance')):
                self.log(f"Chrome {account['name']}: {message}")  # proxy/đổi IP: nhật ký, không đè trạng thái task
                ps = resolve_proxy_server(self.settings, account)
                self.emit('task_execution', task['id'], dict(model=cfg['model'], duration=cfg['duration'],
                    account_id=account['id'], account_name=account['name'], proxy_server=ps))
            else:
                self.emit('status', task['id'], message)
        return report

    def abandon(self, task, note):
        """Release a claimed-but-unstarted job back to the web queue (not an attempt)."""
        with self.lock:
            if task['id'] in self.completed:
                return
            self.completed.add(task['id'])
        try:
            if task['id'] in self.lost_leases:
                self.journal.acknowledge(task['id'], expired=True)
                self.emit('auto_report', task['id'], 'rejected')
            else:
                self.journal.finish(task['id'], 'release', note=note)
        except Exception:
            pass
        self.emit('status', task['id'], 'Trả lại web • ' + note)
        self.emit('task_done', task['id'])
        with self.lock:
            self.done += 1
            self.emit('progress', self.done, self.total)

    def settle(self, slot, task, path=None, error=None):
        with self.lock:
            # Retire this browser on either outcome. Already active tabs may finish,
            # but no replacement task can enter until context.close() has returned.
            if slot.get('runner_active'):
                slot['draining'] = True
            self._settle_locked(slot,task,path,error)
            self.retire_account_after_job(slot)

    def _settle_locked(self, slot, task, path, error):
        from dola_browser import ProfileStopped, SessionDead
        if task['id'] in self.completed or task['id'] in self.held:
            return
        if isinstance(error, SessionDead):
            self.emit('account_die', slot['account']['id'], str(error))
        if error is not None and is_credit_error(error):
            self.lock_credits(slot, credit_until(error))
            if (task.get('sheet_job') and task['id'] not in self.completed and not self.cancel.is_set()
                    and not self.task_cancels.get(task['id'], threading.Event()).is_set()
                    and self.next_account(slot) is not None):
                # Dola refused before generating: run the same job on the next account.
                self.emit('status', task['id'], f"Chờ chạy • {slot['account']['name']} hết điểm, chuyển tài khoản kế tiếp")
                slot['inbox'].put(dict(task, _attempt=uuid.uuid4().hex))
                return
        if error is not None and self.hold_failure(slot, task, error):
            return
        with self.lock:
            if task['id'] in self.completed:
                return
            self.completed.add(task['id'])
        final = None
        try:
            if error is None:
                source = Path(path).resolve()
                if not source.is_file() or source.stat().st_size == 0:
                    raise RuntimeError('Chưa có file video đầu ra.')
                final = self.output_path(task)
                final.parent.mkdir(parents=True, exist_ok=True)
                if source != final:
                    if final.exists():
                        archive = final.parent / '_previous'
                        archive.mkdir(exist_ok=True)
                        final.replace(archive / (final.stem + '_' + uuid.uuid4().hex[:8] + '.mp4'))
                    source.replace(final)
                if task.get('sheet_job'):
                    self.journal.finish(task['id'], 'done', folder=final.parent.as_posix())
                else:
                    self.journal.finish(task['id'], 'done', folder=final.parent.as_posix(), path=final.as_posix())
            else:
                if task.get('sheet_job'):
                    self.journal.finish(task['id'], 'error', note=str(error)[:300])
                else:
                    if task['id'] in self.lost_leases:
                        self.journal.acknowledge(task['id'], expired=True)
                        self.emit('auto_report', task['id'], 'rejected')
                    else:
                        self.journal.finish(task['id'], self.web_failure(error), note=task_failure_status(error)[:1500])
        except Exception as exc:
            error = exc
            self.completed.discard(task['id'])
            if self.hold_failure(slot, task, error):
                return
            self.completed.add(task['id'])
            try:
                self.journal.finish(task['id'], 'error', note=str(exc)[:300])
            except Exception:
                self.pause()
                self.log('Không ghi được nhật ký báo kết quả; dừng nhận job mới, giữ video trên máy.')
        if isinstance(error, ProfileStopped) and not slot['blocked']:
            self.block_slot(slot)
        if error is None:
            # Ran a video successfully: rest this account (untick Dùng chạy, nghỉ 1 tuần)
            # and stop giving it new jobs for the rest of this Auto run.
            with self.lock:
                self.excluded[slot['account']['id']] = float('inf')
                slot['blocked'] = True
            self.emit('run_success', slot['account']['id'])
            self.emit('task_result', task['id'], str(final))
            self.emit('status', task['id'], '✓ Hoàn thành • chờ báo API')
        else:
            self.emit('status', task['id'], task_failure_status(error))
        self.emit('task_done', task['id'])
        with self.lock:
            self.done += 1
            self.ok += error is None
            self.emit('progress', self.done, self.total)
            slot['busy'] -= 1

    def worker(self, slot, video_pool):
        from dola_browser import finish_video, ProfileStopped
        from dola_tabs import run_profile_tabs
        runner = self.profile_runner or run_profile_tabs
        def submit_video(*args):
            return video_pool.submit(finish_video, *args)
        while not self.workers_exit.is_set() or not slot['inbox'].empty():
            try:
                task = slot['inbox'].get(timeout=.2)
            except queue.Empty:
                continue
            if slot['blocked'] and not self.cancel.is_set():
                self.rotate(slot)
            if self.cancel.is_set() or slot['blocked']:
                locked = self.excluded.get(slot['account']['id'], 0) < float('inf')
                self.settle(slot, task, error=Cancelled('Đã dừng') if self.cancel.is_set()
                            else RuntimeError(CREDIT_ERROR) if locked else ProfileStopped('Profile chưa sẵn sàng.'))
                continue
            account = slot['account']
            slot['runner_active'] = True
            def current():
                return not slot['blocked'] and not slot.get('draining') and slot['account']['id'] == account['id']
            def keep_open():
                return False  # Auto never keeps an empty Chrome waiting for another job.
            def on_browser_report(msg):
                self.log(f"Chrome {account['name']}: {msg}")
                if msg.startswith('[Khóa Work]'):
                    val = msg[len('[Khóa Work]'):].strip()
                    try: until = float(val) if val else None
                    except ValueError: until = None
                    from account_health import lock_for_work
                    reopen = lock_for_work(account, until=until)
                    with self.lock:
                        for s in self.slots:
                            if s['account']['id'] == account['id']:
                                lock_for_work(s['account'], until=reopen)
                    self.emit('work_locked', account['id'], reopen)
                elif msg.startswith(('ProxyNo1', 'ProxyVN', 'Proxy tay', 'Proxy sẵn', 'Chrome Seedance')):
                    from proxyvn import resolve_proxy_server
                    from task_runtime import task_settings
                    ps = resolve_proxy_server(self.settings, account)
                    t_cfg = task_settings(self.settings, task)
                    self.emit('task_execution', task['id'], dict(model=t_cfg['model'], duration=t_cfg['duration'],
                        account_id=account['id'], account_name=account['name'], proxy_server=ps))

            try:
                runner([task],account,self.settings,self.cancel,self.report_for,
                    lambda task,path,error:self.settle(slot,task,path,error),
                    video_submit=submit_video,task_cancels=self.task_cancels,intake=slot['inbox'],
                    browser_report=on_browser_report,
                    profile_error=lambda exc:self.block_slot(slot),
                    accepting=current, keep_open=keep_open)
            except Exception as exc:
                self.block_slot(slot)
                self.settle(slot,task,error=ToolStopped(str(exc) or 'Chrome dừng bất thường.'))
            finally:
                with self.lock:
                    slot['runner_active'] = False
                    slot['draining'] = False
                # Chrome của tài khoản đã đóng → nhả key proxy để tài khoản/luồng khác dùng lại.
                try:
                    import proxy_pool
                    proxy_pool.pool().release(account['id'])
                except Exception:
                    pass
                self.log(f"Chrome {account['name']}: đã kết thúc phiên; luồng sẵn sàng mở Chrome cho task tiếp theo.")

    def web_failure(self, error):
        """'release' when the tool (not the job) stopped it, else 'error' for the web to show."""
        from dola_browser import ProfileStopped
        if (self.cancel.is_set() or isinstance(error, (Cancelled, ProfileStopped, ToolStopped, StartupTimeout))
                or is_credit_error(error)):
            return 'release'
        return 'error'

    def block_slot(self, slot):
        with self.lock:
            slot['blocked'] = True
            # Failed Chrome: skip this account for the rest of this Auto run.
            self.excluded.setdefault(slot['account']['id'], float('inf'))
        self.log(f"Chrome {slot['account']['name']} chưa sẵn sàng; ngừng cấp job cho Chrome này.")

    @staticmethod
    def report_item(row):
        item = {'job_id': row['task_code'], 'lease': row['lease']}
        if row['status'] == 'done':
            item.update(status='done', folder=row.get('folder', ''), path=row.get('path', ''))
        elif row['status'] == 'release':
            item.update(status='release', note=(row.get('note') or '')[:500])
        else:
            item.update(status='failed', error=row.get('note') or 'Tool báo lỗi (không có mô tả).', retry=False)
        return item

    def flush_reports(self):
        with self.lock:
            pending=self.journal.pending()
        batches = {}
        for tid, row in pending:
            if row.get('lease'):
                batches.setdefault(row['render_key_protected'], []).append((tid, row))
            else:
                self.flush_legacy(tid, row)
        for protected_key, rows in batches.items():
            client = self.client_factory(protect(protected_key, decrypt=True))
            for start in range(0, len(rows), REPORT_BATCH):
                chunk = rows[start:start + REPORT_BATCH]
                items = [self.report_item(row) for _, row in chunk]
                results = client.report(items)
                for (tid, row), item, result in zip(chunk, items, results):
                    self.acknowledge_result(tid, row, item, result if isinstance(result, dict) else {})

    def acknowledge_result(self, tid, row, item, result):
        label = {'done': 'done', 'release': 'release'}.get(item['status'], 'fail')
        if result.get('ok'):
            self.journal.acknowledge(tid)
            self.emit('auto_report', tid, label)
            word = {'done': 'hoàn thành', 'release': 'trả lại hàng đợi', 'fail': 'lỗi'}[label]
            self.log(f"STT {row['stt']} • {row['task_code']}: đã báo {word} lên web.")
            return
        if label == 'done' and (result.get('error') in ('LEASE_EXPIRED', 'LEASE_MISMATCH', 'LEASE_LOST') or not result.get('ok')):
            try:
                legacy_res = self.client.legacy_report(item)
                if legacy_res and legacy_res.get('ok'):
                    self.journal.acknowledge(tid)
                    self.emit('auto_report', tid, 'done')
                    self.log(f"STT {row['stt']} • {row['task_code']}: đã báo hoàn thành lên web (đồng bộ trực tiếp).")
                    return
            except Exception:
                pass
        # Rejected for good (cancelled / reassigned / invalid data): never resend.
        self.journal.acknowledge(tid, expired=True)
        self.emit('auto_report', tid, 'rejected')
        kept = f" Video vẫn giữ tại {row.get('path') or row.get('folder')}." if label == 'done' else ''
        self.log(f"STT {row['stt']} • {row['task_code']}: web không nhận kết quả "
                 f"({result.get('error') or '?'}: {result.get('message') or ''}).{kept}")

    def flush_legacy(self, tid, row):
        if time.time() - row['claimed_at'] >= 1800:
            self.journal.acknowledge(tid, expired=True)
            self.emit('auto_report', tid, 'rejected')
            self.log(f"STT {row['stt']}: đã quá 30 phút; không gửi kết quả cũ đè lên lượt mới.")
            return
        client = self.client_factory(protect(row['render_key_protected'], decrypt=True))
        item = {'task_code': row['task_code'], 'status': 'done' if row['status'] == 'done' else 'fail',
                'path': row.get('path', ''), 'note': row.get('note', '')}
        if row.get('folder'):
            item['folder'] = row['folder']
        client.legacy_report(item)
        self.journal.acknowledge(tid)
        self.emit('auto_report', tid, item['status'])
        self.log(f"{row['task_code']}: đã báo {item['status']} lên web.")

    def heartbeat_loop(self, stop):
        """Keep leases alive; when the web cancels/reassigns a job, stop that task here."""
        while not stop.wait(self.heartbeat_seconds):
            for tid, job_id, lease in self.journal.running():
                if stop.is_set():
                    return
                try:
                    self.client.heartbeat(job_id, lease)
                except JobsError as exc:
                    if exc.code in JOB_GONE:
                        with self.lock:
                            self.lost_leases.add(tid)
                        cancel = self.task_cancels.get(tid)
                        if cancel is not None and not cancel.is_set():
                            cancel.set()
                            self.log(f'{job_id}: web đã hủy hoặc giao job cho lượt khác; dừng task này.')
                except Exception:
                    pass

    def run(self):
        from dola_browser import ProfileLease
        beat_stop = threading.Event()
        try:
            with ProfileLease(str(self.data_dir / 'auto_job_owner'), str(self.data_dir / 'locks')):
                self.journal = ReportJournal(self.data_dir / 'auto_jobs_v2.json')
                self.journal.recover()
                threading.Thread(target=self.heartbeat_loop, args=(beat_stop,), daemon=True).start()
                self._loop()
        except Exception as exc:
            self.log('Đã dừng Auto: ' + str(exc)[:300])
        finally:
            beat_stop.set()
            self.emit('auto_finished', self.ok, self.total)

    def build_task(self, job):
        """Convert one claimed job; missing content is journaled then reported as failed."""
        try:
            stt = stt_folder(job.get('stt'))
        except ValueError:
            stt = job.get('stt')  # Journal it first, then report fail on validation.
        script_id = str(job.get('script_code') or '')
        code = str(job.get('job_id') or '')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,150}', code):
            raise JobsError('Job thiếu job_id hợp lệ; đã dừng nhận thêm.', 400)
        prompt = job.get('prompt_video', '')
        index, count = job.get('index'), job.get('count')
        part = f' • video {index}/{count}' if index and count else ''
        video_index = index if isinstance(index, int) and not isinstance(index, bool) and index > 0 else None
        options = {}
        policy = str(self.settings.get('auto_duration_policy', 'web'))
        requested = policy if policy in ('15', '30') else job.get('duration')
        seconds = re.match(r'\s*(15|30)', str(requested or ''))
        if seconds:
            # Same mapping as Auto Sheet: 15s rows never use the 30s skill mode.
            mode = self.settings.get('dola_mode', 'pro')
            if mode == 'skill30':
                mode = 'pro'
            options = {'duration': seconds.group(1), 'dola_mode': mode}
        return dict(id=uuid.uuid4().hex, name=f'STT {stt}{part} • {code}',
                    task_code=code, lease=str(job.get('lease') or ''), script_id=script_id, stt=stt, auto_job=True,
                    video_index=video_index, video_count=count, owner_folder=owner_folder(job.get('folder_name')),
                    prompt=prompt if isinstance(prompt, str) else '', images=[], options=options,
                    status='Chờ chạy • Auto', run_action='new',
                    _auto_deadline=time.monotonic() + task_timeout_seconds(self.settings))

    def defer_claims(self):
        """Web mode reports results before claiming more (avoid over-claiming the
        server). Overridden to False where claims don't depend on pending reports."""
        return bool(self.journal.pending()) or bool(self.held and not self.settings.get('auto_retry') and not self.claimed)

    def take_claimed(self):
        """Return the next buffered task, doing a fresh GET only when the buffer is
        empty. GET claims jobs on the server, so we never GET again until the buffer
        drains, and never claim more than the free slots can eventually run."""
        with self.lock:
            if not self.settings.get('auto_retry'):
                fresh = deque()
                while self.claimed:
                    buffered = self.claimed.popleft()
                    if buffered.get('_retry_round') and not buffered.get('_session_retry'):
                        self.held[buffered['id']] = buffered
                    else:
                        fresh.append(buffered)
                self.claimed.extend(fresh)
            if not self.claimed and self.held:
                eligible = [tid for tid, item in self.held.items()
                            if self.settings.get('auto_retry') or item.get('_session_retry')]
                if not eligible:
                    return None
                tid = eligible[0]
                task = self.held.pop(tid)
                task['_attempt'] = uuid.uuid4().hex
                task['run_action'] = 'new'
                task.pop('conversation_url', None)
                task.pop('conversation_account', None)
                task['_auto_deadline'] = time.monotonic() + task_timeout_seconds(self.settings)
                self.task_cancels[tid] = DeadlineEvent(task['_auto_deadline'])
                self.emit('task_requeued', tid, task['_retry_round'])
                return task
        if not self.claimed:
            # +1: the caller already reserved one slot for the task returned here.
            jobs = self.client.claim(limit=self.free_slots() + 1, worker=self.machine)
            for job in jobs:
                task = self.build_task(job)
                if task is None:
                    continue
                previous = [r for r in self.journal.rows.values() if r.get('task_code') == task['task_code'] and r.get('account_id')]
                if previous:
                    task['last_account_id'] = previous[-1]['account_id']
                if not self.journal.claim(task, self.protected_key, self.machine):
                    continue  # this stt is already running/pending here
                with self.lock:
                    self.total += 1
                self.task_cancels[task['id']] = DeadlineEvent(task['_auto_deadline'])
                display = copy.deepcopy(task); display.pop('_auto_deadline', None); display.pop('lease', None)
                self.emit('auto_task', display)
                self.claimed.append(task)
        return self.claimed.popleft() if self.claimed else None

    def route_retry(self, slot, task):
        """Prefer the next account for a server-reissued task, waiting if busy."""
        from dola_tabs import retry_account_id
        with self.lock:
            eligible = [a for a in self.accounts if self.excluded.get(a['id'], 0) <= time.time()]
            target = retry_account_id(task, eligible)
            previous = task.get('last_account_id')
            if target is None or slot['account']['id'] != previous or len(eligible) < 2:
                return slot
            self.release(slot)
            occupied = next((s for s in self.slots if s['account']['id'] == target), None)
            if occupied is not None:
                if not occupied['blocked'] and not occupied.get('draining') and occupied['busy'] < self.capacity:
                    occupied['busy'] += 1
                    return occupied
            alternative = next((s for s in self.slots if s['account']['id'] != previous
                                and not s['blocked'] and not s.get('draining') and s['busy'] < self.capacity), None)
            if alternative is not None:
                alternative['busy'] += 1
                return alternative
            unused = next((a for a in eligible if a['id'] != previous
                           and all(s['account']['id'] != a['id'] for s in self.slots)), None)
            if unused is not None:
                idle = next((s for s in self.slots if not s.get('runner_active') and not s.get('draining') and not s['busy'] and s['inbox'].empty()), None)
                if idle is not None:
                    idle['account'] = copy.deepcopy(unused)
                    idle['blocked'] = False
                    idle['busy'] = 1
                    return idle
            self.claimed.append(task)
            return None

    def _loop(self):
        next_get = next_report = 0
        drained_at = None
        waiting_logged = False
        with ThreadPoolExecutor(max_workers=2) as video_pool, ThreadPoolExecutor(max_workers=len(self.slots)) as chrome_pool:
            workers = [chrome_pool.submit(self.worker, slot, video_pool) for slot in self.slots]
            try:
                while True:
                    if self.cancel.is_set(): self.pause()
                    now = time.monotonic()
                    if now >= next_report and self.journal.pending():
                        try:
                            self.flush_reports()
                        except Exception as exc:
                            self.log('Chưa báo được kết quả lên web (' + str(exc)[:200] +
                                     '); đã lưu để gửi lại. Tạm ngừng nhận thêm job.')
                            if isinstance(exc, JobsError) and exc.status == 401:
                                self.pause()
                            next_report = time.monotonic() + 10
                        else:
                            next_report = 0
                    with self.lock:
                        idle_blocked = [s for s in self.slots if s['blocked'] and not s['busy'] and not s.get('runner_active')]
                    for blocked in idle_blocked:
                        # Also brings accounts back when their credit lock ends (00:00 or Dola's reopen time).
                        self.rotate(blocked)
                    with self.lock:
                        busy = sum(s['busy'] for s in self.slots)
                        available = any(not s['blocked'] for s in self.slots)
                    if available:
                        self.waiting_logged = False
                    elif self.waiting_for_midnight():
                        if not self.waiting_logged:
                            with self.lock:
                                now = time.time()
                                reopen = min((u for u in self.excluded.values() if now < u < float('inf')), default=None)
                            when = unlock_text(reopen) if reopen else '00:00'
                            self.log(f'Tất cả tài khoản đã hết điểm/hết lượt; Auto vẫn bật, chạy tiếp lúc {when}.')
                            self.waiting_logged = True
                    elif not busy and not any(s.get('runner_active') for s in self.slots):
                        self.pause()
                    self.release_held()
                    if self.stop_receiving.is_set():
                        # Hand claimed-but-unstarted jobs back to the queue as errors.
                        while self.claimed:
                            self.abandon(self.claimed.popleft(), 'Auto dừng; job được trả lại hàng đợi.')
                        if not busy:
                            if not self.journal.pending(): break
                            drained_at = drained_at or now
                            if now - drained_at >= 30:
                                self.log('Còn báo cáo chờ trong auto_jobs_v2.json; bật Auto lại để gửi tiếp.')
                                break
                    if self.stop_receiving.is_set() or self.defer_claims() or now < next_get:
                        time.sleep(.1)
                        continue
                    slot = self.reserve()
                    if slot is None:
                        time.sleep(.1)
                        continue
                    try:
                        if self.stop_receiving.is_set() or self.cancel.is_set():
                            self.release(slot)
                            continue
                        task = self.take_claimed()
                        if task is None:
                            if not waiting_logged: self.log('Web chưa có job; tiếp tục chờ khi Auto đang bật.')
                            waiting_logged = True
                            self.release(slot)
                            next_get = time.monotonic() + self.poll_seconds
                            continue
                        waiting_logged = False
                        if task['id'] in self.lost_leases or self.explicitly_cancelled(task['id']):
                            self.release(slot)
                            self.abandon(task, 'Task đã bị dừng hoặc mất quyền giữ job.')
                            continue
                        slot = self.route_retry(slot, task)
                        if slot is None:
                            time.sleep(.1)
                            continue
                        try:
                            self.output_path(task)
                            if not task['prompt'].strip():
                                raise ValueError('Job không có prompt_video.')
                        except ValueError as exc:
                            task['_invalid_job'] = True
                            self.settle(slot, task, error=exc)
                            continue
                        slot['inbox'].put(task)
                        target = self.output_path(task)
                        duration = (task.get('options') or {}).get('duration', self.settings.get('duration', '15'))
                        self.log(f"Đã nhận STT {task['stt']} ({task['script_id'] or 'không có script_id'}) • {duration}s → "
                                 f"{slot['account']['name']} • lưu {target.parent.name}/{target.name}")
                    except Exception as exc:
                        self.release(slot)
                        if isinstance(exc, JobsError) and exc.temporary:
                            # Network / rate limit / server busy: keep Auto on and ask again later.
                            self.log('Chưa nhận được job: ' + str(exc)[:250] + ' Thử lại sau 15 giây.')
                            next_get = time.monotonic() + max(15, self.poll_seconds)
                            continue
                        self.pause()
                        self.log('Dừng nhận job: ' + str(exc)[:250] + ' Kiểm tra kết nối/key rồi bật Auto lại.')
            finally:
                self.workers_exit.set()
                for worker in workers:
                    worker.result()
