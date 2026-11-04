"""Serialized Google Sheets video queue with durable claims and operation receipts.

One writer per spreadsheet on this machine. Sheets is not a distributed queue:
do not run another machine against F/G concurrently without a server-side lock.
Uncertain writes are reconciled by receipt, never blindly replayed.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import quote
import uuid

from auto_jobs import AutoJobs, DeadlineEvent
from engine import atomic_json
from task_runtime import task_timeout_seconds, task_failure_status

DEFAULT_URL = 'https://docs.google.com/spreadsheets/d/1MI9WyRIKdfb4fpm6SCQlqi2RHEjQijhG_vEn9SOguWI/edit'


def reference_defaults():
    """Reuse only connection settings; never copy service-account private key material."""
    source = Path.home() / 'Documents/Codex/2026-09-25/l-m-gi-p-t-i/outputs/ContentStudio/data/workspace.json'
    try:
        cfg = json.loads(source.read_text('utf-8')).get('sheets', {})
        return dict(sheet_video_url=cfg.get('spreadsheet') or DEFAULT_URL,
                    sheet_video_tab=cfg.get('sheet_name') or 'Sheet1',
                    sheet_video_credentials=cfg.get('credentials_file') or '')
    except (OSError, ValueError):
        return {}


def cell(row, index):
    return row[index] if index < len(row) else ''


def count(value):
    if value is None or str(value).strip() == '':
        return 0
    if isinstance(value, bool) or not re.fullmatch(r'\d+(?:\.0+)?', str(value).strip()):
        raise ValueError('Số lượng F/G phải là số nguyên không âm.')
    number = int(float(value))
    if number > 10000:
        raise ValueError('Số lượng mỗi ô tối đa 10000.')
    return number


def serial(value):
    if isinstance(value, bool) or not re.fullmatch(r'\d{1,12}(?:\.0+)?', str(value).strip()):
        raise ValueError('STT ở A phải là số nguyên dương.')
    number = int(float(value))
    if number < 1:
        raise ValueError('STT ở A phải là số nguyên dương.')
    return str(number)


def scan_video_rows(rows):
    """Skip malformed input rows without changing either quantity cell."""
    eligible, issues, stts = [], [], []
    for number, row in enumerate(rows, 1):
        try:
            stt = serial(cell(row, 0))
        except ValueError:
            continue
        stts.append(stt)
        prompt = str(cell(row, 1) or '').strip()
        if not prompt:
            continue
        amounts, bad = [], False
        for column, index in (('F', 5), ('G', 6)):
            try:
                amounts.append(count(cell(row, index)))
            except ValueError as exc:
                issues.append(f'Bỏ qua dòng {number} • STT {stt}: ô {column}{number} không hợp lệ. {exc}')
                bad = True
        if not bad and any(amounts):
            eligible.append((number, stt, prompt, *amounts))
    return eligible, issues, stts


def import_sheet_tasks(api, cfg, settings, existing):
    """Read one snapshot into manual tasks; never claim or write Sheet cells."""
    eligible, issues, stts = scan_video_rows(api.rows())
    known = {t.get('sheet_import_key') for t in existing}
    source = cfg['id'] + '|' + cfg['tab']
    tasks, skipped = [], 0
    for row, stt, prompt, n30, n15 in eligible:
        if stts.count(stt) != 1:
            issues.append(f'Bỏ qua dòng {row}: STT {stt} trùng nhiều dòng.')
            continue
        for seconds, amount in ((30, n30), (15, n15)):
            for index in range(1, amount + 1):
                key = hashlib.sha256(json.dumps([source, stt, prompt, seconds, index],
                    ensure_ascii=False).encode()).hexdigest()
                if key in known:
                    skipped += 1
                    continue
                mode = settings.get('dola_mode', 'pro')
                if mode == 'skill30':
                    mode = 'pro'
                tasks.append(dict(id=uuid.uuid4().hex, name=f'STT {stt} • {seconds}s • video {index}',
                    stt=stt, prompt=prompt, images=[], options={'duration': str(seconds), 'dola_mode': mode},
                    status='Chờ chạy • lấy từ Sheet', sheet_import_key=key,
                    sheet_import_source={'id': cfg['id'], 'tab': cfg['tab'], 'row': row}))
                known.add(key)
    return tasks, issues, skipped


def config(settings):
    value = str(settings.get('sheet_video_url', DEFAULT_URL)).strip()
    match = re.search(r'/spreadsheets/d/([\w-]+)', value)
    sid = match.group(1) if match else value
    if not re.fullmatch(r'[\w-]{20,}', sid):
        raise ValueError('Link Google Sheet không hợp lệ.')
    tab = str(settings.get('sheet_video_tab', 'Sheet1')).strip()
    credentials = str(settings.get('sheet_video_credentials', '')).strip()
    if not tab or not Path(credentials).is_file():
        raise ValueError('Điền tên tab và chọn file JSON service account trong Cài đặt Auto Sheet.')
    return dict(id=sid, tab=tab, credentials=credentials)


class RejectedWrite(RuntimeError):
    """Google explicitly rejected the request without applying it."""


class SheetAPI:
    def __init__(self, cfg):
        from google.oauth2.service_account import Credentials
        from google.auth.transport.requests import AuthorizedSession
        info = json.loads(Path(cfg['credentials']).read_text('utf-8-sig'))
        if info.get('type') != 'service_account' or info.get('token_uri') != 'https://oauth2.googleapis.com/token':
            raise ValueError('Chọn file JSON service account gốc của Google.')
        minimal = {k: info[k] for k in ('type', 'private_key', 'client_email', 'token_uri')}
        self.session = AuthorizedSession(Credentials.from_service_account_info(
            minimal, scopes=['https://www.googleapis.com/auth/spreadsheets']))
        self.cfg = cfg
        self.base = 'https://sheets.googleapis.com/v4/spreadsheets/' + cfg['id']
        self.sheet_id = None
        self.last_call = 0

    def request(self, method, path, **kwargs):
        time.sleep(max(0, 1.1 - (time.monotonic() - self.last_call)))
        self.last_call = time.monotonic()
        try:
            response = self.session.request(method, self.base + path, timeout=30, **kwargs)
        except Exception:
            raise RuntimeError('Mất kết nối Google Sheets; giữ nhật ký để đối chiếu, không gửi ghi trùng.') from None
        if response.status_code >= 400:
            error = RejectedWrite if 400 <= response.status_code < 500 and response.status_code != 408 else RuntimeError
            raise error(f'Google Sheets HTTP {response.status_code}; kiểm tra quyền Editor, tên tab và hạn mức.')
        return response.json()

    def rows(self):
        tab = "'" + self.cfg['tab'].replace("'", "''") + "'"
        return self.request('GET', '/values/' + quote(tab + '!A:M', safe=''),
                            params={'valueRenderOption': 'UNFORMATTED_VALUE'}).get('values', [])

    def receipt(self, operation):
        result = self.request('POST', '/developerMetadata:search', json={'dataFilters': [
            {'developerMetadataLookup': {'metadataKey': 'studio_video_op', 'metadataValue': operation}}]})
        return bool(result.get('matchedDeveloperMetadata'))

    def prepare_write(self):
        if self.sheet_id is None:
            result = self.request('GET', '', params={'fields': 'sheets.properties'})
            self.sheet_id = next((s['properties']['sheetId'] for s in result.get('sheets', [])
                                  if s['properties']['title'] == self.cfg['tab']), None)
            if self.sheet_id is None:
                raise RuntimeError('Không tìm thấy tab Google Sheet.')

    def write_once(self, operation, row, values):
        self.prepare_write()
        requests = []
        for col, value in values.items():
            entered = {'numberValue': value} if isinstance(value, int) else {'stringValue': str(value)}
            requests.append({'updateCells': {'range': {'sheetId': self.sheet_id,
                'startRowIndex': row - 1, 'endRowIndex': row,
                'startColumnIndex': ord(col) - 65, 'endColumnIndex': ord(col) - 64},
                'rows': [{'values': [{'userEnteredValue': entered}]}], 'fields': 'userEnteredValue'}})
        # Receipt and cells are committed atomically by spreadsheets.batchUpdate.
        requests.append({'createDeveloperMetadata': {'developerMetadata': {
            'metadataKey': 'studio_video_op', 'metadataValue': operation,
            'visibility': 'DOCUMENT', 'location': {'spreadsheet': True}}}})
        self.request('POST', ':batchUpdate', json={'requests': requests})


class SheetJournal:
    def __init__(self, path, api, output, return_errors=True, on_warning=None):
        self.path, self.api = Path(path), api
        self.output = Path(output).resolve()
        self.return_errors = return_errors
        self.on_warning = on_warning or (lambda message: None)
        self.scan_warnings = set()
        self.lock = threading.RLock()
        self.data = json.loads(self.path.read_text('utf-8')) if self.path.exists() else {'tasks': {}, 'ops': {}}

    def save(self):
        with self.lock:
            atomic_json(self.path, self.data)

    def commit(self, operation):
        op = self.data['ops'][operation]
        if op['state'] == 'done':
            return
        if self.api.receipt(operation):
            op['state'] = 'done'
            self.save()
            return
        if op['state'] == 'submitted':
            raise RuntimeError('Lần ghi Sheet chưa có xác nhận. Tạm dừng nhận job; giữ nhật ký, không gửi lại phép cộng/xóa.')
        row_number, current = self.locate(op['stt'], op['prompt'])
        for column, expected in op['before'].items():
            if str(cell(current, ord(column) - 65) or '') != str(expected or ''):
                raise RuntimeError('Ô Sheet đã đổi trước khi ghi; giữ job để đối chiếu, không ghi đè số lượng mới.')
        op['row'] = row_number
        if hasattr(self.api, 'prepare_write'):
            self.api.prepare_write()
        # Persist intent before the network call. On a timeout, only read receipts.
        op['state'] = 'submitted'
        self.save()
        try:
            self.api.write_once(operation, op['row'], op['values'])
        except RejectedWrite:
            op['state'] = 'prepared'
            self.save()
            raise
        op['state'] = 'done'
        self.save()

    def recover(self):
        with self.lock:
            for operation in self.data['ops']:
                self.commit(operation)
            for task in list(self.data['tasks'].values()):
                if task['state'] == 'claiming':
                    task['state'] = 'queued'
                elif task['state'] == 'running':
                    output = Path(task['output'])
                    if output.is_file() and output.stat().st_size:
                        task.update(state='pending', status='done', folder=output.parent.as_posix())
                    else:
                        # Crash mid-render: retry this unit (Sheet count already decremented).
                        self.requeue(task)
                        task['state'] = 'reported'
            self.save()

    def locate(self, stt, prompt):
        found = []
        for number, row in enumerate(self.api.rows(), 1):
            try:
                same = serial(cell(row, 0)) == stt
            except ValueError:
                continue
            if same:
                found.append((number, row))
        if len(found) != 1 or str(cell(found[0][1], 1)).strip() != prompt.strip():
            raise RuntimeError(f'STT {stt} bị trùng, bị xóa hoặc đã đổi prompt; chưa ghi vào dòng khác.')
        return found[0]

    def claim_one(self):
        """Claim ONE video unit from the first eligible row and decrement its F/G
        cell by one (0 -> empty). 30s (F) is taken before 15s (G). On failure the
        unit is retried locally; the cell is never restored."""
        with self.lock:
            rows = self.api.rows()
            eligible, issues, stts = scan_video_rows(rows)
            for message in issues:
                if message not in self.scan_warnings:
                    self.on_warning(message)
            self.scan_warnings = set(issues)
            if not eligible:
                return []
            number, stt, prompt, n30, n15 = eligible[0]
            if stts.count(stt) != 1:
                raise RuntimeError(f'STT {stt} trùng nhiều dòng; sửa cột A trước khi nhận job.')
            seconds, column, remaining = (30, 'F', n30) if n30 > 0 else (15, 'G', n15)
            previous = [t['index'] for t in self.data['tasks'].values()
                        if t['stt'] == stt and t['seconds'] == seconds]
            folder = self.output / stt
            for path in folder.glob(f'{stt}_{seconds}-*.mp4'):
                match = re.fullmatch(rf'{stt}_{seconds}-(\d+)\.mp4', path.name)
                if match:
                    previous.append(int(match.group(1)))
            index = max(previous, default=0) + 1
            tid = uuid.uuid4().hex
            task = dict(id=tid, stt=stt, seconds=seconds, index=index, prompt=prompt,
                        row=number, claim_op=None, state='claiming',
                        output=str(folder / f'{stt}_{seconds}-{index}.mp4'))
            self.data['tasks'][tid] = task
            operation = uuid.uuid4().hex
            new_value = remaining - 1 if remaining - 1 > 0 else ''
            self.data['ops'][operation] = dict(state='prepared', row=number, values={column: new_value},
                stt=stt, prompt=prompt, before={column: cell(rows[number-1], ord(column)-65)})
            task['claim_op'] = operation
            self.save()
            self.commit(operation)
            task['state'] = 'queued'
            self.save()
            return [copy.deepcopy(task)]

    def queued(self):
        with self.lock:
            return [copy.deepcopy(t) for t in self.data['tasks'].values() if t['state'] == 'queued']

    def start(self, tid):
        with self.lock:
            self.data['tasks'][tid]['state'] = 'running'
            self.save()

    def finish(self, tid, status, folder='', note=''):
        with self.lock:
            task = self.data['tasks'][tid]
            task.update(state='pending', status=status, folder=folder, note=note,
                        return_error=self.return_errors)
            self.save()

    def pending(self):
        with self.lock:
            return [(tid, copy.deepcopy(t)) for tid, t in self.data['tasks'].items() if t['state'] == 'pending']

    def requeue(self, task):
        """Re-add a failed unit as a fresh queued task so it is retried until it
        succeeds. The Sheet count is NOT restored (the unit was already decremented
        when claimed); only a successful render writes back a path."""
        new_id = uuid.uuid4().hex
        self.data['tasks'][new_id] = dict(task, id=new_id, state='queued', claim_op=None,
            status=None, folder='', note='', report_op=None, return_error=None,
            retry_round=int(task.get('retry_round', 0)) + 1)

    def publish(self, tid):
        with self.lock:
            task = self.data['tasks'][tid]
            if task['state'] == 'reported':
                return
            if task['status'] != 'done':
                # Lỗi: không trả số lượng về Sheet; tạo lại đơn vị để chạy lại.
                self.requeue(task)
                task['state'] = 'reported'
                self.save()
                return
            if task.get('report_op'):
                self.commit(task['report_op'])
            else:
                row_number, row = self.locate(task['stt'], task['prompt'])
                column = 'L' if task['seconds'] == 30 else 'M'
                # Thành công: ghi ĐƯỜNG DẪN video vào L (30s) / M (15s).
                path = task.get('output') or task['folder']
                paths = [v.strip() for v in str(cell(row, ord(column) - 65) or '').splitlines() if v.strip()]
                if path not in paths:
                    paths.append(path)
                operation = uuid.uuid4().hex
                task['report_op'] = operation
                self.data['ops'][operation] = dict(state='prepared', row=row_number,
                    values={column: '\n'.join(paths)}, stt=task['stt'], prompt=task['prompt'],
                    before={column: cell(row, ord(column)-65)})
                self.save()
                self.commit(operation)
            task['state'] = 'reported'
            self.save()


class SheetAutoJobs(AutoJobs):
    def __init__(self, settings, accounts, data_dir, events, cancel, task_cancels, **kwargs):
        self.sheet_cfg = config(settings)
        base = dict(settings, auto_machine_code='sheet', auto_render_key_protected='')
        super().__init__(base, accounts, data_dir, events, cancel, task_cancels,
                         client_factory=lambda key: None, **kwargs)
        self.sheet_key = hashlib.sha256((self.sheet_cfg['id'] + '|' + self.sheet_cfg['tab']).encode()).hexdigest()

    def log(self, message):
        self.emit('log', 'Auto Sheet: ' + message.replace('Web chưa có job', 'Sheet chưa có số lượng F/G')
                  .replace('lên web', 'lên Sheet').replace('kết nối/key', 'kết nối/quyền Sheet'))

    def output_path(self, task):
        path = Path(task['sheet_output'])
        return path.with_name(path.stem + '_logo' + path.suffix) if task.get('_logo_output') else path

    def defer_claims(self):
        # Sheet errors are retried locally (requeued), so keep claiming new units
        # even while results are being written back; don't stall on pending reports.
        return False

    def build_sheet_task(self, saved):
        flat = (Path(self.settings['output']).resolve() / saved['stt'] / Path(saved['output']).name).as_posix()
        if saved['output'] != flat:
            saved['output'] = flat
            self.journal.data['tasks'][saved['id']]['output'] = flat
            self.journal.save()
        seconds = str(saved['seconds'])
        mode = self.settings.get('dola_mode', 'pro')
        if mode == 'skill30':
            mode = 'pro'
        return dict(id=saved['id'], stt=saved['stt'], script_id=f"{saved['stt']}_{seconds}-{saved['index']}",
                    name=f"STT {saved['stt']} • {seconds}s • video {saved['index']}", prompt=saved['prompt'],
                    images=[], options={'duration': seconds, 'dola_mode': mode}, auto_job=True, sheet_job=True,
                    sheet_output=saved['output'], last_account_id=saved.get('last_account_id'),
                    status='Chờ chạy • Auto Sheet', run_action='new')

    def take_claimed(self):
        if not self.claimed:
            # Lấy từng đơn vị một: ưu tiên job đang chờ (kể cả job chạy lại), nếu
            # không có thì mới claim thêm 1 từ Sheet.
            queued = sorted(self.journal.queued(), key=lambda t: int(t.get('retry_round', 0)))
            fresh = [t for t in queued if not t.get('retry_round')]
            saved = fresh[:1] or self.journal.claim_one() or queued[:1]
            for row in saved:
                task = self.build_sheet_task(row)
                self.total += 1
                self.emit('auto_task', copy.deepcopy(task))
                self.claimed.append(task)
        if not self.claimed:
            return None
        task = self.claimed.popleft()
        task['_auto_deadline'] = time.monotonic() + task_timeout_seconds(self.settings)
        self.task_cancels[task['id']] = DeadlineEvent(task['_auto_deadline'])
        self.journal.start(task['id'])
        return task

    def abandon(self, task, note):
        # Accepted units that never started stay queued locally; don't return them as failures.
        self.emit('status', task['id'], 'Chờ chạy • đã giữ job Sheet cho lần bật tiếp')
        self.emit('task_done', task['id'])

    def flush_reports(self):
        for tid, row in self.journal.pending():
            self.journal.publish(tid)
            self.emit('sheet_report', tid, row['status'])
            self.log(f"STT {row['stt']} • {row['seconds']}s: " +
                     ('đã ghi thư mục video.' if row['status'] == 'done' else
                      'đã xử lý kết quả lỗi; trả số lượng theo cài đặt.'))

    def run(self):
        from dola_browser import ProfileLease
        try:
            # Shared lock directory across installations under this Windows user.
            locks = Path(os.environ.get('LOCALAPPDATA', str(self.data_dir))) / 'StudioPython' / 'sheet_locks'
            with ProfileLease(locks / self.sheet_key, locks):
                self.journal = SheetJournal(self.data_dir / ('sheet_jobs_' + self.sheet_key[:24] + '.json'),
                    SheetAPI(self.sheet_cfg), self.settings['output'], self.settings.get('sheet_return_errors', True),
                    on_warning=self.log)
                self.journal.recover()
                self._loop()
        except Exception as exc:
            self.log('Đã dừng: ' + str(exc)[:300])
        finally:
            self.emit('auto_finished', self.ok, self.total)
