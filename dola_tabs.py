"""One browser owner per profile, with cooperative per-task tab polling."""
from collections import deque
import queue
import time
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

from browser_runtime import browser_runtime
from dola_browser import DeferredVideo, ProfileLease, ProfileStopped, launch_context, render_dola_steps
from engine import Cancelled
from task_runtime import (StartupDeadline, StartupTimeout, TaskCancel,
                          startup_timeout_seconds, task_settings)
from work_queue import WorkQueue, snapshot, take_for


def _attempt(task):
    return task.get('_attempt', task['id'])


class AccountIntake:
    """A queue view that leaves tasks intended for another account untouched."""
    def __init__(self, source, allowed):
        self.source, self.allowed = source, allowed

    def get_nowait(self):
        return take_for(self.source, self.allowed)


def retry_account_id(task, accounts):
    if task.get('run_action') == 'resume':
        return None
    previous = task.get('last_account_id') or task.get('execution', {}).get('account_id')
    ids = [a['id'] for a in accounts]
    return ids[(ids.index(previous) + 1) % len(ids)] if previous in ids else None


def dispatch_profiles(initial, profile_count, capacity, intake, cancel, run, *, account_count=None, usable=None, allowed=None):
    """Run accounts top-down. An open Chrome keeps pulling new tasks from intake;
    a freed slot moves on to the next account instead of relaunching the same one.
    Returns tasks left over because no usable account remains."""
    account_count=profile_count if account_count is None else account_count
    usable=usable or (lambda index: True)
    allowed=allowed or (lambda index, task: True)
    slots=max(1,min(profile_count,account_count))
    pending=intake if intake is not None else WorkQueue()
    for task in initial:
        pending.put(task)
    cursor=0

    def next_account(busy):
        nonlocal cursor
        for step in range(account_count):
            index=(cursor+step)%account_count
            if index not in busy and usable(index) and any(allowed(index, t) for t in snapshot(pending)):
                cursor=index+1
                return index
        return None

    def take(group, index):
        while len(group)<capacity:
            try:group.append(take_for(pending, lambda t: allowed(index,t)))
            except queue.Empty:break
        return group

    with ThreadPoolExecutor(max_workers=slots) as executor:
        active={}
        def fill():
            while len(active)<slots and not pending.empty():
                index=next_account(set(active.values()))
                if index is None:return
                group=take([], index)
                if not group:return
                active[executor.submit(run,index,group)]=index
        fill()
        while active:
            done,_=wait(active,timeout=.2,return_when=FIRST_COMPLETED)
            for future in done:
                active.pop(future)
                future.result()
            if not cancel.is_set():fill()
    if intake is not None and (cancel.is_set() or any(usable(index) for index in range(account_count))):
        # Late arrivals stay in intake for the caller's next round.
        return []
    left=[]
    while True:
        try:left.append(pending.get_nowait())
        except queue.Empty:break
    return left


