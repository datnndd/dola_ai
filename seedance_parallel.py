"""Independent chat threads; one Chrome/profile/proxy owner per account."""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
import queue
import threading

from browser_runtime import browser_runtime
from dola_browser import (BrowserSteps, CHAT_URL, DeferredVideo, ProfileLease,
                          ProfileStopped, ensure_chrome_session, launch_context,
                          render_dola_steps)
from engine import Cancelled
from task_runtime import (StartupDeadline, StartupTimeout, TaskCancel,
                          startup_timeout_seconds, task_settings)


@contextmanager
def workers(capacity, stop):
    with ThreadPoolExecutor(max_workers=capacity, thread_name_prefix='seedance-chat') as executor:
        try:
            yield executor
        finally:
            stop.set()  # Signal before executor joins any unfinished workers.


def browser_endpoint(account):
    lines = (Path(account['profile']) / 'DevToolsActivePort').read_text(encoding='utf-8').splitlines()
    port = int(lines[0])
    if not 0 < port < 65536 or len(lines) < 2 or not lines[1].startswith('/devtools/browser/'):
        raise RuntimeError('Chrome chưa cấp kết nối cho các chat độc lập.')
    return f'ws://127.0.0.1:{port}{lines[1]}'


def page_target(context, page):
    session = context.new_cdp_session(page)
    try:
        return session.send('Target.getTargetInfo')['targetInfo']['targetId']
    finally:
        session.detach()


def run_chat(endpoint, task, account, settings, cancel, report, name,
             session_cache, session_cache_lock, video_submit, reuse_target=None):
    # Each thread creates its own driver and wrapper objects; none cross threads.
    with browser_runtime(settings) as runtime:
        browser = runtime.chromium.connect_over_cdp(endpoint, timeout=15000)
        context = browser.contexts[0]
        if reuse_target is not None:
            page = next((candidate for candidate in context.pages
                         if page_target(context, candidate) == reuse_target), None)
            if page is None:
                raise ProfileStopped('Tab Dola đã xác thực không còn tồn tại; chưa gửi prompt.')
            report('Dùng lại tab Dola đã đăng nhập, không mở tab about:blank.')
        else:
            page = context.new_page()
            report('Đã tạo tab task riêng; đang mở Dola.')
        settings = dict(settings, _reuse_ready_page=reuse_target is not None)
        job = render_dola_steps(task, task_settings(settings, task), account, cancel, report,
                session_cache, session_cache_lock, shared_context=context, task_page=page,
                video_submit=video_submit, chrome_session={'valid': True, 'name': name})
        try:
            while True:
                next(job)
        except StopIteration as done:
            return done.value
        finally:
            job.close()
            try:
                if reuse_target is None:
                    page.close()
            except Exception:
                pass
            # Driver exit disconnects this client. Never close the shared context.


def run_profile(tasks, account, settings, cancel, report_for, settle,
                session_cache=None, session_cache_lock=None, video_submit=None, task_cancels=None,
                intake=None, browser_report=None, profile_error=None, accepting=None, keep_open=None):
    if not tasks:
        return
    settings = dict(settings, _parallel_tabs=True)
    capacity = max(1, min(5, int(settings.get('tabs_per_profile', 3))))
    stop = threading.Event()
    token = TaskCancel(cancel, stop)
    pending = deque(tasks)
    known = list(tasks)
    delivered = set()
    processing = []
    active = {}
    report = browser_report or report_for(tasks[0], account)
    def deliver(task, value=None, error=None):
        key = task.get('_attempt', task['id'])
        if key not in delivered:
            delivered.add(key)
            settle(task, value, error)
    def task_token(task):
        event = task_cancels.get(task['id']) if task_cancels else None
        return TaskCancel(token, event) if event is not None else token
    try:
        with ProfileLease(account['profile'], settings['lock_root']), browser_runtime(settings) as runtime:
            # Mở Chrome + điều hướng + đăng nhập phải xong trong hạn khởi động; quá hạn thì
            # trả job về web để luồng nhận job khác, không kẹt mãi ở "Chờ chạy".
            startup = StartupDeadline(token, startup_timeout_seconds(settings))
            owner = None
            try:
                owner = launch_context(runtime, settings, account, video_session=True, cancel=startup, report=report)
                settings = dict(settings, _browser_proxy=dict(getattr(owner, '_seedance_proxy', {}) or {}))
                anchor = owner.pages[0] if owner.pages else owner.new_page()
                steps = BrowserSteps(anchor, settings, startup, report)
                steps.navigate(CHAT_URL)
                name = ensure_chrome_session({}, owner, anchor, steps, settings, account, startup, report)
                endpoint = browser_endpoint(account)
                anchor_id = page_target(owner, anchor)
            except BaseException as exc:
                if owner is not None:
                    try: owner.close()
                    except Exception: pass
                if startup.expired and not token.is_set():
                    raise StartupTimeout(f'Quá {startup_timeout_seconds(settings)} giây chưa mở '
                                         f'được Chrome/đăng nhập; trả job về web.') from exc
                raise
            try:
                anchor_free = True
                anchor_jobs = set()
                report(f'Đã mở Chrome: tối đa {capacity} chat thao tác và gửi prompt độc lập.')
                with workers(capacity, stop) as executor:
                    while True:
                        if token.is_set():
                            raise Cancelled('Đã dừng các chat của tài khoản.')
                        room = capacity - len(active)
                        if intake is not None and (accepting is None or accepting()):
                            while len(pending) < room:
                                try:
                                    task = intake.get_nowait()
                                except queue.Empty:
                                    break
                                pending.append(task)
                                known.append(task)
                        while pending and len(active) < capacity:
                            task = pending.popleft()
                            task_cancel = task_token(task)
                            if task_cancel.is_set():
                                deliver(task, error=Cancelled('Đã dừng task này.'))
                                continue
                            reuse = anchor_id if anchor_free else None
                            if reuse is not None:
                                anchor_free = False
                            future = executor.submit(run_chat, endpoint, task, account, settings,
                                task_cancel, report_for(task, account), name,
                                session_cache, session_cache_lock, video_submit, reuse)
                            active[future] = task
                            if reuse is not None:
                                anchor_jobs.add(future)
                        for future, task in list(active.items()):
                            if not future.done():
                                continue
                            del active[future]
                            if future in anchor_jobs:
                                anchor_jobs.remove(future)
                                anchor_free = True
                            try:
                                value = future.result()
                                if task_token(task).is_set():
                                    raise Cancelled('Đã dừng task này.')
                                if isinstance(value, DeferredVideo):
                                    processing.append((task, value.future))
                                else:
                                    deliver(task, value)
                            except ProfileStopped as exc:
                                deliver(task, error=exc)
                                stop.set()
                            except Exception as exc:
                                deliver(task, error=exc)
                        for task, future in list(processing):
                            if future.done():
                                processing.remove((task, future))
                                try:
                                    if task_token(task).is_set():
                                        raise Cancelled('Đã dừng task này.')
                                    deliver(task, future.result())
                                except Exception as exc:
                                    deliver(task, error=exc)
                        stay = (intake is not None and keep_open is not None and keep_open()
                                and (accepting is None or accepting()))
                        if not pending and not active and not processing and not stay:
                            break
                        if anchor.is_closed():
                            raise ProfileStopped('Tab Dola chính đã đóng; dừng profile để tránh gửi trùng.')
                        anchor.wait_for_timeout(100)
            finally:
                stop.set()
                owner.close()
    except Exception as exc:
        if profile_error is not None:
            profile_error(exc)
        for task in known:
            deliver(task, error=exc)