def cycle_jobs(tasks, capacity, cancel, open_job, settle, activate, close_page,
               *, clock=time.monotonic, interval=3.0, browser_idle=None, task_cancelled=lambda task: False,
               pull_more=None, idle_wait=None):
    """All browser calls stay on this thread; each generator advances one phase.
    idle_wait() True keeps the browser open while idle, waiting for new tasks."""
    waiting=deque(tasks)
    seen=list(tasks)
    queued={_attempt(task) for task in tasks}
    active=[]
    processing=[]
    completed=set()
    last_switch=None
    browser_closed=False

    def finish(task, result=None, error=None):
        key=_attempt(task)
        if key in completed:return
        completed.add(key)
        settle(task,result,error)

    def take_new():
        if pull_more is None:
            return
        room=capacity-len(active)-len(waiting)
        if room<=0:
            return
        for task in pull_more(room):
            key=_attempt(task)
            if key in completed or key in queued:
                continue
            queued.add(key)
            seen.append(task)
            waiting.append(task)

    def focus(page):
        nonlocal last_switch
        if cancel.is_set():raise Cancelled('Đã dừng các tab Dola.')
        if last_switch is not None:
            delay=max(0,interval-(clock()-last_switch))
            if delay and cancel.wait(delay):raise Cancelled('Đã dừng các tab Dola.')
        activate(page)
        last_switch=clock()

    def dispose(slot):
        try:slot['job'].close()
        except Exception:pass
        try:close_page(slot['page'])
        except Exception:pass

    try:
        while True:
            take_new()
            if not waiting and not active and not processing:
                if idle_wait is None or not idle_wait():
                    break
                cancel.wait(.2)
                continue
            if cancel.is_set():raise Cancelled('Đã dừng các tab Dola.')
            while waiting and len(active)<capacity:
                task=waiting.popleft()
                slot=None
                try:
                    if task_cancelled(task):raise Cancelled('Đã dừng task này.')
                    page,job=open_job(task)
                    slot={'task':task,'page':page,'job':job}
                    focus(page)
                    next(job)  # Navigate, then yield before prompt submission.
                    active.append(slot)
                except StopIteration as done:
                    finish(task,done.value)
                    if slot:dispose(slot)
                except ProfileStopped:
                    if slot:dispose(slot)
                    raise
                except Exception as exc:
                    finish(task,error=exc)
                    if slot:dispose(slot)
            for slot in list(active):
                try:
                    if task_cancelled(slot['task']):raise Cancelled('Đã dừng task này.')
                    focus(slot['page'])
                    next(slot['job'])
                except StopIteration as done:
                    value=done.value
                    active.remove(slot)
                    if isinstance(value,DeferredVideo):
                        try:close_page(slot['page'])
                        except Exception:pass
                        processing.append({'task':slot['task'],'job':value})
                    else:
                        finish(slot['task'],value)
                        dispose(slot)
                except ProfileStopped:
                    dispose(slot)
                    active.remove(slot)
                    raise
                except Exception as exc:
                    finish(slot['task'],error=exc)
                    dispose(slot);active.remove(slot)
            for slot in list(processing):
                future=slot['job'].future
                if not future.done():continue
                processing.remove(slot)
                try:
                    if task_cancelled(slot['task']):raise Cancelled('Đã dừng task này.')
                    finish(slot['task'],future.result())
                except Exception as exc:finish(slot['task'],error=exc)
            if pull_more is None and not browser_closed and not waiting and not active and processing:
                # No live intake: release Chrome while FFmpeg finishes.
                browser_closed=True
                if browser_idle is not None:browser_idle()
            if processing and not active and not waiting:
                cancel.wait(.05)
    except Exception as exc:
        for task in seen:finish(task,error=exc)
    finally:
        for slot in active:dispose(slot)


def _run_cooperative_profile_tabs(tasks, account, settings, cancel, report_for, settle,
                     session_cache=None, session_cache_lock=None, video_submit=None, task_cancels=None,
                     intake=None, browser_report=None, profile_error=None, accepting=None, keep_open=None):
    capacity=max(1,min(5,int(settings.get('tabs_per_profile',3))))
    delivered=set()
    def deliver(task,result=None,error=None):
        key=_attempt(task)
        if key in delivered:return
        delivered.add(key)
        settle(task,result,error)
    try:
        with ProfileLease(account['profile'],settings['lock_root']), browser_runtime(settings) as runtime:
            # Mở profile/trình duyệt phải xong trong hạn khởi động; quá hạn thì trả job về web
            # để luồng nhận job khác, không kẹt mãi ở "Chờ chạy".
            startup=StartupDeadline(cancel,startup_timeout_seconds(settings))
            try:
                context=launch_context(runtime,settings,account,video_session=True,cancel=startup,
                                       report=browser_report or report_for(tasks[0],account))
            except BaseException as exc:
                if startup.expired and not cancel.is_set():
                    raise StartupTimeout(f'Quá {startup_timeout_seconds(settings)} giây chưa mở '
                                         f'được profile/trình duyệt; trả job về web.') from exc
                raise
            try:
                resolved_proxy = getattr(context, '_seedance_proxy', None) or account.get('proxy') or {}
                if resolved_proxy.get('server'):
                    account['proxy'] = dict(resolved_proxy)
                for t in tasks:
                    report_for(t, account)
                # Reuse a launch/startup tab once; never reuse an active task's tab.
                initial=list(context.pages[:1])
                chrome_session={}
                def open_job(task):
                    page=initial.pop() if initial else context.new_page()
                    report=report_for(task,account)
                    report(f"{task['name']} → {account['name']} • tab riêng, tối đa {capacity} tab/tài khoản")
                    event = task_cancels.get(task['id']) if task_cancels else None
                    token = TaskCancel(cancel, event) if event is not None else cancel
                    return page,render_dola_steps(task,task_settings(settings,task),account,token,report,
                                session_cache,session_cache_lock,shared_context=context,task_page=page,
                                video_submit=video_submit,chrome_session=chrome_session)
                def release_page(page):
                    # Closing the last persistent Chromium tab can end the whole
                    # profile before a queued task gets its turn. Keep it reusable.
                    if not page.is_closed() and len(context.pages)==1:
                        initial.append(page)
                    elif not page.is_closed():
                        page.close()
                browser_closed=[False]
                def close_browser_when_idle():
                    if browser_closed[0]:return
                    browser_closed[0]=True
                    try:context.close()
                    except Exception:pass
                    report_for(tasks[0],account)('Đã tải đủ link; đã đóng Chrome, FFmpeg tiếp tục xử lý riêng.')
                def pull(room):
                    found=[]
                    if accepting is not None and not accepting():
                        return found  # Account locked (out of credits): let the next account take new tasks.
                    for _ in range(max(0, room)):
                        try: found.append(intake.get_nowait())
                        except queue.Empty: break
                    return found
                def idle_wait():
                    # Auto: keep this Chrome open between jobs instead of relaunching it for each one.
                    return (not cancel.is_set() and keep_open()
                            and (accepting is None or accepting()))
                cycle_jobs(tasks,capacity,cancel,open_job,deliver,
                           lambda page:page.bring_to_front(),release_page,
                           browser_idle=close_browser_when_idle,
                           task_cancelled=lambda task: bool(task_cancels and task_cancels.get(task['id']) and task_cancels[task['id']].is_set()),
                           pull_more=pull if intake is not None else None,
                           idle_wait=idle_wait if keep_open is not None and intake is not None else None)
            finally:
                try:context.close()
                except Exception:pass
    except Exception as exc:
        if profile_error is not None:profile_error(exc)
        for task in tasks:deliver(task,error=exc)


def run_profile_tabs(tasks, account, settings, cancel, report_for, settle,
                     session_cache=None, session_cache_lock=None, video_submit=None, task_cancels=None,
                     intake=None, browser_report=None, profile_error=None, accepting=None, keep_open=None):
    from account_browser_backend import prepare_account_settings
    for task in tasks:
        report_for(task, account)('Đang mở profile và kiểm tra cookie…')
    # Chuẩn bị backend (mở/tạo profile Hidemium, phân giải proxy…) cũng phải xong trong hạn
    # khởi động, nếu không job sẽ kẹt trước cả khi mở trình duyệt.
    startup = StartupDeadline(cancel, startup_timeout_seconds(settings))
    try:
        settings = prepare_account_settings(settings, account, startup, browser_report or report_for(tasks[0], account))
    except Exception as exc:
        if startup.expired and not cancel.is_set():
            exc = StartupTimeout(f'Quá {startup_timeout_seconds(settings)} giây chưa chuẩn bị '
                                 f'được profile; trả job về web.')
        if profile_error is not None:
            profile_error(exc)
        for task in tasks:
            settle(task, None, exc)
        return
    if settings.get('parallel_chat_submit', True) and not settings.get('use_gpm') and not settings.get('use_hidemium'):
        from seedance_parallel import run_profile
        run = run_profile
    else:
        run = _run_cooperative_profile_tabs
    return run(tasks, account, settings, cancel, report_for, settle,
               session_cache, session_cache_lock, video_submit, task_cancels,
               intake, browser_report, profile_error, accepting, keep_open)
