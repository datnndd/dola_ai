"""Dola browser workflow. Uses normal visible UI and the video served to the page."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import random
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from contextlib import contextmanager, ExitStack, nullcontext
from urllib.parse import urlsplit, urljoin

from engine import ORIGINAL, Cancelled, atomic_json, find_ffmpeg, run_process, video_resolution
from task_runtime import (ensure_task_time, task_timeout_seconds, requires_skill_file, VideoDownloadError,
                          PolicyBlocked, RetryLimit)
from cookie_accounts import browser_proxy, load_cookies, save_cookies
from gpm_browser import connect_gpm, manual_gpm, rename_profile, prepare_profile, GpmError
from hidemium_browser import connect_hidemium, manual_hidemium
from account_browser_backend import account_settings, prepare_account_settings
from video_postprocess import export_video, validate_video_edit
from dola_media import DolaMediaTracker, VideoSource, media_url, has_video_result
from mp4_attachment import scan_files, download_file_card
from proxy_bridge import native_proxy
from proxyvn import BoundContext, open_runtime_proxy, gpm_proxy_account
from video_receipt import media_receipt, retry_is_safe
from browser_runtime import browser_runtime, BrowserError, BrowserTimeout
from studio_extension import launch_options as extension_launch_options, verify_loaded as verify_studio_extension
from chrome_window import OFFSCREEN_ARG, set_window_visible
from seedance_ui import prepare as prepare_seedance, prompt_text, clarification
from seedance_browser import executable as seedance_executable

CHAT_URL = 'https://www.dola.com/chat'
EDITOR = 'textarea:visible, [contenteditable="true"]:visible'
# Lời chào gửi trước mỗi hội thoại mới (giống người thật mở lời). Nhiều câu khác nhau,
# mỗi lần chào lại chọn một câu khác để không lặp (tránh bị nhận diện bot).
GREETINGS = ('chào bạn', 'xin chào', 'chào bạn nhé', 'hello bạn', 'chào Dola',
             'chào bạn, giúp mình chút nhé', 'hi bạn', 'hello', 'hi', 'chào nha',
             'chào buổi sáng', 'chào, mình cần nhờ chút', 'alo chào bạn', 'hế lô',
             'chào bạn ơi', 'chào Dola nhé', 'hi, giúp mình với', 'chào, bạn khoẻ không',
             'xin chào ạ', 'chào bạn, mình hỏi chút')
# Chào tối đa bao nhiêu lần (mỗi lần F5 + câu chào khác) trước khi coi phiên là lỗi.
GREETING_ATTEMPTS = 5
# Đã thấy video nhưng không lấy được link tải: chờ ngần này giây rồi F5.
LINK_STUCK_GRACE_SECONDS = 75
# Số lần F5 tối đa để cố bắt link tải khi đã có video.
LINK_F5_LIMIT = 3
# Sau khi trang load xong (F5), chờ ngần này giây rồi mới bắt link tải.
LINK_AFTER_RELOAD_WAIT = 15
VIDEO_TILE = '[class*="block-video-"], [data-flow-product-card-root], [data-testid*="product-card" i], [data-testid*="flow-product" i], [class*="video-card" i]'
DIALOG_OVERLAY = '[data-slot="dialog-overlay"][data-state="open"], [data-slot="alert-dialog-overlay"][data-state="open"]'
DIALOG = '[role="dialog"][data-state="open"], [role="alertdialog"][data-state="open"]'
DIALOG_CLOSE = ('[data-slot="dialog-close"], button[aria-label*="close" i], button[aria-label*="đóng" i], '
                'button[aria-label*="dismiss" i]')
_profile_guard = threading.Lock()
_profiles_in_use = set()


def find_3big_browser():
    """Compatibility entry point for the local Seedance Chrome bundle."""
    return seedance_executable() or find_browser()


def find_browser(configured=''):
    bundled = seedance_executable()
    candidates = [Path(bundled)] if bundled else []
    if configured:
        candidates.append(Path(configured))
    candidates.append(Path(os.environ.get('LOCALAPPDATA', '')) / 'Google/Chrome/Application/chrome.exe')
    candidates.extend([Path(os.environ.get('PROGRAMFILES', 'C:/Program Files')) / 'Google/Chrome/Application/chrome.exe',
                       Path(os.environ.get('PROGRAMFILES(X86)', 'C:/Program Files (x86)')) / 'Google/Chrome/Application/chrome.exe'])
    ms_pw = Path(os.environ.get('LOCALAPPDATA', '')) / 'ms-playwright'
    if ms_pw.is_dir():
        candidates.extend(sorted(ms_pw.glob('chromium-*/chrome-win*/chrome.exe'), reverse=True))
    return next((str(p.resolve()) for p in candidates if p.is_file()), '')


def discover_accounts():
    folder = ORIGINAL / 'accounts'
    if not folder.is_dir():
        return []
    return [{'id': hashlib.sha256(str(p.resolve()).encode()).hexdigest()[:24], 'name': p.name,
             'profile': str(p.resolve()), 'selected': True, 'connection': 'Profile có sẵn'}
            for p in sorted(folder.iterdir()) if p.is_dir()]


class ProfileLease:
    """Prevent simultaneous use by two workers or two copies of this app."""
    def __init__(self, profile, lock_root):
        self.profile = str(Path(profile).resolve())
        self.key = os.path.normcase(self.profile)
        self.root = Path(lock_root)
        self.handle = None

    def __enter__(self):
        with _profile_guard:
            if self.key in _profiles_in_use:
                raise RuntimeError('Profile đang được dùng trong tác vụ hoặc cửa sổ đăng nhập khác.')
            _profiles_in_use.add(self.key)
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            lock = self.root / (hashlib.sha256(self.key.encode()).hexdigest() + '.lock')
            self.handle = open(lock, 'a+b')
            self.handle.seek(0)
            if lock.stat().st_size == 0:
                self.handle.write(b'0'); self.handle.flush(); self.handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise RuntimeError('Profile đang được mở bởi một phiên Studio Python khác.') from None

    def __exit__(self, *args):
        if self.handle:
            self.handle.close()
        with _profile_guard:
            _profiles_in_use.discard(self.key)


def check_cancel(cancel):
    if cancel.is_set():
        raise Cancelled('Đã dừng điều khiển trình duyệt. Tác vụ Dola đã gửi có thể vẫn chạy trên máy chủ.')


class BrowserSteps:
    """Steady, visible pacing and manual challenge handoff; no challenge solving."""
    def __init__(self, page, settings, cancel, report):
        self.page, self.settings, self.cancel, self.report = page, settings, cancel, report
        self.delay = max(.2, min(10, float(settings.get('action_delay', 3))))
        self.paused = 0.0

    def wait(self, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            check_cancel(self.cancel)
            if self.page.is_closed():raise RuntimeError('Cửa sổ đã đóng; mở lại phiên hoặc dùng Lấy kết quả cũ nếu đã gửi yêu cầu.')
            self.page.wait_for_timeout(min(100, max(1, (deadline-time.monotonic())*1000)))

    def challenge_visible(self):
        # Only visible verification surfaces; hidden CAPTCHA infrastructure is ignored.
        markers = self.page.locator('iframe[src*="captcha" i], iframe[title*="challenge" i], '
                                    'iframe[src*="challenges.cloudflare.com" i], '
                                    '[id*="captcha" i], [class*="captcha" i]')
        if any(m.is_visible() for m in markers.all()):
            return True
        messages = self.page.get_by_text(re.compile(
            r'^(?:Please )?(?:verify (?:that )?you are human|complete the security check|'
            r'xác minh (?:bạn là con người|bảo mật)|vui lòng hoàn thành xác minh)[.!…]?$', re.I))
        return any(m.is_visible() for m in messages.all())

    def guard(self):
        check_cancel(self.cancel)
        ensure_task_time(self.settings, self.report)
        if self.page.is_closed():raise RuntimeError('Cửa sổ đã đóng. Nếu đã gửi yêu cầu, dùng Lấy kết quả cũ.')
        # SPA navigation can destroy the JavaScript context for a brief moment,
        # especially just after submitting. Let the navigation settle and retry
        # on the next poll instead of failing an already-submitted task.
        try:
            online = self.page.evaluate('navigator.onLine')
        except Exception as exc:
            if 'execution context was destroyed' in str(exc).lower() or 'navigation' in str(exc).lower():
                return
            raise
        # Wait for connectivity without reloading or resending an in-flight task.
        if online is False:
            self.report('Trình duyệt đang mất mạng; chờ kết nối trở lại, không gửi lại yêu cầu.')
            start=time.monotonic()
            try:
                while self.page.evaluate('navigator.onLine') is False:
                    if time.monotonic()-start>=120:
                        raise TimeoutError('Mất mạng quá 2 phút. Kiểm tra mạng/proxy; dùng Lấy kết quả cũ nếu đã gửi.')
                    self.wait(.5)
                self.wait(1)
            finally:self.paused+=time.monotonic()-start
        if not self.challenge_visible(): return
        hidden = not self.settings.get('show_browser', True) and not self.settings.get('use_gpm')
        if hidden:
            # Off-screen window: bring it on-screen so the user can verify by hand.
            if not set_window_visible(self.page, True):
                raise RuntimeError('Cần xác minh thủ công. Bật Hiện cửa sổ trình duyệt; nếu đã gửi yêu cầu, dùng Lấy kết quả cũ.')
            self.report('Cần xác minh thủ công: đã đưa cửa sổ Chrome ra màn hình, hãy xử lý CAPTCHA. Tool đang tạm dừng thao tác (tối đa 10 phút).')
        else:
            self.report('Cần xác minh thủ công: hãy xử lý CAPTCHA trong cửa sổ trình duyệt. Tool đang tạm dừng thao tác (tối đa 10 phút).')
        start = time.monotonic()
        try:
            while self.challenge_visible():
                if time.monotonic() - start >= 600:
                    raise TimeoutError('Hết thời gian chờ xác minh thủ công. Nếu đã gửi yêu cầu, dùng Lấy kết quả cũ.')
                self.wait(.5)
            self.wait(1)
            self.report('Xác minh đã đóng; ẩn lại Chrome và tiếp tục tác vụ.' if hidden else 'Xác minh đã đóng; tiếp tục tác vụ.')
        finally:
            self.paused += time.monotonic() - start
            if hidden and not self.page.is_closed():
                set_window_visible(self.page, False)

    def pause(self):
        self.guard()
        self.wait(random.uniform(self.delay * .6, self.delay * 1.4))
        self.guard()

    def open_dialog(self):
        """A visible Dola modal (announcement, promo, confirm) whose overlay blocks clicks."""
        overlays = self.page.locator(DIALOG_OVERLAY)
        if not any(o.is_visible() for o in overlays.all()):
            return None
        dialogs = [d for d in self.page.locator(DIALOG).all() if d.is_visible()]
        return dialogs[-1] if dialogs else self.page.locator(DIALOG_OVERLAY).last

    def dismiss_blocking_dialog(self, target):
        """Close a modal covering `target` (Esc, then its own close button).
        Never touches a target inside that dialog or a verification page."""
        dialog = self.open_dialog()
        if dialog is None or self.challenge_visible():
            return
        try:
            if target.evaluate('el => !!el.closest(\'[role="dialog"],[role="alertdialog"]\')'):
                return
        except Exception:
            pass
        try:
            title = ' '.join(dialog.inner_text(timeout=2000).split())[:80]
        except Exception:
            title = ''
        self.report(f'Dola đang mở hộp thoại che nút bấm{" (" + title + ")" if title else ""}; đóng hộp thoại rồi bấm tiếp.')
        for _ in range(3):
            self.page.keyboard.press('Escape')
            self.wait(.8)
            dialog = self.open_dialog()
            if dialog is None:
                return
            closers = [c for c in dialog.locator(DIALOG_CLOSE).all() if c.is_visible()]
            if closers:
                closers[0].click(timeout=5000)
                self.wait(.8)
                if self.open_dialog() is None:
                    return
        raise RuntimeError(f'Dola đang mở hộp thoại chặn thao tác{" (" + title + ")" if title else ""}; chưa đóng được, chưa gửi prompt.')

    def click(self, locator, timeout=15000, before_click=None):
        self.pause()
        if hasattr(locator, 'wait_for'):
            locator.wait_for(state='visible',timeout=timeout)
        else:
            locator.wait_for_element_state('visible',timeout=timeout)
        self.dismiss_blocking_dialog(locator)
        locator.scroll_into_view_if_needed(timeout=timeout)
        # Trial waits for enabled/stable/unobscured without dispatching a click.
        locator.click(trial=True,timeout=timeout)
        # Di chuột tới nút theo quỹ đạo nhiều bước, nhắm vào một điểm ngẫu nhiên trong nút
        # (giống người thật di chuột), rồi hover — tránh bị nhận diện bot.
        try:
            box = locator.bounding_box()
            if box:
                tx = box['x'] + box['width'] * random.uniform(.25, .75)
                ty = box['y'] + box['height'] * random.uniform(.25, .75)
                self.page.mouse.move(tx, ty, steps=random.randint(8, 22))
        except Exception:
            pass
        locator.hover(timeout=timeout)
        self.wait(random.uniform(.15, .45))
        self.guard()
        if before_click is not None and not before_click():return False
        locator.click(timeout=timeout, delay=random.uniform(50, 140))
        self.pause()
        return True

    def navigate(self, url, pause=True):
        """Retry only opening a page, before sending; never retry a submission."""
        for attempt in range(3):
            check_cancel(self.cancel)
            try:
                response = self.page.goto(url, wait_until='domcontentloaded', timeout=45000)
                if response is not None and response.status in (429, 403):
                    self.guard()
                    raise RuntimeError(f'Dola trả HTTP {response.status}. Hãy kiểm tra quyền truy cập/giới hạn trong trình duyệt; tool không tự tải lại liên tục.')
                if response is not None and response.status >= 500:
                    if attempt == 2: raise RuntimeError('Dola đang lỗi máy chủ. Chưa gửi yêu cầu; hãy thử lại sau.')
                    self.report(f'Dola lỗi máy chủ; đợi {2**(attempt+1)} giây trước lần tải lại {attempt+1}/2.')
                    self.wait(2**(attempt+1)); continue
                self.wait_until(lambda: self.page.evaluate('document.readyState') in ('interactive', 'complete'),
                                30, 'Trang Dola chưa tải xong.')
                if pause:
                    self.pause()
                return
            except BrowserError as exc:
                if self.page.is_closed(): raise RuntimeError('Trình duyệt đã đóng khi đang tải trang.') from None
                # A load timeout can occur after the page has become usable.
                if isinstance(exc, BrowserTimeout) and urlsplit(self.page.url).path == urlsplit(url).path:
                    if self.page.locator(EDITOR).count() and self.page.locator(EDITOR).first.is_visible():
                        if pause:
                            self.pause()
                        return
                transient=isinstance(exc,BrowserTimeout) or any(code in str(exc) for code in (
                    'ERR_CONNECTION_RESET','ERR_CONNECTION_CLOSED','ERR_CONNECTION_TIMED_OUT',
                    'ERR_NETWORK_CHANGED','ERR_INTERNET_DISCONNECTED','ERR_NAME_NOT_RESOLVED','ERR_PROXY_CONNECTION_FAILED'))
                if not transient or attempt==2:
                    raise RuntimeError('Không tải được Dola. Kiểm tra kết nối/proxy; chưa gửi yêu cầu.') from None
                self.report(f'Tải trang bị gián đoạn; đợi {2**(attempt+1)} giây rồi thử lại ({attempt+1}/2).')
                self.wait(2**(attempt+1))

    def submit(self,editor,should_send=None):
        """Wait for a usable composer and submit once, without retrying on timeout."""
        self.guard()
        delay = random.uniform(2, 4)
        self.report(f'Đã nhập xong; chờ {delay:.1f} giây trước khi chuyển đến nút Gửi…')
        self.wait(delay)
        self.guard()
        self.wait_until(lambda:editor.is_visible() and editor.is_editable(),30,'Ô nhập chưa sẵn sàng để gửi.')
        send=self.page.locator('[data-testid="chat_input_send_button"], #flow-end-msg-send, button[class*="send-msg-btn"], button[aria-label="Gửi"], button[aria-label="Send"]')
        buttons=[b for b in send.all() if b.is_visible()]
        if len(buttons)==1:
            return self.click(buttons[0],timeout=30000,before_click=should_send)
        else:
            # Existing Dola UI supports Enter. Don't perform both actions.
            self.guard()
            if should_send is not None and not should_send():return False
            editor.press('Enter',timeout=15000,delay=random.uniform(30, 90));self.pause()
            return True

    def type_prompt(self, editor, text):
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        self.report(f'Đang nhập prompt ({len(text):,} ký tự)…')
        self.click(editor)
        self.wait_until(lambda: editor.is_editable(), 30, 'Ô nhập chưa sẵn sàng.')
        self._paste_prompt(editor, text)
        actual = editor.evaluate("el => 'value' in el ? el.value : el.innerText")
        if ' '.join(str(actual).split()) != ' '.join(text.split()):
            raise RuntimeError('Ô nhập chưa nhận đầy đủ prompt; chưa gửi yêu cầu.')
        self.guard()
        self.wait(.4)

    def _type_segment(self, editor, segment):
        """Gõ tay từng cụm ký tự với nhịp ngẫu nhiên, thỉnh thoảng dừng 'suy nghĩ'."""
        pos = 0
        while pos < len(segment):
            self.guard()
            step = random.randint(2, 5)
            editor.press_sequentially(segment[pos:pos + step], delay=random.uniform(28, 95))
            pos += step
            if random.random() < 0.10:
                self.wait(random.uniform(.2, .6))

    def _paste_rest(self, editor, rest):
        """Dán phần còn lại sau đoạn đã gõ tay: mẩu đầu nối tiếp dòng hiện tại (inline),
        các dòng sau thành đoạn mới — giữ nguyên xuống dòng của prompt."""
        handled = editor.evaluate('''(e,rest)=>{
            const nl=String.fromCharCode(10);
            const parts=rest.split(nl);
            const data=new DataTransfer(); data.setData('text/plain',rest);
            const container=document.createElement('div');
            if(parts.length){ container.append(document.createTextNode(parts[0])); }
            for(let i=1;i<parts.length;i++){
                const p=document.createElement('p');
                p.append(parts[i] ? document.createTextNode(parts[i]) : document.createElement('br'));
                container.append(p);
            }
            data.setData('text/html',container.innerHTML);
            const event=new ClipboardEvent('paste',{clipboardData:data,bubbles:true,cancelable:true});
            e.dispatchEvent(event); return event.defaultPrevented;
        }''', rest)
        if not handled:
            for i, line in enumerate(rest.split('\n')):
                if i:
                    editor.press('Shift+Enter', delay=random.uniform(30, 80))
                if line:
                    editor.press_sequentially(line, delay=random.uniform(2, 8))

    def _paste_prompt(self, editor, text):
        """Dán toàn bộ qua sự kiện paste của chính ô nhập (dự phòng khi gõ tay lỗi)."""
        editor.press('ControlOrMeta+A', delay=random.uniform(30, 80))
        editor.press('Backspace', delay=random.uniform(30, 80))
        handled = editor.evaluate('''(e,text)=>{
            const data=new DataTransfer(); data.setData('text/plain',text);
            const container=document.createElement('div');
            text.split(String.fromCharCode(10)).forEach(line=>{
                const p=document.createElement('p');
                p.append(line ? document.createTextNode(line) : document.createElement('br'));
                container.append(p);
            });
            data.setData('text/html',container.innerHTML);
            const event=new ClipboardEvent('paste',{clipboardData:data,bubbles:true,cancelable:true});
            e.dispatchEvent(event); return event.defaultPrevented;
        }''', text)
        if not handled:
            editor.fill(text, timeout=15000)

    def _reload_composer(self, page):
        """F5: tải lại hội thoại hiện tại (hoặc trang chat) rồi chờ ô nhập sẵn sàng."""
        try:
            self.navigate(conversation_url(page) or CHAT_URL)
        except Exception:
            self.navigate(CHAT_URL)
        self.wait_until(lambda: page.locator(EDITOR).first.is_visible(), 30,
                        'Ô nhập chưa sẵn sàng sau khi tải lại.')

    def _share_count(self, page):
        """Số nút Share đang có = số câu trả lời bình thường đã hoàn tất."""
        try:
            return int(page.locator(SHARE_SELECTOR).count())
        except Exception:
            return 0

    def _failed_count(self, page):
        """Số icon 'gửi tin thất bại' của Dola (message_box_failed_icon)."""
        try:
            return int(page.locator('[data-testid="message_box_failed_icon"]').count())
        except Exception:
            return 0

    def send_greeting(self, page):
        """Chào TRƯỚC khi chạy. Phải bắt được câu trả lời CÓ NÚT SHARE (giống trả lời bình thường).
        Nếu Dola hiện ICON LỖI gửi tin, hoặc quá 40 giây không có trả lời-có-share, thì phiên lỗi:
        F5, chờ load, rồi chào lại bằng câu KHÁC. Chỉ chạy tiếp khi đã bắt được trả lời."""
        last = None
        for attempt in range(1, GREETING_ATTEMPTS + 1):
            greeting = random.choice([g for g in GREETINGS if g != last] or list(GREETINGS))
            last = greeting
            editor = page.locator(EDITOR).first
            self.report(f'Lời chào {attempt}/{GREETING_ATTEMPTS}: "{greeting}"')
            base_shares = self._share_count(page)
            base_failed = self._failed_count(page)
            try:
                self.type_prompt(editor, greeting)
                self.submit(editor)
            except Cancelled:
                raise
            except Exception as exc:
                self.report(f'Lỗi khi gửi lời chào ({exc}); F5 chờ load rồi chào lại.')
                self._reload_composer(page)
                continue
            # Chờ câu trả lời CÓ NÚT SHARE, tối đa 40s; icon lỗi -> hỏng ngay.
            start = time.monotonic()
            replied = failed = False
            while time.monotonic() - start <= 40:
                self.guard()
                if self._failed_count(page) > base_failed:
                    failed = True
                    break
                if self._share_count(page) > base_shares:
                    replied = True
                    break
                self.wait(random.uniform(.5, 1.2))
            if replied:
                self.report('Dola đã trả lời lời chào (có nút Share); bắt đầu chạy.')
                self.wait(random.uniform(.6, 1.6))
                return
            reason = 'Dola báo lỗi gửi tin (icon lỗi)' if failed else 'quá 40s chưa có trả lời có nút Share'
            self.report(f'{reason}; phiên lỗi → F5, chờ load rồi chào lại câu khác.')
            self._reload_composer(page)
        raise RuntimeError(f'Đã chào {GREETING_ATTEMPTS} lần nhưng Dola không trả lời được (icon lỗi / không có nút Share); phiên lỗi, dừng task.')

    def wait_until(self, predicate, timeout, message):
        start, paused = time.monotonic(), self.paused
        while True:
            self.guard()
            try:
                if predicate(): return
            except Exception as exc:
                if 'execution context was destroyed' not in str(exc).lower() and 'navigation' not in str(exc).lower():
                    raise
            if time.monotonic()-start-(self.paused-paused) > timeout:
                raise TimeoutError(message)
            self.wait(.25)


def launch_context(playwright, settings, account, visible=None, *, video_session=False, cancel=None, report=None, resolve_proxy=True):
    settings = account_settings(settings, account)
    if settings.get('use_hidemium'):
        def hidemium_report(message):
            if report:
                report(str(message).replace('GPM', 'Hidemium'))
        if resolve_proxy:
            account = gpm_proxy_account(settings, account, cancel, hidemium_report, unique=bool(video_session))
        hidden = not bool(settings.get('show_browser', True)) and bool(video_session)
        return connect_hidemium(playwright, settings, account, video_session=video_session,
                                cancel=cancel, report=hidemium_report, hidden=hidden)
    if settings.get('use_gpm'):
        # Resolve proxy upstream THẬT rồi gắn vào profile GPM (GPM nối thẳng, không qua bridge).
        # resolve_proxy=False khi caller (đăng nhập) đã resolve rồi → tránh xoay IP 2 lần (dính cooldown).
        # Lúc chạy video (video_session) mới "1 key/luồng"; check/mở thì chia sẻ key.
        if resolve_proxy:
            account = gpm_proxy_account(settings, account, cancel, report, unique=bool(video_session))
        # Chạy ẩn CHỈ khi chạy video (không ẩn lúc đăng nhập/mở tay để còn thao tác).
        hidden = bool(settings.get('gpm_hidden')) and bool(video_session)
        return connect_gpm(playwright,settings,account,cancel=cancel,report=report,hidden=hidden)
    from seedance_browser import proxy_account
    runtime, lease = proxy_account(settings, account, cancel, report, unique=bool(video_session))
    if isinstance(runtime, dict) and runtime.get('proxy'):
        account['proxy'] = dict(runtime['proxy'])
    try:
        return _launch_context(playwright, settings, runtime, visible, video_session, report, lease, cancel)
    except Exception:
        if lease is not None:
            lease.close()
        raise


def _launch_context(playwright, settings, account, visible, video_session, report, lease, cancel=None):
    # Ưu tiên Chromium đi kèm 3 BIG (chống phát hiện tốt nhất). Nếu máy chưa cài
    # 3 BIG Studio thì tự dùng Chrome/Chromium đã chọn trong Cài đặt để vẫn chạy được.
    executable = ''
    if video_session or settings.get('use_3big_browser') or settings.get('load_extension', True):
        executable = find_3big_browser()
    if not executable:
        executable = account.get('browser_executable') or find_browser(settings.get('browser', ''))
    if not executable:
        raise ValueError('Không tìm thấy Chromium/Chrome. Kiểm tra thư mục browser hoặc chọn chrome.exe trong Cài đặt.')
    profile = Path(account.get('profile', ''))
    if not account.get('profile'):
        raise ValueError('Tài khoản chưa có profile. Mở Quản lý tài khoản để liên kết hoặc tạo profile.')
    profile.mkdir(parents=True, exist_ok=True)
    with_extension = settings.get('load_extension', True)
    options = extension_launch_options(True, with_extension)
    options['args'].extend(['--no-first-run', '--no-default-browser-check'])
    if settings.get('_parallel_tabs'):
        options['args'].append('--remote-debugging-port=0')
    proxy = browser_proxy(account)
    if proxy:
        options['proxy'] = proxy
        if urlsplit(proxy['server']).scheme in ('http', 'https'):
            # Authenticated HTTP proxy can stall Google's HTTP/2 transport.
            # Use HTTP/1.1 over the same proxy, without bypassing TLS or proxy auth.
            options['args'].append('--disable-quic')
            if proxy.get('username') or proxy.get('password'):
                options['args'].append('--disable-http2')
    show = settings.get('show_browser', True) if visible is None else visible
    # "Hide Chrome while running": keep a real window, parked off-screen, so a
    # verification page can be brought on-screen for the user to handle by hand.
    offscreen = visible is None and not show
    if offscreen:
        options['args'].append(OFFSCREEN_ARG)
    try:
        if report and settings.get('use_3big_browser'):
            report('Đang dùng Patchright + Chrome for Testing của Seedance Studio • profile riêng của tài khoản.')
        context = playwright.chromium.launch_persistent_context(
            str(profile.resolve()), executable_path=executable, chromium_sandbox=True,
            headless=not show and not offscreen,
            locale='vi-VN', viewport={'width': 1280, 'height': 900}, accept_downloads=True,
            timeout=45000, **options)
        # CDP clients do not inherit this launch metadata. Keep it in memory
        # for the downloader; never persist proxy credentials in task settings.
        context._seedance_proxy = dict(proxy or {})
    except Exception:
        raise RuntimeError('Không mở được profile trình duyệt. Đóng cửa sổ đang dùng cùng tài khoản trong Chrome rồi thử lại; kiểm tra đường dẫn Chromium trong Cài đặt.') from None
    if with_extension:
        try:
            verify_studio_extension(context, cancel, report)
        except Exception:
            context.close()
            raise
    elif video_session and report:
        report('Chạy không nạp extension Seedance Studio Pro (đã tắt trong Cài đặt).')
    if lease is None:
        return context
    return BoundContext(context, lease)


def is_login_required(page):
    return any(button.is_visible() for button in page.get_by_role('button', name=re.compile(r'^(Đăng nhập|Log in|Sign in)$', re.I)).all())


def find_login_browser():
    """Use the installed, supported browser for user-operated sign-in."""
    candidates = [Path(os.environ.get('PROGRAMFILES', 'C:/Program Files')) / 'Google/Chrome/Application/chrome.exe',
                  Path(os.environ.get('LOCALAPPDATA', '')) / 'Google/Chrome/Application/chrome.exe',
                  Path(os.environ.get('PROGRAMFILES(X86)', 'C:/Program Files (x86)')) / 'Google/Chrome/Application/chrome.exe',
                  Path(os.environ.get('PROGRAMFILES(X86)', 'C:/Program Files (x86)')) / 'Microsoft/Edge/Application/msedge.exe']
    ms_pw = Path(os.environ.get('LOCALAPPDATA', '')) / 'ms-playwright'
    if ms_pw.is_dir():
        candidates.extend(sorted(ms_pw.glob('chromium-*/chrome-win*/chrome.exe'), reverse=True))
    return next((str(p.resolve()) for p in candidates if p.is_file()), '')


@contextmanager
def native_login_context(playwright, account, cancel, report, target=CHAT_URL, *, settings=None):
    """Launch installed Chrome, then observe Dola in that same window via CDP."""
    executable = account.get('browser_executable') or find_login_browser()
    if not executable:
        raise RuntimeError('Cần cài Chrome hoặc Edge để đăng nhập.')
    profile = Path(account['profile']).resolve()
    port_file = profile / 'DevToolsActivePort'
    # Caller holds ProfileLease. A previous endpoint must never select another process.
    port_file.unlink(missing_ok=True)
    runtime, lease = open_runtime_proxy(settings, account, cancel, report) if settings is not None else (account, None)
    try:
        with native_proxy(browser_proxy(runtime), report) as proxy_url:
            args = [executable, f'--user-data-dir={profile}', '--no-first-run',
                    '--no-default-browser-check', '--disable-extensions', '--disable-background-mode',
                    '--remote-debugging-address=127.0.0.1', '--remote-debugging-port=0']
            if proxy_url:
                args.extend(['--proxy-server=' + proxy_url, '--disable-quic'])
                if runtime and isinstance(runtime.get('proxy'), dict) and (runtime['proxy'].get('username') or runtime['proxy'].get('password')):
                    args.append('--disable-http2')
            process = subprocess.Popen(args + ['--new-window', target],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if lease is not None: lease.start()
            browser = None
            try:
                deadline = time.monotonic() + 45
                while True:
                    check_cancel(cancel)
                    if process.poll() is not None:
                        raise Cancelled('Cửa sổ đăng nhập đã đóng.')
                    try:
                        port = int(port_file.read_text(encoding='utf-8').splitlines()[0])
                        if not 0 < port < 65536:
                            raise ValueError()
                        break
                    except (OSError, ValueError, IndexError):
                        if time.monotonic() >= deadline:
                            raise RuntimeError('Chrome chưa mở kết nối theo dõi Dola; hãy đóng cửa sổ cũ rồi thử lại.') from None
                        cancel.wait(.2)
                browser = playwright.chromium.connect_over_cdp(f'http://127.0.0.1:{port}', timeout=15000)
                if not browser.contexts:
                    raise RuntimeError('Không nhận được profile Chrome vừa mở.')
                report('Đăng nhập trong Chrome; tool tự nhận tên khi bạn trở lại Dola, không cần đóng cửa sổ. Tool không nhập hay đọc thông tin đăng nhập trên Google.')
                yield browser.contexts[0]
            finally:
                if browser:
                    try:
                        browser.new_browser_cdp_session().send('Browser.close')
                    except Exception:
                        pass
                    try:
                        browser.close()
                    except Exception:
                        pass
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=10)
    finally:
        if lease is not None:
            lease.close()


def manual_account_browser(settings, account, cancel, report, target):
    if settings.get('use_hidemium'):
        account = gpm_proxy_account(settings, account, cancel,
                                    lambda message: report(str(message).replace('GPM', 'Hidemium')), unique=False)
        with ProfileLease('hidemium-' + str(account.get('hidemium_profile_id') or account['id']), settings['lock_root']):
            check_cancel(cancel)
            return manual_hidemium(settings, account, cancel, report, target)
    if settings.get('use_gpm'):
        account = gpm_proxy_account(settings, account, cancel, report, unique=False)  # mở tay: chia sẻ key
        with ProfileLease(account['profile'],settings['lock_root']):
            check_cancel(cancel)
            return manual_gpm(settings,account,cancel,report,target)
    executable = find_3big_browser() if settings.get('use_3big_browser') else (account.get('browser_executable') or find_login_browser())
    if not executable:
        raise RuntimeError('Không tìm thấy trình duyệt đăng nhập. Kiểm tra bộ cài 3 BIG hoặc Chrome/Edge.')
    if not account.get('profile'):
        raise ValueError('Tài khoản chưa có thư mục profile.')
    runtime, lease = open_runtime_proxy(settings, account, cancel, report)
    try:
        return _manual_account_browser(settings, runtime, cancel, report, target, executable, lease)
    finally:
        if lease is not None:
            lease.close()


def _manual_account_browser(settings, account, cancel, report, target, executable, lease=None):
    with ProfileLease(account['profile'], settings['lock_root']), ExitStack() as resources:
        profile = Path(account['profile']).resolve()
        profile.mkdir(parents=True, exist_ok=True)
        check_cancel(cancel)
        args = [executable, f'--user-data-dir={profile}', '--disable-extensions', '--disable-background-mode']
        proxy = browser_proxy(account)
        proxy_url = resources.enter_context(native_proxy(proxy, report))
        if proxy_url:
            args.append('--proxy-server=' + proxy_url)
            if urlsplit(proxy['server']).scheme in ('http', 'https'):
                args.append('--disable-quic')
                if proxy.get('username') or proxy.get('password'):
                    args.append('--disable-http2')
        process = subprocess.Popen(args + ['--new-window', target],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if lease is not None: lease.start()
        report(f"Đã mở trình duyệt cho {account['name']}. Bạn tự thao tác và đóng cửa sổ khi xong.")
        started = time.monotonic()
        while process.poll() is None:
            if cancel.wait(.25):
                process.terminate()
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=5)
                report('Đã đóng phiên Chrome của tài khoản.')
                return False
        if process.returncode or time.monotonic() - started < 2:
            raise RuntimeError('Trình duyệt đã thoát hoặc chuyển sang cửa sổ profile có sẵn. Đóng cửa sổ dùng cùng profile rồi mở lại. Nếu vừa tự đóng, bạn có thể bấm Kiểm tra.')
        report('Đã đóng cửa sổ tài khoản.')
        return True


def account_browser(settings, account, cancel, report, login=False):
    settings = prepare_account_settings(settings, account, cancel, report)
    target = settings.get('open_url', CHAT_URL)
    if target != CHAT_URL and not re.fullmatch(r'https://www\.dola\.com/chat/\d+', target):
        raise ValueError('Địa chỉ hội thoại không hợp lệ.')
    if login and (settings.get('use_hidemium') or settings.get('use_gpm') or not settings.get('use_3big_browser')):
        return manual_account_browser(settings, account, cancel, report, target)
    with ProfileLease(account['profile'], settings['lock_root']), browser_runtime(settings) as p:
        context = launch_context(p, settings, account, visible=True if login else settings.get('show_browser', True),cancel=cancel,report=report)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            if login:
                if account.get('cookie_kind') == 'dola' and account.get('cookie_file'):
                    snapshot = session_snapshot_path(settings, account)
                    source = {'cookie_file':str(snapshot)} if snapshot.exists() else account
                    context.add_cookies(load_cookies(source))
                    report('Đã nạp cookie Dola đã lưu vào profile.')
                elif account.get('cookie_file') and Path(account['cookie_file']).exists():
                    try:
                        context.add_cookies(load_cookies(account))
                        report('Đã nạp cookie đã lưu vào profile.')
                    except Exception:
                        pass
                try:
                    page.goto(target, wait_until='domcontentloaded', timeout=45000)
                except BrowserError:
                    if page.is_closed():
                        return True
                    report('Trang chưa tải xong; cửa sổ vẫn mở để bạn thao tác.')
                report(f"Đã mở {account.get('name', 'profile')}. Bạn dùng trình duyệt và tự đóng khi xong; tool sẽ tự lưu phiên nếu đăng nhập thành công.")
                while True:
                    check_cancel(cancel)
                    pages = [tab for tab in context.pages if not tab.is_closed()]
                    if not pages:
                        break
                    for tab in pages:
                        if urlsplit(tab.url).hostname in ('dola.com', 'www.dola.com'):
                            name = dola_identity(tab)
                            if name or dola_account_present(tab):
                                if name: account['name'] = name
                                save_live_session(context, settings, account)
                                account['_login_verified'] = True
                    try:
                        pages[0].wait_for_timeout(250)
                    except BrowserError:
                        break
                report('Đã đóng cửa sổ tài khoản.')
                return True
            steps = BrowserSteps(page, settings, cancel, report)
            steps.navigate(target, pause=False)
            steps.wait_until(lambda: is_login_required(page) or bool(dola_identity(page)),
                             45, 'Chưa xác định được phiên Dola; kiểm tra lại mạng/proxy.')
            if is_login_required(page):
                if account.get('cookie_kind') == 'dola' and not session_snapshot_path(settings, account).exists():
                    name = authenticate_cookie_context(context, settings, account, cancel, report, page=page)
                elif not session_snapshot_path(settings, account).exists():
                    if account.get('cookie_file') and Path(account['cookie_file']).exists():
                        name = authenticate_cookie_context(context, settings, account, cancel, report, page=page)
                    else:
                        return False
                else:
                    try:
                        name = restore_cookie_session(context, settings, account, cancel, report, page=page)
                    except Cancelled:
                        raise
                    except Exception:
                        if is_login_required(page):
                            return False
                        raise
                report('[Tài khoản] ' + name)
                page = context.pages[0] if context.pages else context.new_page()
                steps = BrowserSteps(page, settings, cancel, report)
                steps.navigate(target, pause=False)
                if is_login_required(page):
                    return False
            page.locator(EDITOR).first.wait_for(state='visible', timeout=30000)
            name=dola_identity(page)
            if is_login_required(page): return False
            if not name:
                raise RuntimeError('Chưa đọc được tài khoản; chưa kết luận profile die.')
            save_live_session(context,settings,account)
            account['name']=name
            return True
        finally:
            try: context.close()
            except Exception: pass


def _clean_account_label(txt):
    if not txt:
        return ''
    cleaned = ' '.join(str(txt).split())
    if not (1 <= len(cleaned) <= 120):
        return ''
    lower = cleaned.lower()
    if lower in ('đăng nhập', 'log in', 'sign in', 'nâng cấp', 'upgrade', 'pro', 'work', 'fast',
                 'free', 'miễn phí', 'free plan', 'dola', 'dola ai', 'cài đặt', 'settings',
                 'tạo video', 'create video', 'chat', 'trò chuyện'):
        return ''
    if mode_kind_from_name(cleaned):
        return ''
    return cleaned


def dola_identity(page):
    if urlsplit(page.url).hostname not in ('dola.com', 'www.dola.com'): return ''
    login = page.get_by_role('button',name=re.compile(r'^(Đăng nhập|Log in|Sign in)$',re.I))
    try:
        if any(x.is_visible() for x in login.all()): return ''
    except Exception:
        pass

    # 1. Account dropdown in sidebar / header with span.text-dbx-text-primary
    try:
        labels = page.locator(':is(nav, aside, [data-slot="sidebar"], [data-sidebar="sidebar"]) '
                              ':is(button, [role="button"])[data-slot="dropdown-menu-trigger"][aria-haspopup="menu"] '
                              'span.text-dbx-text-primary')
        for label in labels.all():
            if label.is_visible():
                name = _clean_account_label(label.inner_text())
                if name: return name
    except Exception:
        pass

    try:
        candidates = page.locator('[data-slot="dropdown-menu-trigger"][aria-haspopup="menu"]:has(img) span.text-dbx-text-primary')
        for label in candidates.all():
            if label.is_visible():
                name = _clean_account_label(label.inner_text())
                if name: return name
    except Exception:
        pass

    # 2. General user button with avatar in sidebar/footer/header
    try:
        user_triggers = page.locator(
            ':is(nav, aside, [data-slot="sidebar"], [data-sidebar="sidebar"], [data-sidebar="footer"], header) '
            ':is(button, [role="button"]):is([data-slot="dropdown-menu-trigger"], [aria-haspopup="menu"], [data-sidebar="menu-button"], :has(img), :has([class*="avatar" i]))'
        )
        for btn in user_triggers.all():
            if not btn.is_visible():
                continue
            for item in btn.locator('span, div, p').all():
                if item.is_visible():
                    name = _clean_account_label(item.inner_text())
                    if name: return name
            for img in btn.locator('img').all():
                alt = (img.get_attribute('alt') or img.get_attribute('title') or '').strip()
                name = _clean_account_label(alt)
                if name: return name
    except Exception:
        pass

    # 3. LocalStorage user info
    try:
        js_name = page.evaluate("""() => {
            try {
                for (let k of ['user_info', 'user', 'profile', 'flow_user_info']) {
                    let v = localStorage.getItem(k);
                    if (v) {
                        let obj = JSON.parse(v);
                        let n = obj.name || obj.nickname || obj.nick_name || obj.username || obj.display_name;
                        if (n && typeof n === 'string') return n.trim();
                    }
                }
            } catch(e) {}
            return '';
        }""")
        name = _clean_account_label(js_name)
        if name: return name
    except Exception:
        pass

    return ''


def dola_account_present(page):
    """A named account or its sidebar avatar proves that the Dola session is live."""
    if dola_identity(page):
        return True
    if urlsplit(page.url).hostname not in ('dola.com', 'www.dola.com') or is_login_required(page):
        return False
    selectors = (
        ':is(nav, aside, [data-slot="sidebar"], [data-sidebar="sidebar"]) '
        ':is(button, [role="button"])[data-slot="dropdown-menu-trigger"][aria-haspopup="menu"]:has(img), '
        ':is(nav, aside, [data-slot="sidebar"], [data-sidebar="sidebar"]) '
        ':is(button, [role="button"])[aria-haspopup="menu"]:has([class*="avatar" i])'
    )
    return any(item.is_visible() for item in page.locator(selectors).all())


def dola_shell_loaded(page):
    """The chat shell has rendered far enough that a missing account marker is meaningful."""
    try:
        return (urlsplit(page.url).hostname in ('dola.com', 'www.dola.com')
                and page.evaluate('document.readyState') in ('interactive', 'complete')
                and page.locator(EDITOR).first.is_visible())
    except BrowserError:
        return False


def _find_facebook_button(page):
    selectors = (
        '[data-testid="login_third_facebook"]',
        'button:has-text("Facebook")',
        '[role="button"]:has-text("Facebook")',
        'div[role="button"]:has-text("Facebook")',
        '[aria-label*="Facebook" i]',
        '[title*="Facebook" i]',
        'button:has(svg[class*="facebook" i])',
        'button:has(img[src*="facebook" i])',
        'button:has(svg path[d*="facebook" i])',
        'button:has(svg[data-testid*="facebook" i])',
    )
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if loc.count() and loc.first.is_visible():
                return loc.first
        except Exception:
            continue
    return None


def _handle_facebook_oauth_tab(tab, report=None):
    try:
        if 'facebook.com' not in tab.url:
            return False

        # 1. Phát hiện nếu Facebook báo lỗi phiên ("Sorry, something went wrong" / "Rất tiếc, đã xảy ra lỗi")
        try:
            body_text = tab.inner_text('body', timeout=1000).lower()
            if 'sorry, something went wrong' in body_text or 'đã xảy ra lỗi' in body_text:
                if report:
                    report('Facebook báo lỗi phiên (Sorry, something went wrong). Cookie có thể đã hết hạn hoặc không hợp lệ.')
                return False
        except Exception:
            pass

        # 2. Nếu Facebook đang hiện form đăng nhập email/mật khẩu -> cookie không còn dùng được
        try:
            pwd = tab.locator('input[type="password"], input[name="pass"]')
            if pwd.count() > 0 and any(pwd.nth(i).is_visible() for i in range(min(pwd.count(), 2))):
                if report:
                    report('Facebook yêu cầu nhập mật khẩu (cookie Facebook chưa đăng nhập được). Bạn có thể đăng nhập tay trên cửa sổ Facebook.')
                return False
        except Exception:
            pass

        selectors = (
            'button:has-text("Tiếp tục dưới tên")',
            '[role="button"]:has-text("Tiếp tục dưới tên")',
            'div[role="button"]:has-text("Tiếp tục dưới tên")',
            'button:has-text("Tiếp tục với tư cách")',
            '[role="button"]:has-text("Tiếp tục với tư cách")',
            'div[role="button"]:has-text("Tiếp tục với tư cách")',
            'button:has-text("Continue as")',
            '[role="button"]:has-text("Continue as")',
            'div[role="button"]:has-text("Continue as")',
            'button:has-text("Đăng nhập bằng")',
            '[role="button"]:has-text("Đăng nhập bằng")',
            'button:has-text("Tiếp tục")',
            '[role="button"]:has-text("Tiếp tục")',
            'div[role="button"]:has-text("Tiếp tục")',
            'button:has-text("Continue")',
            '[role="button"]:has-text("Continue")',
            'div[role="button"]:has-text("Continue")',
            'button:has-text("Đồng ý")',
            '[role="button"]:has-text("Đồng ý")',
            'button:has-text("Xác nhận")',
            '[role="button"]:has-text("Xác nhận")',
            'button:has-text("Phê duyệt")',
            '[role="button"]:has-text("Phê duyệt")',
            'button[name="__CONFIRM__"]',
            'button[value="1"]',
            '[aria-label*="Tiếp tục" i]',
            '[aria-label*="Continue" i]',
        )
        for sel in selectors:
            loc = tab.locator(sel)
            if loc.count() and loc.first.is_visible():
                if report:
                    report('Đang tự động xác nhận quyền đăng nhập Facebook…')
                try:
                    loc.first.click(timeout=2000)
                except Exception:
                    loc.first.click(force=True)
                return True
    except BrowserError:
        pass
    except Exception:
        pass
    return False


def _handle_age_confirmation(tab, report=None):
    """Tự động phát hiện và xử lý popup / hộp thoại xác nhận tuổi (18+) trên Dola hoặc popup liên quan."""
    try:
        if tab.is_closed():
            return False

        age_patterns = (
            'xác nhận tuổi', 'tuổi của bạn', '18 tuổi', 'độ tuổi', 'đủ 18', 'trở lên',
            'confirm your age', 'age verification', '18 or older', 'at least 18', 'over 18',
            'để tiếp tục, vui lòng xác nhận', 'sinh', 'birth'
        )

        # 1. Tìm container hộp thoại (dialog, alertdialog, modal, popup overlay)
        dialogs = tab.locator(
            '[role="dialog"], [role="alertdialog"], [data-slot="dialog-content"], '
            '[data-slot="alert-dialog-content"], [aria-modal="true"], [class*="dialog" i], [class*="modal" i]'
        )
        found_dialog = None
        has_dialog = False
        try:
            count = dialogs.count()
            for i in range(count):
                d = dialogs.nth(i)
                if d.is_visible():
                    text = d.inner_text().lower()
                    if any(k in text for k in age_patterns):
                        found_dialog = d
                        has_dialog = True
                        break
        except Exception:
            pass

        page_scope = found_dialog if has_dialog else tab

        # Kiểm tra nội dung có nhắc tới xác nhận tuổi / ngày sinh không
        page_has_age_prompt = False
        try:
            body_text = page_scope.inner_text().lower()
            if any(k in body_text for k in age_patterns):
                page_has_age_prompt = True
        except Exception:
            pass

        if not page_has_age_prompt and not has_dialog:
            return False

        # 2. Xử lý Checkbox nếu có (VD: "Tôi xác nhận đủ 18 tuổi")
        checkboxes = page_scope.locator('input[type="checkbox"], [role="checkbox"], button[data-state][role="checkbox"]')
        try:
            for i in range(checkboxes.count()):
                cb = checkboxes.nth(i)
                if cb.is_visible():
                    checked = False
                    try:
                        checked = cb.is_checked()
                    except Exception:
                        state = cb.get_attribute('data-state') or cb.get_attribute('aria-checked')
                        checked = (state in ('checked', 'true'))
                    if not checked:
                        if report:
                            report('Đang tích chọn xác nhận độ tuổi (18+)…')
                        try:
                            cb.check(timeout=1500)
                        except Exception:
                            cb.click(timeout=1500)
        except Exception:
            pass

        # 3. Xử lý ô nhập năm sinh (Year) / ngày sinh nếu có
        year_inputs = page_scope.locator('input[placeholder*="YYYY" i], input[placeholder*="Năm" i], input[name*="year" i], input[name*="birth" i]')
        try:
            if year_inputs.count() and year_inputs.first.is_visible():
                val = year_inputs.first.input_value()
                if not val:
                    if report:
                        report('Đang điền năm sinh xác nhận độ tuổi…')
                    year_inputs.first.fill('2000')
        except Exception:
            pass

        # 4. Tìm các nút xác nhận độ tuổi cụ thể
        specific_age_buttons = (
            'button:has-text("18 tuổi trở lên")',
            '[role="button"]:has-text("18 tuổi trở lên")',
            'button:has-text("Từ 18 tuổi")',
            '[role="button"]:has-text("Từ 18 tuổi")',
            'button:has-text("Trên 18 tuổi")',
            '[role="button"]:has-text("Trên 18 tuổi")',
            'button:has-text("Tôi đủ 18 tuổi")',
            '[role="button"]:has-text("Tôi đủ 18 tuổi")',
            'button:has-text("Đủ 18 tuổi")',
            '[role="button"]:has-text("Đủ 18 tuổi")',
            'button:has-text("Tôi từ 18 tuổi")',
            '[role="button"]:has-text("Tôi từ 18 tuổi")',
            'button:has-text("18 or older")',
            '[role="button"]:has-text("18 or older")',
            'button:has-text("At least 18")',
            '[role="button"]:has-text("At least 18")',
            'button:has-text("Over 18")',
            '[role="button"]:has-text("Over 18")',
            'button:has-text("I am 18")',
            '[role="button"]:has-text("I am 18")',
            'button:has-text("Yes, I am 18")',
            '[role="button"]:has-text("Yes, I am 18")',
            'button:has-text("18+")',
            '[role="button"]:has-text("18+")',
        )
        for sel in specific_age_buttons:
            try:
                loc = page_scope.locator(sel)
                if loc.count() and loc.first.is_visible():
                    if report:
                        report('Đang tự động bấm xác nhận đủ 18 tuổi…')
                    loc.first.click(timeout=2000)
                    return True
            except Exception:
                continue

        # 5. Các nút xác nhận chung trong hộp thoại xác nhận tuổi (ưu tiên nút "Xác nhận" xanh)
        confirm_buttons = (
            'button:has-text("Xác nhận")',
            '[role="button"]:has-text("Xác nhận")',
            'button:has-text("Xác nhận tuổi")',
            '[role="button"]:has-text("Xác nhận tuổi")',
            'button:has-text("Confirm")',
            '[role="button"]:has-text("Confirm")',
            'button:has-text("Đồng ý")',
            '[role="button"]:has-text("Đồng ý")',
            'button:has-text("Agree")',
            '[role="button"]:has-text("Agree")',
            'button:has-text("Tiếp tục")',
            '[role="button"]:has-text("Tiếp tục")',
            'button:has-text("Continue")',
            '[role="button"]:has-text("Continue")',
            'button:has-text("Chấp nhận")',
            '[role="button"]:has-text("Chấp nhận")',
            'button:has-text("Accept")',
            '[role="button"]:has-text("Accept")',
            'button:has-text("Hoàn tất")',
            '[role="button"]:has-text("Hoàn tất")',
            'button:has-text("Done")',
            '[role="button"]:has-text("Done")',
            'button[type="submit"]',
        )
        for sel in confirm_buttons:
            try:
                loc = page_scope.locator(sel)
                for j in range(loc.count()):
                    btn = loc.nth(j)
                    if btn.is_visible():
                        btn_text = btn.inner_text().strip()
                        # Loại trừ nút hủy hoặc "Không, tôi không phải"
                        if any(neg in btn_text.lower() for neg in ['không', 'hủy', 'cancel', 'no']):
                            continue
                        if report:
                            report(f'Đang tự động bấm "{btn_text}" trên hộp thoại xác nhận tuổi…')
                        btn.click(timeout=2000)
                        return True
            except Exception:
                continue

        return False
    except BrowserError:
        return False
    except Exception:
        return False


def _trigger_facebook_login(context, page, report=None):
    report = report or (lambda text: None)
    try:
        if page.is_closed():
            return False

        # 0. Nếu đang có popup "Xác nhận tuổi của bạn" che khuất màn hình -> bấm "Xác nhận" ngay
        if _handle_age_confirmation(page, report):
            page.wait_for_timeout(500)

        # 1. Đã có nút Facebook hiển thị (modal đăng nhập đã mở sẵn) -> bấm trực tiếp
        fb_btn = _find_facebook_button(page)
        if fb_btn:
            report('Đang tự động mở xác thực Facebook trên Dola…')
            try:
                fb_btn.click(timeout=5000)
                return True
            except Exception as exc:
                try:
                    fb_btn.click(force=True, timeout=3000)
                    return True
                except Exception:
                    report(f'Lỗi khi bấm nút Facebook ({exc})')

        # 2. Kiểm tra modal/overlay có đang mở không. Nếu chưa mở mới bấm nút "Đăng nhập" ở thanh tiêu đề
        dialog_overlay = page.locator(
            '[data-slot="dialog-overlay"], [role="dialog"], [role="alertdialog"], '
            '[data-slot="dialog-content"], [data-slot="alert-dialog-content"]'
        )
        overlay_open = False
        try:
            overlay_open = dialog_overlay.count() > 0 and any(
                dialog_overlay.nth(i).is_visible() for i in range(min(dialog_overlay.count(), 3))
            )
        except Exception:
            overlay_open = False

        if not overlay_open:
            login_btn = page.locator('[data-testid="to_login_button"]')
            if not (login_btn.count() and login_btn.first.is_visible()):
                login_btn = page.get_by_role('button', name=re.compile(r'^(Đăng nhập|Log in|Sign in)$', re.I))
            if login_btn.count() and login_btn.first.is_visible():
                report('Đang mở hộp thoại đăng nhập Dola…')
                try:
                    login_btn.first.click(timeout=3000)
                except Exception:
                    try:
                        login_btn.first.click(force=True, timeout=2000)
                    except Exception:
                        pass

        # 3. Chờ tối đa 8 giây để nút Facebook xuất hiện và bấm
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            if page.is_closed():
                return False
            # Nếu popup tuổi xuất hiện trong lúc chờ, bấm Xác nhận ngay
            if _handle_age_confirmation(page, report):
                page.wait_for_timeout(300)
            fb_btn = _find_facebook_button(page)
            if fb_btn:
                report('Đang tự động mở xác thực Facebook trên Dola…')
                try:
                    fb_btn.click(timeout=5000)
                    return True
                except Exception as exc:
                    try:
                        fb_btn.click(force=True, timeout=3000)
                        return True
                    except Exception:
                        report(f'Lỗi khi bấm nút Facebook ({exc})')
                        return False
            page.wait_for_timeout(250)

        report('Chưa thấy biểu tượng Facebook sau khi mở đăng nhập; bạn hãy bấm biểu tượng Facebook trên Dola.')
        return False
    except Exception as exc:
        report(f'Chưa tự động bấm đăng nhập Facebook ({exc}); bạn hãy bấm biểu tượng Facebook trên Dola.')
        return False


def wait_for_dola_identity(context, cancel, report):
    report('Đang chờ tên tài khoản và ô nhập Dola; sẽ tự lưu rồi đóng cửa sổ khi nhận được. Bấm Dừng đăng nhập để hủy.')
    stable_name, stable_since = '', time.monotonic()
    last_fb_trigger = 0.0
    while True:
        check_cancel(cancel)
        pages = [tab for tab in context.pages if not tab.is_closed()]
        if not pages:
            if stable_name:
                return stable_name
            raise Cancelled('Đã đóng cửa sổ khi chưa nhận được tài khoản Dola.')
        name = ''
        has_fb_tab = any('facebook.com' in tab.url for tab in pages)
        for tab in pages:
            try:
                if 'facebook.com' in tab.url:
                    _handle_facebook_oauth_tab(tab, report)
                else:
                    _handle_age_confirmation(tab, report)
                if not has_fb_tab and urlsplit(tab.url).hostname in ('dola.com', 'www.dola.com'):
                    now = time.monotonic()
                    if now - last_fb_trigger >= 15.0:
                        fb = _find_facebook_button(tab)
                        if fb:
                            last_fb_trigger = now
                            _trigger_facebook_login(context, tab, report)
                name = dola_identity(tab)
                if not name and dola_account_present(tab):
                    name = 'Tài khoản Dola'
                if name:
                    break
            except BrowserError:
                continue
        if name != stable_name:
            stable_name, stable_since = name, time.monotonic()
        if name and time.monotonic() - stable_since >= 1.5:
            return name
        try:
            pages[0].wait_for_timeout(250)
        except BrowserError:
            if stable_name:
                return stable_name
            raise Cancelled('Đã đóng cửa sổ khi chưa nhận được tài khoản Dola.')


def register_dola_account(settings, account, cancel, report, profiles_root, initial_cookies=None):
    """Only commit a newly created profile after a named Dola login is observed."""
    if account.get('browser_backend') == 'hidemium' and not account.get('hidemium_profile_id'):
        from hidemium_browser import create_account_profile
        create_account_profile(settings, account, cancel, report)
    settings = account_settings(settings, account)
    if settings.get('use_hidemium'):
        pending = dict(account, browser_backend='hidemium')
        with ProfileLease('hidemium-' + pending['hidemium_profile_id'], settings['lock_root']), browser_runtime(settings) as runtime:
            context = launch_context(runtime, settings, pending, visible=True, cancel=cancel, report=report)
            try:
                if initial_cookies:
                    context.add_cookies(initial_cookies)
                page = context.pages[0] if context.pages else context.new_page()
                BrowserSteps(page, settings, cancel, report).navigate(CHAT_URL, pause=False)
                if initial_cookies:
                    _trigger_facebook_login(context, page, report)
                name = wait_for_dola_identity(context, cancel, report)
                result = dict(pending, name=name, selected=True, connection='Phiên hợp lệ',
                              session_verified_at=time.time())
                save_live_session(context, settings, result)
                return result
            finally:
                context.close()
    if settings.get('use_gpm'):
        return register_gpm_account(settings, account, cancel, report, initial_cookies=initial_cookies)
    root = Path(profiles_root).resolve()
    profile = Path(account['profile']).resolve()
    if profile.parent != root or not re.fullmatch(r'[0-9a-f]{32}', profile.name):
        raise ValueError('Thư mục đăng nhập tạm không hợp lệ.')
    root.mkdir(parents=True, exist_ok=True)
    # Never delete or repurpose an existing account directory on failure.
    profile.mkdir(exist_ok=False)
    confirmed = False
    try:
        cfg = dict(settings, show_browser=True)
        with ProfileLease(str(profile), settings['lock_root']), browser_runtime(cfg) as p, ExitStack() as resources:
            manual = settings.get('manual_new_login', True) and not settings.get('use_3big_browser')
            if manual:
                executable = find_login_browser()
                if not executable:
                    raise RuntimeError('Cần cài Chrome hoặc Edge để đăng nhập.')
                account = dict(account, browser_executable=executable)
                context = resources.enter_context(native_login_context(p, account, cancel, report, settings=cfg))
            else:
                if settings.get('use_3big_browser'):
                    account = dict(account, browser_executable=find_3big_browser())
                context = launch_context(p, cfg, account, visible=True, cancel=cancel, report=report)
                resources.callback(context.close)
            page = context.pages[0] if context.pages else context.new_page()
            if initial_cookies:
                context.add_cookies(initial_cookies)
                report('Đã nạp cookie Facebook. Đăng nhập Facebook trên Dola; tool tiếp tục chờ nhận tên tài khoản Dola.')
            if not manual or initial_cookies:
                try:
                    BrowserSteps(page, cfg, cancel, report).navigate(CHAT_URL, pause=False)
                except Exception as exc:
                    if page.is_closed():
                        raise Cancelled('Trình duyệt đã đóng khi đang tải trang.') from None
                    report(f'Chưa tải được Dola qua proxy ({exc}). Cửa sổ Chrome vẫn mở để bạn kiểm tra kết nối.')
            if initial_cookies:
                _trigger_facebook_login(context, page, report)
            name = wait_for_dola_identity(context, cancel, report)
            result = dict(account, name=name, selected=True, connection='Phiên hợp lệ',
                          session_verified_at=time.time())
            save_live_session(context, cfg, result)
        confirmed = True
        return result
    finally:
        if not confirmed:
            # This is exclusively the new directory allocated above, never a linked profile.
            if profile.resolve().parent != root:
                raise RuntimeError('Không dọn profile vì đường dẫn đã thay đổi.')
            for attempt in range(30):
                try:
                    if profile.exists(): shutil.rmtree(profile)
                    break
                except OSError:
                    if attempt == 29:
                        raise RuntimeError('Profile chưa được lưu vào danh sách nhưng chưa dọn được thư mục tạm vì trình duyệt còn khóa file.') from None
                    time.sleep(.2)


def register_gpm_account(settings, account, cancel, report, initial_cookies=None):
    """Create a GPM profile with the supplied proxy and save only a named login."""
    check_cancel(cancel)
    # Gắn proxy upstream thật vào profile GPM ngay khi tạo (lúc đăng nhập) — chia sẻ key.
    account = gpm_proxy_account(settings, account, cancel, report, unique=False)
    pending = dict(account, name='Đăng nhập • ' + account['id'][:8])
    client, link, link_path = prepare_profile(settings, pending)
    pending.update(gpm_profile_id=link['id'], gpm_api=client.root,
                   profile=str(Path(settings['lock_root']).parent / 'gpm_profile_locks' / str(link['id'])))
    pending.pop('browser_executable', None)
    confirmed = False
    context = None
    try:
        with ProfileLease(pending['profile'], settings['lock_root']), browser_runtime(settings) as p:
            try:
                check_cancel(cancel)
                # Proxy đã resolve ở trên — KHÔNG resolve lại để tránh xoay IP lần 2 (dính cooldown).
                context = launch_context(p, settings, pending, visible=True, cancel=cancel, report=report, resolve_proxy=False)
                if initial_cookies:
                    context.add_cookies(initial_cookies)
                    report('Đã nạp cookie Facebook. Đăng nhập Facebook trên Dola; tool tiếp tục chờ nhận tên tài khoản Dola.')
                    page = context.pages[0] if context.pages else context.new_page()
                    BrowserSteps(page, settings, cancel, report).navigate(CHAT_URL, pause=False)
                    _trigger_facebook_login(context, page, report)
                name = wait_for_dola_identity(context, cancel, report)
                try:
                    client.update(link['id'], name=name, profile_name=name)
                except GpmError:
                    report('Đã nhận tên Dola; GPM chưa đổi được tên profile. Tool vẫn lưu đúng tên tài khoản và GPM ID.')
                result = dict(pending, name=name, selected=True, connection='Phiên hợp lệ',
                              session_verified_at=time.time())
                save_live_session(context, settings, result)
                confirmed = True
            finally:
                if context is not None:
                    try: context.close()
                    except Exception:
                        if not confirmed: raise
                        report('Đã nhận tài khoản; GPM chưa xác nhận đóng profile. Hãy đóng profile trong GPM trước khi chạy tác vụ.')
        return result
    finally:
        if not confirmed and link.get('owned'):
            try:
                client.delete(link['id'])
                link_path.unlink(missing_ok=True)
                report('Đã xóa profile GPM mới chưa hoàn tất đăng nhập.')
            except (GpmError, OSError):
                report('Chưa dọn được profile GPM mới: ' + str(link['id']) + '. Liên kết được giữ để xử lý trong GPM.')


def authenticate_cookie_context(context, settings, account, cancel, report, page=None):
    check_cancel(cancel)
    cookies = load_cookies(account)
    try:
        context.add_cookies(cookies)
    except Exception:
        raise RuntimeError('Không import được cookie vào profile. Kiểm tra định dạng, miền và hạn dùng cookie.') from None
    report('Đã nạp cookie vào profile; đang mở Dola để xác nhận phiên…')
    page=page if page is not None else (context.pages[0] if context.pages else context.new_page())
    cfg=dict(settings,show_browser=True)
    steps=BrowserSteps(page,cfg,cancel,report)
    steps.navigate(CHAT_URL)
    steps.wait(3)
    def confirmed(name):
        if settings.get('use_gpm'):
            try:rename_profile(settings,account,name)
            except GpmError:report('Đã xác nhận tên trên Dola, nhưng GPM chưa cập nhật được tên profile.')
        return name
    name=dola_identity(page)
    if name or dola_account_present(page): return confirmed(name or account.get('name', 'Tài khoản Dola'))
    facebook=any('facebook.com' in c.get('domain','') for c in cookies)
    dola=account.get('cookie_kind')=='dola' or all(is_dola_cookie(c) for c in cookies)
    report('Đã import cookie đã lưu; tải lại Dola và chờ phiên đăng nhập…')
    page.reload(wait_until='domcontentloaded')
    if dola:
        report('Đã nạp cookie Dola; đang chờ trang xác nhận tên tài khoản…')
        # Direct Dola sessions must never fall through to Google/Facebook OAuth.
        try:
            steps.wait_until(lambda: dola_account_present(page), 15,
                             'Chưa nhận được tài khoản sau khi nạp cookie và tải lại Dola.')
        except TimeoutError as exc:
            raise SessionDead(str(exc)) from None
        return confirmed(dola_identity(page) or account.get('name', 'Tài khoản Dola'))
    start = time.monotonic(); clicked_login = False; clicked_google = False; clicked_facebook = False; sidebar_opened = False
    last_fb_attempt = 0.0
    while time.monotonic() - start < 600:
        check_cancel(cancel)
        if page.is_closed(): raise RuntimeError('Đã đóng cửa sổ trước khi xác nhận được tài khoản Dola.')
        steps.guard()
        for candidate in context.pages:
            if candidate.is_closed(): continue
            if 'facebook.com' in candidate.url:
                _handle_facebook_oauth_tab(candidate, report)
            else:
                _handle_age_confirmation(candidate, report)
            name = dola_identity(candidate)
            if name: return confirmed(name)
        if urlsplit(page.url).hostname in ('dola.com', 'www.dola.com'):
            sidebar = page.get_by_role('button', name=re.compile(r'^(Hiển thị thanh bên|Open sidebar|Show sidebar)$', re.I))
            if not sidebar_opened and sidebar.count() and sidebar.first.is_visible():
                steps.click(sidebar.first); sidebar_opened = True
            login = page.get_by_role('button', name=re.compile(r'^(Đăng nhập|Log in|Sign in)$', re.I))
            google = page.get_by_role('button', name=re.compile(r'^(Continue with Google|Sign in with Google|Đăng nhập bằng Google|Tiếp tục với Google)$', re.I))
            if facebook:
                now = time.monotonic()
                has_fb_tab = any('facebook.com' in candidate.url for candidate in context.pages if not candidate.is_closed())
                if not has_fb_tab and (not clicked_facebook or (now - last_fb_attempt >= 15.0)):
                    if _find_facebook_button(page) or not clicked_facebook:
                        last_fb_attempt = now
                        if _trigger_facebook_login(context, page, report):
                            clicked_facebook = True
                steps.wait(.5)
                continue
            if not clicked_google and google.count() and google.first.is_visible():
                steps.click(google.first); clicked_google=True
                report('Đã chọn đăng nhập Google. Nếu Google yêu cầu chọn tài khoản, chấp thuận hoặc xác minh, hãy tự xử lý; tool chờ tên tài khoản xuất hiện trên Dola.')
            elif not clicked_login and login.count() and login.first.is_visible():
                steps.click(login.first); clicked_login=True
        steps.wait(.5)
    raise TimeoutError('Chưa xác nhận được tên tài khoản Dola sau 10 phút. Cookie có thể hết hạn hoặc Google cần đăng nhập/xác minh thủ công.')


def _forget_session_cache(account, cache, lock):
    if cache is not None and lock is not None:
        with lock:
            cache.pop(account.get('id'), None)


class ProfileStopped(RuntimeError):
    """The account is still missing after cookie retries. Stop this profile worker."""


class SessionDead(ProfileStopped):
    """Cookie import and page reload completed, but account recovery failed."""


def restore_cookie_session(context, settings, account, cancel, report, *, attempts=2, page=None):
    """Restore only a previously verified Dola session, never provider cookies."""
    snapshot=session_snapshot_path(settings,account)
    cookie_source = {'cookie_file': str(snapshot)} if snapshot.exists() else account
    cookies=[c for c in load_cookies(cookie_source) if is_dola_cookie(c)]
    if not cookies:
        raise SessionDead('Không có cookie Dola để khôi phục; chuyển profile khác.')
    page=page if page is not None else (context.pages[0] if context.pages else context.new_page())
    steps=BrowserSteps(page,settings,cancel,report)
    last_error = None
    missing_after_reload = False
    for attempt in range(1, attempts + 1):
        check_cancel(cancel)
        report(f'Mất phiên Dola; đang import lại cookie và tải lại trang ({attempt}/{attempts})…')
        reloaded = False
        try:
            context.add_cookies(cookies)
            steps.navigate(CHAT_URL)
            reloaded = True
            steps.wait_until(lambda: dola_account_present(page),15,'Cookie Dola chưa khôi phục được phiên đăng nhập.')
            name=dola_identity(page) or account.get('name', 'Tài khoản Dola')
            save_live_session(context,settings,account)
            report(f'Đã khôi phục phiên Dola ở lần {attempt}/{attempts}.')
            return name
        except Cancelled:
            raise
        except Exception as exc:
            if reloaded and not page.is_closed():
                try:
                    missing_after_reload = missing_after_reload or not dola_account_present(page)
                except Exception:
                    pass
            last_error = 'Cookie Dola hết hạn hoặc trang chưa xác nhận đăng nhập.'
            if attempt < attempts:
                delay = random.uniform(1.0, 2.5)
                report(f'Khôi phục phiên chưa thành công; chờ {delay:.1f} giây rồi thử lại…')
                deadline = time.monotonic() + delay
                while time.monotonic() < deadline:
                    check_cancel(cancel)
                    time.sleep(min(.1, deadline - time.monotonic()))
    error = SessionDead if missing_after_reload else ProfileStopped
    raise error(f'Không khôi phục được cookie sau {attempts} lần: {last_error}') from None


def session_snapshot_path(settings,account):
    key=hashlib.sha256((str(settings.get('gpm_api',''))+'|'+account['id']).encode()).hexdigest()
    return Path(settings['lock_root']).parent/'session_vault'/(key+'.dpapi')


def save_live_session(context,settings,account):
    path=session_snapshot_path(settings,account)
    cookies=[c for c in context.cookies() if is_dola_cookie(c)]
    if cookies: save_cookies(path.parent,path.stem,cookies)


def is_dola_cookie(cookie):
    domain=cookie.get('domain','').lower().lstrip('.')
    return domain=='dola.com' or domain.endswith('.dola.com')


def check_task_session(context,page,steps,settings,account,cancel,report):
    """Verify a newly opened Chrome, or restore an explicitly lost session."""
    report('Đang kiểm tra phiên Dola cho Chrome; các tab dùng chung kết quả kiểm tra…')
    try:
        _handle_age_confirmation(page, report)
        cookie_based = (session_snapshot_path(settings, account).exists()
                        or (account.get('cookie_kind') == 'dola' and account.get('cookie_file')))
        try:
            grace = float(settings.get('cookie_identity_grace', 3))
        except (TypeError, ValueError):
            grace = 3
        grace = max(.05, min(10.0, grace))
        started = time.monotonic()
        try:
            steps.wait_until(
                lambda: (is_login_required(page) or dola_account_present(page)
                         or (cookie_based and dola_shell_loaded(page)
                             and time.monotonic() - started >= grace)),
                12 if cookie_based else 45,
                'Chưa xác định được phiên Dola; chưa tiếp tục tác vụ.')
        except TimeoutError:
            pass
        if is_login_required(page) or not dola_account_present(page):
            if session_snapshot_path(settings,account).exists():
                report('Trang đã tải nhưng không thấy tên/avatar; nạp lại cookie Dola ngay.')
                name=restore_cookie_session(context,settings,account,cancel,report,attempts=2,page=page)
            elif account.get('cookie_kind') == 'dola' and account.get('cookie_file'):
                report('Trang đã tải nhưng không thấy tên/avatar; import lại cookie tài khoản ngay.')
                name=restore_cookie_session(context,settings,account,cancel,report,attempts=2,page=page)
            elif account.get('cookie_file') and Path(account['cookie_file']).exists():
                report('Trang đã tải nhưng không thấy tên/avatar; import lại cookie tài khoản Facebook ngay.')
                name=authenticate_cookie_context(context,settings,account,cancel,report,page=page)
            else:
                raise SessionDead('Profile chưa đăng nhập và không có cookie Dola; chuyển profile khác.')
        else:
            name=dola_identity(page) or account.get('name', 'Tài khoản Dola')
        if not name:
            raise ProfileStopped('Chưa xác nhận được tài khoản Dola; dừng luồng này.')
    except Cancelled:
        raise
    except ProfileStopped:
        raise
    except Exception as exc:
        raise ProfileStopped(str(exc) or 'Không khôi phục được phiên Dola; dừng luồng này.') from None
    save_live_session(context,settings,account)
    report('[Phiên hợp lệ] '+name)
    return name


def ensure_chrome_session(state, context, page, steps, settings, account, cancel, report, *, force=False):
    """State belongs to one Chrome lifetime, never to a task or a saved account."""
    if state.get('valid') and not force:
        cookie_based = (session_snapshot_path(settings, account).exists()
                        or (account.get('cookie_kind') == 'dola' and account.get('cookie_file')))
        if not cookie_based or dola_account_present(page):
            return state.get('name', '')
        if not dola_shell_loaded(page):
            return state.get('name', '')
        report('Tab job đã tải nhưng không thấy tên/avatar; kiểm tra lại phiên cookie ngay.')
    state.clear()
    name=check_task_session(context,page,steps,settings,account,cancel,report)
    state.update(valid=True,name=name)
    return name


def cookie_account_browser(settings, account, cancel, report):
    settings = prepare_account_settings(settings, account, cancel, report)
    check_cancel(cancel)
    with ProfileLease(account['profile'],settings['lock_root']),browser_runtime(settings) as p:
        context=launch_context(p,settings,account,visible=True,cancel=cancel,report=report)
        try:
            name=authenticate_cookie_context(context,settings,account,cancel,report)
            save_live_session(context,settings,account)
            return name
        finally:
            try: context.close()
            except Exception: pass


def build_prompt(task, settings):
    return prompt_text(task, settings)


ASSISTANT_TEXT_SELECTOR = ('[data-message-author-role="assistant"], '
                           '[data-message-role="assistant"]:not(:has([data-message-author-role="assistant"])), '
                           '[data-testid^="conversation-turn"] [data-message-role="assistant"]')


def assistant_response_text(page, allow_main=True):
    """Read the latest assistant card without treating the user's prompt as a refusal."""
    try:
        values=page.locator(ASSISTANT_TEXT_SELECTOR).all_inner_texts()
        values=[str(value).strip() for value in values if str(value).strip()]
        if values:return values[-1]
    except Exception:pass
    if not allow_main:return ''
    try:return page.locator('main').inner_text(timeout=3000)
    except Exception:return ''


def _text_list(values):
    if not isinstance(values, (list, tuple)):
        return []
    return [str(value).strip() for value in values if str(value).strip()]


def _without_prompt(text, prompt):
    text = ' '.join(str(text or '').split())
    if prompt:
        text = text.replace(prompt, ' ')
    return ' '.join(text.split())


def credit_response_text(page, task):
    """Join every visible reply. Dola splits one credit refusal across several blocks."""
    prompt = ' '.join(str(task.get('prompt', '')).split())
    chunks = []
    try:
        chunks.extend(_text_list(page.locator(ASSISTANT_TEXT_SELECTOR).all_inner_texts()))
    except Exception:
        pass
    try:
        chunks.extend(_text_list(page.locator('[data-flow-product-card-root], [role="alert"]').all_inner_texts()))
    except Exception:
        pass
    try:
        nodes = page.get_by_text(re.compile(r'điểm tín dụng|tin dụng|video credits|giới hạn|quay lại vào|limit', re.I)).all()
    except Exception:
        nodes = []
    if isinstance(nodes, (list, tuple)):
        for node in nodes[:8]:
            for target in (node, node.locator('xpath=..')):
                try:
                    value = target.inner_text(timeout=1000)
                except Exception:
                    continue
                if isinstance(value, str) and value.strip():
                    chunks.append(value.strip())
    if not chunks:
        try:
            main = page.locator('main').inner_text(timeout=3000)
        except Exception:
            main = ''
        if isinstance(main, str) and main.strip():
            chunks.append(main)
    return '\n'.join(text for text in (_without_prompt(chunk, prompt) for chunk in chunks) if text)


def is_generation_in_progress(page):
    """Kiểm tra xem Dola có đang trong quá trình tạo video/ảnh hay không."""
    try:
        if page.is_closed():
            return False
        stop_selectors = (
            'button[aria-label*="Stop" i]:visible',
            'button[aria-label*="Dừng" i]:visible',
            '[data-testid*="stop" i]:visible',
            'button:has([class*="stop" i]):visible'
        )
        if page.locator(', '.join(stop_selectors)).count():
            return True

        progress_selectors = (
            '[role="progressbar"]',
            '[aria-busy="true"]',
            '[data-testid*="loading" i]',
            '[data-testid*="progress" i]',
            '[class*="spinner" i]',
            '[class*="animate-spin" i]',
            '[class*="skeleton" i]',
            '[class*="loading" i]',
            '[class*="generating" i]',
            '[data-state="generating" i]',
            '[class*="progress-bar" i]',
        )
        for sel in progress_selectors:
            loc = page.locator(sel)
            if loc.count() and any(loc.nth(i).is_visible() for i in range(min(loc.count(), 3))):
                return True

        main_loc = page.locator('main, [class*="message-list"], [class*="list_items"]')
        if main_loc.count():
            main_text = main_loc.first.inner_text(timeout=1000)
            tail_text = main_text[-500:] if len(main_text) > 500 else main_text
            if tail_text and reply_still_running(tail_text):
                return True
            if tail_text and re.search(r'\b\d{1,2}%\b', tail_text):
                return True
    except Exception:
        pass
    return False


def raise_if_credit_refused(page, task, report, settings=None):
    from account_health import CreditExhausted, WorkLimitReached, RateLimitExceeded, unlock_text
    from dola_limits import insufficient_video_credits, retry_after, usage_limit_reached, is_work_limit_reached, is_message_rate_limited

    # 1. Nếu quy trình tạo đang chạy -> KHÔNG báo lỗi thiếu điểm
    if is_generation_in_progress(page):
        return

    # 2. Nếu đã có video / media / file tải về trên trang -> KHÔNG báo lỗi thiếu điểm
    try:
        from mp4_attachment import scan_files
        if genuine_video_url(page) or scan_files(page).get('count'):
            return
    except Exception:
        pass

    text = credit_response_text(page, task)
    if is_message_rate_limited(text):
        until = retry_after(text)
        when = unlock_text(until) if until else '5 phút sau'
        report(f'Tài khoản bị giới hạn tần suất tin nhắn (Rate Limit) • mở lại {when} • dừng task để chuyển tài khoản khác.')
        raise RateLimitExceeded(until)

    if is_work_limit_reached(text):
        until = retry_after(text)
        when = unlock_text(until) if until else '00:00 hôm sau'
        report(f'Tài khoản đạt giới hạn chế độ Work • mở lại {when} • chuyển tài khoản khác.')
        raise WorkLimitReached(until)
    if usage_limit_reached(text):
        until = retry_after(text)
        when = unlock_text(until) if until else '00:00 hôm sau'
        report(f'Hết lượt dùng Dola (đạt giới hạn) • tài khoản mở lại {when} • dừng task, không gửi lại yêu cầu.')
        raise CreditExhausted(until)
    if insufficient_video_credits(text):
        finished, _ = latest_turn(page)
        if finished and not is_generation_in_progress(page):
            report('Không đủ điểm tín dụng và không có tiến trình tạo video • dừng task, không gửi lại yêu cầu.')
            raise CreditExhausted()


def policy_blocked(text):
    value=str(text or '').casefold()
    markers=(
        'bị chặn bởi chính sách', 'bi chan boi chinh sach', 'vi phạm chính sách',
        'vi pham chinh sach', 'safety policy', 'content policy', 'safety rules',
        'policy restriction', 'policy violation', 'blocked by policy',
        'unsafe content', 'restricted content', 'cannot create this video',
        'không thể tạo video do', 'khong the tao video do',
    )
    return any(marker in value for marker in markers)


REPLY_SETTLE_SECONDS = 25


def needs_video_confirmation(text):
    from dola_limits import insufficient_video_credits, usage_limit_reached
    value=str(text or '').casefold()
    if not value or policy_blocked(value) or insufficient_video_credits(value) or usage_limit_reached(value):return False
    confirmation=(
        'hãy xác nhận', 'hay xac nhan', 'xác nhận để tạo', 'xac nhan de tao',
        'confirm to create', 'please confirm', 'bạn vui lòng xác nhận',
        'ban vui long xac nhan',
    )
    return any(marker in value for marker in confirmation)


def reply_still_running(text):
    """A progress sentence is not a finished reply that lacks a video."""
    value=str(text or '').casefold()
    markers=(
        'đang tạo', 'dang tao', 'đang xử lý', 'dang xu ly', 'đang dựng', 'dang dung',
        'đang kết xuất', 'dang ket xuat', 'đang chuẩn bị', 'dang chuan bi',
        'vui lòng đợi', 'vui long doi', 'đang chờ', 'dang cho',
        'generating', 'creating the video', 'working on your video',
        'trước tiên', 'truoc tien', 'tôi sẽ tạo', 'toi se tao',
    )
    return any(marker in value for marker in markers)


def images_only_reply(receipt, media_present, video_signal, video_seen, reply_text):
    """The latest reply shows images but no video, and is not still generating.
    Dola sometimes returns images instead of a video; treat this like a text-only
    reply so the task re-sends the make-video confirmation."""
    return (not video_seen and not media_present and not video_signal
            and bool(receipt.get('pending')) and not receipt.get('video')
            and not reply_still_running(reply_text))


def confirmation_ready(text, stable_seconds, has_video):
    if has_video or stable_seconds < REPLY_SETTLE_SECONDS or reply_still_running(text):
        return False
    return needs_video_confirmation(text)


def assistant_snapshot(page):
    try:
        nodes=page.locator(ASSISTANT_TEXT_SELECTOR)
        count=int(nodes.count())
    except (TypeError, ValueError, Exception):
        return 0, ''
    if count <= 0:
        return 0, ''
    try:
        text=nodes.nth(count - 1).inner_text(timeout=1000)
    except Exception:
        text=''
    return count, ' '.join(str(text or '').split())


def current_assistant_reply(page, baseline):
    """Only the reply that appeared after this prompt. Older bubbles are not a new result."""
    # reply_baseline is a dict {'assistant': (count, text), 'shares': n} on the
    # seedance path but a plain (count, text) tuple here; accept either shape.
    if isinstance(baseline, dict):
        baseline = baseline.get('assistant', (0, ''))
    count, text = assistant_snapshot(page)
    base_count, base_text = baseline
    fresh = count > base_count or (count == base_count and bool(text) and text != base_text)
    return fresh, text if fresh else ''


ACTION_BAR_SELECTOR = '.message-action-button-main:has([data-testid="message_action_share"]), [data-testid="message_action_share"]'
VIDEO_CONFIRMATION_LIMIT = 3
TEXT_ONLY_GRACE_SECONDS = 5
# Ảnh có thể là poster của video đang tạo; chỉ coi là "chỉ có ảnh" sau khi
# lượt trả về đã ngừng thay đổi trong khoảng này mà vẫn không có video.
IMAGE_ONLY_GRACE_SECONDS = 20
# Đã nghĩ là có video nhưng mãi không lấy được nguồn tải/không phát được:
# sau khoảng này coi lượt đó là ảnh và gửi lại yêu cầu tạo video.
VIDEO_STUCK_GRACE_SECONDS = 60
# Chế độ "không logo bình thường": ưu tiên nguồn sạch, sau khoảng này mà vẫn chưa
# có nguồn không logo thì mới chấp nhận tải MP4 (có thể dính logo) để vẫn ra video.
NO_LOGO_FALLBACK_SECONDS = 45
SHARE_SELECTOR = '[data-testid="message_action_share"]'
EXT_VIDEO_SELECTOR = 'video, [data-video-url], [data-play-url], [src*=".mp4"]'
# Giống hệt findActiveDolaVideo của StudioRelay (3 BIG): nguồn phát của mọi
# <video> (currentSrc, src, <source>) rồi mọi source / [data-video-url].
# Hàm gốc lấy phần tử cuối cùng; ở đây trả cả danh sách để loại video có sẵn
# trước khi gửi prompt.
ACTIVE_VIDEO_JS = r'''() => {
    const isInput = el => !!el.closest('[data-message-author-role="user"], [data-message-role="user"], .justify-end, [contenteditable="true"], nav, aside[class*="sidebar"]:not([class*="artifact"]):not([class*="work"])');
    const videos = [];
    for (const el of document.querySelectorAll('video')) {
        if (isInput(el)) continue;
        const src = el.currentSrc || el.src
            || (el.querySelector('source') ? el.querySelector('source').src : '');
        if (src && src.length > 5) videos.push(src);
    }
    const sources = [];
    for (const el of document.querySelectorAll('source, [data-video-url], [data-play-url]')) {
        if (isInput(el)) continue;
        const src = el.src || el.getAttribute('data-video-url') || el.getAttribute('data-play-url');
        if (src && src.length >= 5) sources.push(src);
    }
    const files = [];
    for (const el of document.querySelectorAll('main a[href], [data-message-author-role="assistant"] a[href], [class*="artifact" i] a[href], [class*="work" i] a[href], [class*="workspace" i] a[href], [class*="canvas" i] a[href], [data-slot="artifact-content"] a[href], [role="complementary"] a[href]')) {
        if (isInput(el)) continue;
        const href = el.href || '';
        let url;
        try { url = new URL(href, location.href); } catch (_) { continue; }
        if (!['http:', 'https:', 'blob:'].includes(url.protocol)) continue;
        const filename = (el.getAttribute('download') || el.textContent || '').trim();
        const title = el.getAttribute('title') || el.getAttribute('aria-label') || '';
        if (/\.mp4(?:$|[?#])/i.test(url.pathname + url.search)
            || /\.mp4(?:$|[\s)])/i.test(filename + ' ' + title)
            || /(?:video\/mp4|video_mp4)/i.test(url.search)
            || (/(^|\.)dola\.com$/i.test(url.hostname) && url.pathname.includes('/video/'))) {
            files.push(href);
        }
    }
    return {videos, sources, files};
}'''
# Lượt chat mới nhất là .v_list_row cuối cùng có nội dung. Cuộn xuống đáy
# trước để virtual list render đúng lượt cuối chứ không phải một lượt cũ.
SCROLL_BOTTOM_JS = r'''() => {
    for (const el of document.querySelectorAll('[class*="v_list_scroller"], [class*="message-list"]')) {
        el.scrollTop = el.scrollHeight;
    }
}'''
LATEST_TURN_JS = r'''(shareSel) => {
    const isFinishedTurn = (el) => {
        if (!el) return false;
        if (el.querySelector(shareSel)) return true;
        if (el.querySelector('[data-testid="message_action_copy"], [data-testid*="copy" i], [class*="message-action"], [data-testid*="message_action"]')) return true;
        if (el.querySelector('video, [data-studio-mp4-result], [data-flow-product-card-root], [class*="block-video-"]')) return true;
        return false;
    };
    const rows = [...document.querySelectorAll('[class*="list_items"] > [class*="v_list_row"]')]
        .filter(row => (row.innerText || '').trim() || row.querySelector('button, video, img'));
    if (rows.length) {
        const last = rows[rows.length - 1];
        return {finished: isFinishedTurn(last), text: last.innerText || ''};
    }
    const shares = document.querySelectorAll(shareSel + ', [data-testid="message_action_copy"], [data-testid*="copy" i]');
    if (!shares.length) return {finished: false, text: ''};
    const share = shares[shares.length - 1];
    const bar = share.closest('.message-action-button-main, [class*="message-action"]') || share;
    return {finished: true, text: (bar.parentElement || bar).innerText || ''};
}'''


def active_video_urls(page):
    """Mọi nguồn video 3 BIG nhìn thấy: <video> trước, rồi source/data-video-url."""
    try:
        found = page.evaluate(ACTIVE_VIDEO_JS)
    except Exception:
        return []
    if not isinstance(found, dict):
        return []
    videos = [u for u in found.get('videos') or [] if isinstance(u, str)]
    sources = [u for u in found.get('sources') or [] if isinstance(u, str)]
    files = [u for u in found.get('files') or [] if isinstance(u,str) and (media_url(u) or u.startswith('blob:'))]
    files += [u for u in scan_files(page).get('urls', []) if media_url(u) or u.startswith('blob:')]
    return list(dict.fromkeys(videos + sources + files))


def find_active_video(page, baseline=()):
    """Video cuối cùng như findActiveDolaVideo, bỏ qua nguồn đã có trước khi gửi."""
    try:
        found = page.evaluate(ACTIVE_VIDEO_JS)
    except Exception:
        return None
    if not isinstance(found, dict):
        return None
    seen = set(baseline or ())
    for group in ('videos', 'sources', 'files'):
        for url in reversed(found.get(group) or []):
            if isinstance(url, str) and url not in seen and (group != 'files' or media_url(url) or url.startswith('blob:')):
                return url
    return None


def latest_turn(page):
    """(đã xong, chữ) của lượt chat cuối. Xong là khi lượt đó có nút Share."""
    try:
        page.evaluate(SCROLL_BOTTOM_JS)
        page.wait_for_timeout(150)
        state = page.evaluate(LATEST_TURN_JS, SHARE_SELECTOR)
    except Exception:
        return False, ''
    if not isinstance(state, dict):
        return False, ''
    return bool(state.get('finished')), ' '.join(str(state.get('text') or '').split())


VIDEO_RESULT_SELECTOR = ('video, xg-player, .xgplayer, [data-xgplayerid], '
                         '[class*="block-video-"], [data-flow-product-card-root], '
                         '[data-testid*="video"], [mediatype="video"], '
                         '[data-video-url], [data-play-url], [src*=".mp4"]')


def page_has_video_card(page):
    """A visible player, cover, or play icon means this reply already has a video."""
    receipt=media_receipt(page)
    if receipt.get('video') or receipt.get('pending'):
        return True
    return _locator_count(page.locator(
        '[class*="block-video-"], .xgplayer, [class*="video-player"], [class*="play-icon"], '
        + EXT_VIDEO_SELECTOR
    )) > 0


def _locator_count(locator):
    try:
        count = locator.count()
    except Exception:
        return 0
    return count if type(count) is int else 0


def action_bar_count(page):
    return _locator_count(page.locator(ACTION_BAR_SELECTOR))


def finished_reply_root(page, baseline_count):
    """The Share button is the final reply. Copy/Like can appear while the video is still being made."""
    bars = page.locator(ACTION_BAR_SELECTOR)
    count = _locator_count(bars)
    if count == 0 or count <= baseline_count:
        return None
    bar = bars.nth(count - 1)
    try:
        handle = bar.evaluate_handle('''el => {
            // Dola's virtual list row contains both the content and action bar.
            // Never climb past it into the entire conversation or previous reply.
            for(let node=el;node && node!==document.body;node=node.parentElement) {
                if(node.parentElement?.classList.contains('list_items')) return node;
            }
            const semantic=el.closest('[data-message-author-role="assistant"], [data-message-role="assistant"], [data-testid^="conversation-turn"], article');
            if(semantic) return semantic;
            return el.parentElement || el;
        }''')
        root = handle.as_element()
        if root is not None:
            return root
    except Exception:
        pass
    return bar


def message_node_text(node):
    if node is None:
        return ''
    try:
        # ElementHandle.inner_text has no timeout argument (Locator does).
        if hasattr(node, 'wait_for_element_state'):
            text=node.evaluate("el => el.innerText || ''")
        else:
            text=node.inner_text(timeout=1000)
        return ' '.join(str(text or '').split())
    except Exception:
        return ''


def message_node_has_video(node):
    """Video inside the finished message, not an older tile elsewhere on the page."""
    if node is None:
        return False
    if hasattr(node, 'wait_for_element_state'):
        try:
            return bool(node.evaluate('(el, sel) => !!el.querySelector(sel)', EXT_VIDEO_SELECTOR))
        except Exception:
            return False
    try:
        if _locator_count(node.locator(VIDEO_RESULT_SELECTOR)):
            return True
    except Exception:
        pass
    # A filename in prose/code is not an attached video.
    try:
        hrefs = node.locator('a[href]').evaluate_all('(es)=>es.map(e=>e.href)')
    except Exception:
        hrefs = []
    return any(dola_video_link(url) for url in hrefs or [])


def assistant_message_has_video(page):
    """Video attached to the latest assistant message, not an older tile on the page."""
    try:
        nodes=page.locator(ASSISTANT_TEXT_SELECTOR)
        count=int(nodes.count())
    except (TypeError, ValueError, Exception):
        return False
    if count <= 0:
        return False
    return message_node_has_video(nodes.nth(count - 1))


def mode_kind_from_name(text):
    """The mode label itself: Pro, Fast, or Nhanh."""
    normalized = ' '.join(re.sub(r'[•·|]', ' ', str(text or '').replace('\u00a0', ' ')).split())
    if not normalized or len(normalized) > 40:
        return ''
    if re.fullmatch(r'(?:Fast|Nhanh)(?:Mới|Moi|New)', normalized, re.I):
        return 'fast'
    badge = r'(?:Mới|Moi|New)'
    core = re.sub(rf'^(?:{badge}\s+)+', '', normalized, flags=re.I)
    core = re.sub(rf'(?:\s+{badge})+$', '', core, flags=re.I)
    if re.fullmatch(r'Pro', core, re.I):
        return 'pro'
    if re.fullmatch(r'(?:Fast|Nhanh)', core, re.I):
        return 'fast'
    return ''


_MODE_SCAN = '''() => {
    const word = /^(?:pro|fast|nhanh)(?:\\s+(?:mới|moi|new))?$/i;
    const hits = [];
    // Stable action attribute; Radix IDs change on every render.
    for (const host of document.querySelectorAll('[data-valid-btn="mode-select-action-btn"]')) {
        const button=host.querySelector('button') || host;
        const rect=button.getBoundingClientRect();
        if(rect.width>0 && rect.height>0 && getComputedStyle(button).visibility!=='hidden') hits.push(button);
    }
    for (const el of document.querySelectorAll('body *')) {
        if (!(el instanceof HTMLElement)) continue;
        const text = (el.innerText || el.getAttribute('aria-label') || '').replace(/\\s+/g, ' ').trim();
        if (!word.test(text) && !/^(?:fast|nhanh)(?:mới|moi|new)$/i.test(text)) continue;
        const style = getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') continue;
        const rect = el.getBoundingClientRect();
        if (rect.width < 2 || rect.height < 2) continue;
        hits.push(el);
    }
    return [...new Set(hits)].filter((el) => el.closest('[data-valid-btn="mode-select-action-btn"]') ? el.matches('button') || !el.querySelector('button') && el.matches('[data-valid-btn]') : !hits.some((other) => other !== el && el.contains(other)));
}'''


def _mode_controls_from_dom(page):
    """Return re-resolving Locators, never handles to a previous React render."""
    if not hasattr(page, 'get_by_text'):
        return None
    found = []
    for kind, word in (('fast', '(?:Fast|Nhanh)'), ('pro', 'Pro')):
        name = re.compile(r'^\s*(?:(?:Mới|Moi|New)\s+)*' + word
                          + r'(?:\s*[•·|]?\s*(?:Mới|Moi|New))*\s*$', re.I)
        # Scope the trigger by its stable attribute, not a generated Radix ID.
        triggers = page.locator(
            '[data-valid-btn="mode-select-action-btn"] button:visible, '
            '[data-valid-btn="mode-select-action-btn"]:not(:has(button)):visible'
        ).filter(has_text=name)
        found.extend((kind, item) for item in triggers.all())
        # Menu options can be plain text divs. Text locators re-find their leaf
        # after a re-render, including during the hover/click readiness wait.
        choices = page.get_by_text(name).filter(visible=True)
        found.extend((kind, item) for item in choices.all())
    return found


def mode_controls(page):
    """Visible Pro/Fast controls, including a plain Pro label that is not a button role."""
    scanned = _mode_controls_from_dom(page)
    if scanned is not None:
        return scanned
    try:
        items = page.locator('button, [role="button"], [role="menuitem"], [role="option"], span, div').all()
    except Exception:
        return []
    if not isinstance(items, list):
        return []
    found = []
    for item in items:
        try:
            if not item.is_visible():
                continue
            text = item.inner_text(timeout=500)
        except Exception:
            continue
        kind = mode_kind_from_name(text)
        if kind:
            found.append((kind, item))
    return found


NHANH_BEFORE_PRO_SECONDS = 1.5


def _fast_trigger(controls):
    """The closed mode button is Nhanh. Fast is only a fallback label."""
    ranked = []
    for kind, item in controls:
        if kind != 'fast':
            continue
        try:
            text = ' '.join(str(item.inner_text(timeout=500) or '').split())
        except Exception:
            text = ''
        ranked.append((0 if re.fullmatch(r'Nhanh(?:\s+(?:Mới|Moi|New))?', text, re.I) else 1, item))
    if not ranked:
        return None
    ranked.sort(key=lambda pair: pair[0])
    return ranked[0][1]


def _click_mode_control(steps, locator):
    """Let the click reach the control when an invisible hit-area covers it."""
    try:
        locator.evaluate('''el => {
            const host = el.closest('button,[role="button"],[role="menuitem"]') || el;
            for (const cover of host.querySelectorAll('[class*="opacity-0"]'))
                cover.style.pointerEvents = 'none';
        }''')
    except Exception:
        pass
    steps.click(locator)


def select_mode(page, generation_mode, report, steps):
    """Pro appears only after Nhanh is opened and given a few seconds."""
    fast = str(generation_mode or 'pro').strip().lower() in ('fast', 'nhanh')
    label = 'Fast/Nhanh.' if fast else 'Pro.'

    def visible(kind):
        return [item for item_kind, item in mode_controls(page) if item_kind == kind]

    def open_then_choose(source, desired):
        steps.wait(NHANH_BEFORE_PRO_SECONDS)
        # Do not carry even the chosen locator/index across the initial delay.
        def current_trigger():
            controls = mode_controls(page)
            return (_fast_trigger(controls) if source == 'fast' else
                    next((item for kind, item in controls if kind == source), None))
        steps.wait_until(lambda: current_trigger() is not None, 10,
                         'Không tìm thấy nút mở menu chế độ sau 10 giây.')
        trigger = current_trigger()
        _click_mode_control(steps, trigger)
        try:
            steps.wait_until(lambda: bool(visible(desired)), 10, 'Không tìm thấy lựa chọn chế độ ' + label)
        except TimeoutError:
            raise RuntimeError('Không tìm thấy lựa chọn chế độ ' + label) from None
        steps.wait(1.5)
        choices=visible(desired)
        if not choices:
            raise RuntimeError('Nút chế độ biến mất trước khi bấm; chưa gửi prompt.')
        _click_mode_control(steps, choices[0])

    if not fast:
        trigger = _fast_trigger(mode_controls(page))
        if trigger is None and not visible('pro'):
            try:
                steps.wait_until(lambda: _fast_trigger(mode_controls(page)) is not None or bool(visible('pro')), 10, 'Không tìm thấy nút Nhanh hoặc Pro sau 10 giây.')
            except TimeoutError:
                raise RuntimeError('Không tìm thấy nút Nhanh hoặc Pro sau 10 giây.') from None
            trigger = _fast_trigger(mode_controls(page))
        if trigger is not None:
            report('Đã thấy Nhanh; chờ 1,5 giây rồi bấm, sau đó chờ Pro tối đa 10 giây…')
            open_then_choose('fast', 'pro')
    else:
        steps.wait_until(lambda: bool(visible('fast') or visible('pro')),10,'Không thấy nút Fast/Nhanh hoặc Pro sau 10 giây.')
        chosen = visible('fast')
        if chosen:
            pass
        elif visible('pro'):
            report('Đang bấm Pro; chờ vài giây để nút Nhanh hiện ra…')
            open_then_choose('pro', 'fast')
        else:
            raise RuntimeError('Không tìm thấy nút chọn chế độ Pro/Fast trên Dola.')
    try:
        steps.wait_until(lambda: bool(visible('fast' if fast else 'pro')), 10, 'Chưa thấy nút chế độ ' + label)
    except TimeoutError:
        raise RuntimeError('Chưa thấy nút chế độ ' + label) from None
    if fast:
        create = page.get_by_role('button', name=re.compile(r'^Tạo video(?:\s+(?:Mới|Moi|New))?$', re.I))
        visible_create = next((item for item in create.all() if item.is_visible()), None)
        if visible_create is not None:
            _click_mode_control(steps, visible_create)
    report('Đã chọn chế độ ' + ('Fast/Nhanh' if fast else 'Pro') + '.')


def skill_file_path():
    """Tìm SKILL.md: cạnh EXE, trong gói (PyInstaller _MEIPASS / Nuitka cạnh module), hoặc mã nguồn."""
    candidates = []
    if getattr(sys, 'frozen', False) or '__compiled__' in globals():
        candidates.append(Path(sys.executable).parent / 'SKILL.md')         # cạnh EXE (người dùng đổi được)
        if getattr(sys, '_MEIPASS', ''):
            candidates.append(Path(sys._MEIPASS) / 'SKILL.md')              # PyInstaller
        candidates.append(Path(__file__).resolve().parent / 'SKILL.md')     # Nuitka: cạnh module đã biên dịch
    else:
        home = Path(__file__).resolve().parent
        candidates += [home / 'release' / 'SKILL.md', home / 'release_mod30' / 'SKILL.md', home / 'SKILL.md']
    return next((path for path in candidates if path.is_file()), None)


def attach_skill_file(page, steps, cancel, report):
    from dola_attachment import upload_document, AttachmentError
    path = skill_file_path()
    if path is None:
        raise AttachmentError('Không tìm thấy SKILL.md cạnh EXE; chưa nhập/gửi prompt.')
    return upload_document(page, path, steps, cancel, report)


def upload_images(page, images, cancel, report, steps):
    from seedance_ui import upload_images as upload_ordered
    upload_ordered(page, images, steps, report)


def conversation_url(page):
    return page.url if re.fullmatch(r'https://www\.dola\.com/chat/\d+', page.url) else ''


def dola_video_link(value):
    try:
        u=urlsplit(value)
        return (u.scheme in ('http','https') and bool(u.hostname) and
                u.hostname.endswith('.dola.com') and '/video/' in u.path and
                not u.username and not u.password and u.port in (None,80,443))
    except (ValueError,TypeError): return False


def is_image_url(value):
    """A generated image on Dola, not a video. Its URL is on the image CDN
    (ibyteimg / rc_gen_image / an image path or extension), never .mp4/video."""
    text=str(value or '').lower()
    if not text or text.startswith('blob:'):
        return False
    try:
        u=urlsplit(text)
    except (ValueError,TypeError):
        return False
    host=u.hostname or ''
    path=u.path
    if 'ibyteimg' in host or 'image-sign' in host:
        return True
    if 'rc_gen_image' in path or '/image/' in path or '/rc_gen_image' in text:
        return True
    return bool(re.search(r'\.(?:jpe?g|png|webp|gif|bmp|heic|avif)(?:$|[?#~])', path)) and '.mp4' not in text


def genuine_video_url(page, baseline=()):
    """The URL of a real video on the page, or None when the reply only has
    images/text. <img> is already ignored by the scan; image URLs (posters,
    data-video-url pointing at a picture) are dropped defensively."""
    url=find_active_video(page, baseline)
    if url and not is_image_url(url):
        return url
    return None


def collect_video_links(page):
    try:
        found=page.evaluate(ACTIVE_VIDEO_JS)
        urls = list(found.get('files', [])) + scan_files(page).get('urls', [])
        return list(dict.fromkeys(u for u in urls if media_url(u) or u.startswith('blob:')))
    except Exception:
        return []


def collect_video_urls(page, network_urls):
    from dola_media import watermark_status
    urls = page.locator('video').evaluate_all('(es)=>es.flatMap(e=>[e.currentSrc,e.src,...Array.from(e.querySelectorAll("source[src]"),s=>s.src)]).filter(Boolean)')
    candidates = [u for u in urls + list(reversed(network_urls)) if u.startswith('https://') or dola_video_link(u)]
    candidates += collect_video_links(page)
    # Filter out URLs explicitly marked as watermarked
    return list(dict.fromkeys(sorted(candidates,key=lambda u: watermark_status(u)=='marked')))


def video_player_state(page):
    return page.locator('video').evaluate_all('''es => es.map(e => ({
        ready: e.readyState >= 2 && !e.error && Number.isFinite(e.duration) && e.duration > 0,
        error: e.error ? e.error.code : 0,
        url: e.currentSrc || e.src || e.querySelector('source[src]')?.src || '',
        visible: !!(e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden')
    }))''')


def request_mp4_attachment(context, url, referer):
    """Follow file-download redirects only after validating each target."""
    for _ in range(4):
        if not media_url(url):
            raise ValueError('Địa chỉ tải file không hợp lệ.')
        response=context.request.get(url,timeout=60000,headers={'Referer':referer},max_redirects=0)
        if response.status not in (301,302,303,307,308):
            return response
        location=response.headers.get('location','')
        response.dispose()
        if not location:break
        url=urljoin(url,location)
    raise RuntimeError('Link tải file chuyển hướng quá nhiều lần.')


def download_video(context, page, network_urls, output, cancel, report, steps, clean_sources=None, provenance=None):
    if clean_sources is not None:
        # In clean-source mode never silently fall back to the ordinary download.
        for source in clean_sources:
            check_cancel(cancel)
            response=None
            try:
                attachment=source.evidence=='assistant_mp4_attachment'
                if attachment and source.url.startswith('blob:'):
                    link=page.locator('main a[href='+json.dumps(source.url)+']').last
                    with page.expect_download(timeout=15000) as pending:
                        steps.click(link)
                    pending.value.save_as(str(output))
                    check_cancel(cancel)
                    body=output.read_bytes()
                    output.unlink(missing_ok=True)
                else:
                    response=(request_mp4_attachment(context,source.url,page.url) if attachment else
                              context.request.get(source.url,timeout=60000,headers={'Referer':page.url},max_redirects=0))
                    if response.status!=200:continue
                    mime=response.headers.get('content-type','').lower()
                    if 'video/' not in mime and 'octet-stream' not in mime and 'application/mp4' not in mime:continue
                    body=response.body()
                if len(body)<1024 or b'ftyp' not in body[:64]:continue
                length=response.headers.get('content-length') if response is not None else None
                if length and not response.headers.get('content-encoding') and int(length)!=len(body):continue
                check_cancel(cancel)
                output.write_bytes(body)
                if provenance is not None:
                    attachment=source.evidence=='assistant_mp4_attachment'
                    unverified=attachment or 'unverified' in source.evidence
                    provenance.update(mode='attachment_mp4' if attachment else ('model_source' if unverified else 'no_logo'),evidence=source.evidence,
                                      host=urlsplit(source.url).hostname,
                                      source_sha256=hashlib.sha256(source.url.encode()).hexdigest(),
                                      watermark_status='unknown' if unverified else 'provider_marked_unwatermarked')
                report(f'Đã tải nguồn video từ dữ liệu hội thoại: {len(body)/1048576:.1f} MB; đang kiểm tra file.' )
                if 'unverified' in source.evidence: report('Nguồn main_url chưa xác nhận không logo; cần kiểm tra video thực tế.')
                return True
            except Cancelled:raise
            except Exception:
                # Signed URLs and cookies must not appear in logs on failures.
                report('Nguồn video chưa tải được; tiếp tục đợi nguồn hợp lệ.')
            finally:
                if response is not None:response.dispose()
        return False
    # Read only URLs already used by the visible video player; no signed URL rewriting.
    for media_url in collect_video_urls(page, network_urls):
        steps.pause()
        response = None
        try:
            response = context.request.get(media_url, timeout=60000, headers={'Referer': page.url})
            if response.status != 200:
                report(f'Nguồn video trả HTTP {response.status}; thử nguồn khác hoặc nút tải.')
                continue
            mime = response.headers.get('content-type', '').lower()
            if 'video/' not in mime and 'octet-stream' not in mime: continue
            body = response.body()
            if len(body) < 1024 or b'ftyp' not in body[:64]: continue
            length=response.headers.get('content-length')
            if length and not response.headers.get('content-encoding') and int(length)!=len(body): continue
            check_cancel(cancel)
            output.write_bytes(body)
            if provenance is not None:
                from dola_media import watermark_status
                provenance.update(mode='standard',evidence='served_video_url',
                                  host=urlsplit(media_url).hostname,
                                  watermark_status=watermark_status(media_url))
            report(f'Đã tải video từ trình phát: {len(body) / 1048576:.1f} MB')
            return True
        except Cancelled: raise
        except Exception:
            report('Chưa tải được nguồn của thẻ video; thử nguồn khác hoặc nút tải.')
        finally:
            if response is not None: response.dispose()
    # Prefer the site's normal download control, observed in the video preview panel.
    download_button = page.get_by_role('complementary').locator('button[data-dbx-name="button"]')
    if download_button.count() == 1 and download_button.first.is_visible():
        try:
            check_cancel(cancel)
            with page.expect_download(timeout=20000) as info:
                steps.click(download_button.first, timeout=10000)
            download = info.value
            download.save_as(output)
            check_cancel(cancel)
            if output.is_file() and output.stat().st_size > 1024:
                with output.open('rb') as file:
                    if b'ftyp' in file.read(64):
                        report(f'Đã tải bằng nút tải xuống của Dola: {output.stat().st_size / 1048576:.1f} MB')
                        return True
        except BrowserTimeout:
            report('Nút tải xuống chưa phản hồi; đang kiểm tra nguồn của trình phát.')
    return False


@contextmanager
def download_stage():
    """Dola already made the video: any failure from here on is a download error,
    so the task shows 'Lỗi tải video' and 'Lấy video' can fetch it again."""
    try:
        yield
    except (Cancelled, ProfileStopped, VideoDownloadError):
        raise
    except TimeoutError as exc:
        if str(exc) == 'Hết thời gian task':
            raise
        raise VideoDownloadError(str(exc) or 'Hết thời gian tải video.') from None
    except Exception as exc:
        raise VideoDownloadError(str(exc) or 'Không tải được video.') from None


def finish_video(source, settings, task, url, cancel, report, provenance=None):
    with download_stage():
        from dola_media import provenance_has_logo
        if provenance_has_logo(provenance):
            task['_logo_output'] = True
            report('Nguồn video có logo hoặc là link Dola dự phòng; tên file sẽ thêm _logo.')
        else:
            task.pop('_logo_output', None)
        result = _finish_video(source, settings, task, url, cancel, report, provenance)
        if task.get('stt') is not None and not task.get('auto_job'):
            from auto_jobs import output_for_job
            name = task.get('task_code') or f"{task['stt']}_{settings.get('duration', '15')}-{task['id']}"
            if task.get('_logo_output') and not str(name).endswith('_logo'):
                name = str(name) + '_logo'
            final = output_for_job(settings['output'], task['stt'], name)
            final.parent.mkdir(parents=True, exist_ok=True)
            source_path = Path(result).resolve()
            if source_path != final:
                if final.exists():
                    archive = final.parent / '_previous'
                    archive.mkdir(exist_ok=True)
                    final.replace(archive / (final.stem + '_' + uuid.uuid4().hex[:8] + '.mp4'))
                source_path.replace(final)
            result = str(final)
        return result


def _finish_video(source, settings, task, url, cancel, report, provenance=None):
    ffmpeg = find_ffmpeg(settings.get('ffmpeg', ''))
    if not ffmpeg:
        raise RuntimeError('Cần FFmpeg để kiểm tra video tải về. Video gốc đã được giữ lại.')
    final = source.with_name(source.name.replace('.download.mp4', '.mp4'))
    edit = validate_video_edit(settings.get('video_edit'))
    # Keep only the final MP4 after a successful export.
    run_process([ffmpeg,'-hide_banner','-loglevel','error','-xerror','-i',str(source),
                 '-map','0:v:0','-f','null','-'],cancel)
    source_size = video_resolution(source, settings.get('ffmpeg', ''))
    if settings.get('upscale') or edit['mode']!='none':
        export_video(source, final, settings, cancel, report)
        run_process([ffmpeg,'-hide_banner','-loglevel','error','-xerror','-i',str(final),
                     '-map','0:v:0','-f','null','-'],cancel)
        source.unlink(missing_ok=True)
    else:
        source.replace(final)
    final_size = video_resolution(final, settings.get('ffmpeg', ''))
    if final_size:
        src = f'{source_size[0]}x{source_size[1]}' if source_size else 'không rõ'
        hint = ''
        if not settings.get('upscale'):
            hint = ' • Bật "Nâng cấp video 1080p" trong Cấu hình để xuất 1080.'
        elif source_size and min(source_size) < 1080:
            hint = ' • nguồn dưới 1080 nên 1080 là bản phóng to.'
        report(f'Video xuất: {final_size[0]}x{final_size[1]} (nguồn {src}){hint}')
    return str(final)


def _session_cached(account, cache, lock):
    if cache is not None and lock is not None:
        with lock:
            entry=cache.get(account.get('id'))
            return bool(entry.get('valid')) if isinstance(entry,dict) else bool(entry)
    return bool(account.get('session_verified_at'))


def _mark_session_cached(account, cache, lock, name=''):
    if cache is not None and lock is not None:
        with lock:
            cache[account.get('id')] = {'name': name or account.get('name', ''), 'at': time.time(), 'valid': True}


def _mark_session_expired(account, cache, lock, report):
    if cache is not None and lock is not None:
        with lock:
            cache[account.get('id')] = {'at': time.time(), 'valid': False, 'expired': True}
    report('[Phiên hết hạn]')


def wait_for_task_login(page, steps):
    raise SessionDead('Phiên Dola chưa đăng nhập; dừng profile để chuyển tài khoản khác.')


def refresh_completed_video(page, tracker, steps, cancel, report):
    """Refresh once after media appears; collect history responses from the reload."""
    check_cancel(cancel)
    report('Đã có video. Đang tải lại trang (F5) để cập nhật dữ liệu tải…')
    # Reset before reload, never after: the new responses contain the VOD metadata.
    tracker.reset()
    tracker.last_pull=0
    tracker.last_fallback=0
    try:
        response=page.reload(wait_until='domcontentloaded',timeout=45000)
    except BrowserTimeout:
        raise RuntimeError('Trang chưa tải xong sau F5. Dùng Lấy kết quả cũ để thử lại; không gửi lại prompt.') from None
    if response is not None and response.status>=400:
        raise RuntimeError(f'Tải lại hội thoại trả HTTP {response.status}; chưa lấy được link tải.')
    steps.wait_until(lambda: page.locator('main').is_visible(),45,'Trang chưa sẵn sàng sau F5.')
    tracker.drain()
    check_cancel(cancel)
    report('Đang lấy link tải video…')


def render_dola(task, settings, account, cancel, report, session_cache=None, session_cache_lock=None):
    job=render_dola_steps(task,settings,account,cancel,report,session_cache,session_cache_lock)
    try:
        while True:next(job)
    except StopIteration as done:
        return done.value
    finally:
        job.close()


@dataclass
class DeferredVideo:
    """A downloaded source whose FFmpeg export runs outside the browser loop."""
    future: object
    source: Path


def render_dola_steps(task, settings, account, cancel, report, session_cache=None, session_cache_lock=None, *, shared_context=None, task_page=None, video_submit=None, chrome_session=None):
    settings = prepare_account_settings(settings, account, cancel, report)
    check_cancel(cancel)
    settings = dict(settings or {})
    from account_health import work_locked, unlock_text, WorkLimitReached
    if work_locked(account) and str(settings.get('dola_mode', 'pro')).strip().lower() == 'pro':
        when = unlock_text(account.get('work_locked_until'))
        report(f"Tài khoản {account.get('name', '')} đang trong thời gian giới hạn chế độ Work (đến {when}) • dừng task để chuyển tài khoản khác.")
        raise WorkLimitReached(account.get('work_locked_until'))
    from seedance_30s import enabled as merge30_enabled, prompts as prompts30
    merge30 = merge30_enabled(settings)
    export_settings = dict(settings)
    if task.get('run_action') != 'resume':
        if not str(task.get('prompt') or '').strip():
            raise ValueError('Job thiếu prompt; chưa mở trang hoặc gửi yêu cầu.')
    if merge30:
        settings.update(dola_mode='pro', duration='15', seedance_flow='pro_chat', download_pipeline='seedance_api')
        export_settings['duration'] = '30'
        report('Luồng 30s: hai phần 15s nối tiếp trong cùng hội thoại, sau đó ghép MP4.')
    chrome_session = chrome_session if chrome_session is not None else {}
    if settings.get('_task_deadline') is None:
        settings['_task_deadline'] = time.monotonic() + task_timeout_seconds(settings)
    output = Path(settings['output'])
    output.mkdir(parents=True, exist_ok=True)
    source = output / f'dola_{time.strftime("%Y%m%d_%H%M%S")}_{uuid.uuid4().hex[:8]}.download.mp4'
    old_url = task.get('conversation_url', '')
    resume = task.get('run_action') == 'resume'
    if resume:
        task['_dola_submission_attempted'] = True
    if resume and task.get('conversation_account') != account['id']:
        raise ValueError('Lấy kết quả cũ cần đúng profile đã tạo hội thoại đó.')
    if resume and not re.fullmatch(r'https://www\.dola\.com/chat/\d+', old_url):
        raise ValueError('Địa chỉ hội thoại không hợp lệ.')
    report(f"Đang mở profile {account['name']}…")
    with (nullcontext() if shared_context is not None else ProfileLease(account['profile'], settings['lock_root'])), (nullcontext() if shared_context is not None else browser_runtime(settings)) as p:
        context = shared_context if shared_context is not None else launch_context(p, settings, account, video_session=True,cancel=cancel,report=report)
        tracker=None
        try:
            page = task_page if task_page is not None else (context.pages[0] if context.pages else context.new_page())
            steps = BrowserSteps(page, settings, cancel, report)
            tracker=DolaMediaTracker(page)
            source_mode=settings.get('video_source','api_preview')
            # Prefer clean metadata, but the configured default always falls
            # back to Dola's MP4 when no clean artifact is returned.
            strict_no_logo=(source_mode == 'no_logo'
                            and not settings.get('fallback_to_dola_link', True))
            clean_mode=strict_no_logo
            auto_mode=source_mode=='auto'
            # In auto mode, require clean sources before proceeding
            require_clean=clean_mode or auto_mode
            fallback_reported=False
            provenance={}
            ready_page = (settings.get('_reuse_ready_page') and not resume
                          and urlsplit(page.url).hostname in ('www.dola.com', 'dola.com')
                          and urlsplit(page.url).path.rstrip('/') == '/chat')
            if ready_page:
                report('Tab Dola đã sẵn sàng; tiếp tục thao tác trên phiên hiện tại.')
            else:
                report('Đang tải trang Dola cho task…')
                steps.navigate(old_url if resume else CHAT_URL)
            report('Đang chờ ô nhập prompt hoặc trạng thái đăng nhập Dola…')
            steps.wait_until(lambda: is_login_required(page) or page.locator(EDITOR).first.is_visible(),
                             45, 'Trang Dola chưa sẵn sàng.')
            check_cancel(cancel)
            # Confirm this account before the scheduler opens another tab on the same profile.
            ensure_chrome_session(chrome_session,context,page,steps,settings,account,cancel,report)
            yield
            steps.wait_until(lambda: page.locator(EDITOR).first.is_visible(), 30, 'Chưa thấy ô nhập prompt trên Dola.')
            if resume and conversation_url(page)!=old_url:
                steps.navigate(old_url)
            video_baseline=set()
            file_baseline=[]
            # Với hội thoại khôi phục, Share đang có trên lượt cuối chính là
            # kết quả cần đọc. Lượt mới sẽ thay các mốc này trước khi gửi.
            reply_baseline={'assistant': (0, ''), 'shares': 0}
            if not resume:
                prepare_seedance(page, settings, steps, report)
                upload_images(page, task.get('images', []), cancel, report, steps)
                editor = page.locator(EDITOR).first
                prompt_task = dict(task, prompt=prompts30(task['prompt'])[0]) if merge30 else task
                steps.type_prompt(editor, build_prompt(prompt_task, settings))
                check_cancel(cancel)
                tracker.reset()  # Discard media from the home page/previous turn.
                media_receipt(page)  # Arm receipt observer before the first response arrives.
                reply_baseline={'assistant': assistant_snapshot(page),
                                'shares': action_bar_count(page)}
                video_baseline=set(active_video_urls(page))
                file_baseline=scan_files(page).get('keys', [])
                def mark_submission():
                    # This callback runs after trial-click/hover, immediately
                    # before the real click or Enter. Errors after this point
                    # must not turn into an automatic second generation.
                    task['_dola_submission_attempted'] = True
                    task['conversation_account'] = account['id']
                    return True
                steps.submit(editor, should_send=mark_submission)
                report('Đã gửi yêu cầu; đợi 5 giây để hội thoại xuất hiện…')
                steps.wait(5)
                ensure_task_time(settings, report)
                sent_url=conversation_url(page)
                if sent_url: report('[Hội thoại] '+sent_url)
                if is_login_required(page):
                    ensure_chrome_session(chrome_session,context,page,steps,settings,account,cancel,report,force=True)
                if sent_url and conversation_url(page)!=sent_url:
                    steps.navigate(sent_url)
                report('Tiếp tục chờ hội thoại, không gửi lại prompt.')
                def conversation_ready():
                    return bool(conversation_url(page))
                steps.wait_until(conversation_ready, 45,
                                 'Chưa nhận được hội thoại sau khi gửi. Kiểm tra trình duyệt trước khi chạy mới để tránh gửi trùng.')
            url = conversation_url(page)
            task['conversation_url'] = url
            task['conversation_account'] = account['id']
            report('[Hội thoại] ' + url)
            if merge30:
                from seedance_30s import follow as follow30
                provenance = yield from follow30(page, context, url, source, settings, task,
                                                 cancel, report, steps, resume=resume,
                                                 ui_baseline=reply_baseline)
                if video_submit is not None:
                    return DeferredVideo(video_submit(source, export_settings, task, url, cancel, report, provenance), source)
                return finish_video(source, export_settings, task, url, cancel, report, provenance)
            if settings.get('download_pipeline') == 'seedance_api' and source_mode != 'record_element':
                from seedance_video import follow_conversation
                provenance = yield from follow_conversation(page, context, url, source, settings,
                                                           cancel, report, steps, resume=resume,
                                                           ui_baseline=reply_baseline)
                if video_submit is not None:
                    report('Đã tải video qua API; chuyển sang luồng FFmpeg nền.')
                    return DeferredVideo(video_submit(source, settings, task, url, cancel, report, provenance), source)
                return finish_video(source, settings, task, url, cancel, report, provenance)
            if source_mode=='api_preview':
                report('Chế độ lấy video: chờ video xuất hiện trên trang, sau đó lấy nguồn API → FFmpeg (không OCR/ghi màn hình).')
            report('Chờ trả lời…' if not resume else 'Chờ trả lời trong hội thoại đã lưu; không gửi lại prompt.')
            start = time.monotonic()
            paused_at_start = steps.paused
            last_report, last_open, last_download = 0, 0, 0
            player_retries=0;last_player_retry=0
            recording_failed=False
            video_received=False
            video_seen=False
            confirmation_count=0
            last_confirmation_text=''
            reply_completed=False
            text_only_since=0
            images_only_since=0
            treat_as_images=False
            looking_since=0
            video_seen_at=0
            link_stuck_since=0
            link_f5_count=0
            attachment_links=[]
            file_seen=False
            last_file_download=-10
            pending_reply_text=''; pending_reply_since=0
            if resume:
                reply_baseline=(0, '')
            ensure_task_time(settings, report)
            last_scroll = 0
            while time.monotonic() < settings['_task_deadline']:
                yield  # Return control to the per-profile tab scheduler.
                check_cancel(cancel)
                ensure_task_time(settings, report)
                # A logout can happen while a render is still running. This is a
                # lightweight visible-login check; it does not re-import a live session.
                if is_login_required(page):
                    chrome_session.clear()
                    _forget_session_cache(account, session_cache, session_cache_lock)
                    recovery_started=time.monotonic()
                    try:
                        if session_snapshot_path(settings,account).exists():
                            name=restore_cookie_session(context,settings,account,cancel,report,page=page)
                        else:
                            wait_for_task_login(page,steps)
                            name=dola_identity(page)
                    except Cancelled:
                        raise
                    except SessionDead:
                        _mark_session_expired(account, session_cache, session_cache_lock, report)
                        raise
                    except Exception as exc:
                        _mark_session_expired(account, session_cache, session_cache_lock, report)
                        raise ProfileStopped(str(exc) or 'Không khôi phục được phiên Dola; dừng luồng này.') from None
                    report('[Tài khoản] ' + name)
                    chrome_session.update(valid=True,name=name)
                    steps = BrowserSteps(page, settings, cancel, report)
                    steps.navigate(url)
                    start += time.monotonic()-recovery_started
                    paused_at_start=steps.paused
                    tracker.reset()
                steps.guard()
                tracker.drain()
                elapsed = int(time.monotonic() - start - (steps.paused-paused_at_start))
                if page.is_closed():
                    raise RuntimeError('Cửa sổ trình duyệt đã đóng. Dùng nút Lấy kết quả cũ để lấy video từ hội thoại đã lưu.')
                if elapsed - last_scroll >= 4:
                    try:
                        page.evaluate(SCROLL_BOTTOM_JS)
                    except Exception:
                        pass
                    last_scroll = elapsed
                # Kiểm tra nếu chế độ Work bị giới hạn -> tự động hoàn thành trên chế độ Chat bình thường
                is_pro_mode = str(settings.get('dola_mode', 'pro')).strip().lower() == 'pro'
                if is_pro_mode:
                    if not is_generation_in_progress(page) and not genuine_video_url(page, video_baseline) and not scan_files(page, file_baseline).get('count'):
                        resp_text = credit_response_text(page, task)
                        _fr, asst_txt = current_assistant_reply(page, reply_baseline)
                        _fin, fin_txt = latest_turn(page)
                        check_txt = f"{resp_text} {fin_txt or asst_txt}".strip()
                        from dola_limits import is_work_limit_reached
                        if check_txt and is_work_limit_reached(check_txt):
                            until = retry_after(check_txt)
                            report(f'[Khóa Work] {until or ""}')
                            if account:
                                from account_availability import lock_for_work
                                lock_for_work(account, until)
                            from account_health import WorkLimitReached, unlock_text
                            when = unlock_text(until) if until else '00:00 hôm sau'
                            report(f'Tài khoản đạt giới hạn chế độ Work • mở lại {when} • chuyển tài khoản khác.')
                            raise WorkLimitReached(until)

                # Detect credit refusals before confirmation, player interaction
                # or download polling. Split bubbles are joined; the prompt is excluded.
                raise_if_credit_refused(page, task, report, settings=settings)
                tiles = page.locator(VIDEO_TILE)
                states=video_player_state(page)
                if tiles.count() and elapsed - last_open >= 5:
                    tile = tiles.last
                    if tile.is_visible() and not any(s['visible'] for s in states):
                        steps.click(tile, timeout=10000)
                    last_open = elapsed
                states=video_player_state(page)
                ready=any(s['ready'] for s in states)
                links=[u for u in collect_video_links(page) if u not in video_baseline]
                file_cards=scan_files(page, file_baseline)
                file_seen=file_seen or bool(file_cards.get('count'))
                attachment_links=list(dict.fromkeys(attachment_links+links))
                try:
                    card_texts=page.locator('[data-flow-product-card-root], [class*="block-video-"], [class*="video" i]').all_inner_texts()
                except Exception:
                    card_texts=[]
                # Check refusal/limits from the assistant response, not the user's
                # prompt text. Stop immediately on policy/safety refusal.
                _fresh_reply, assistant_text=current_assistant_reply(page, reply_baseline)
                finished, finished_text=latest_turn(page)
                text_for_limits=finished_text or assistant_text
                media_present=file_seen or has_video_result(states,links,tracker.media_urls,card_texts)
                receipt=media_receipt(page)
                # Only a genuine video counts: a playable <video>, a real .mp4/dola
                # /video/ link, or a video URL on the page — never a generated image.
                # This keeps replies made of images from being mistaken for a video.
                attachment_links=[u for u in attachment_links if not is_image_url(u)]
                video_url=genuine_video_url(page, video_baseline)
                video_signal=file_seen or bool(video_url) or bool(attachment_links)
                if file_seen:
                    treat_as_images=False
                if treat_as_images:
                    # Đã kết luận lượt này là ảnh; bỏ mọi tín hiệu "video" giả cho tới
                    # khi gửi lại yêu cầu, để không nhận nhầm lần nữa.
                    video_signal=False
                if video_signal and not video_seen:
                    report('Đã thấy video trên trang; không gửi lại.')
                video_seen=video_seen or video_signal
                if video_seen and not video_seen_at:
                    video_seen_at=time.monotonic()
                # Đã thấy video THẬT trên trang nhưng mãi không lấy được link/nguồn tải:
                # F5 tối đa 3 lần, mỗi lần chờ trang load xong rồi đợi 15s để bắt link;
                # hết 3 lần vẫn không được thì dừng.
                genuine_video_present=(bool(video_url) or ready or file_seen) and not treat_as_images
                if genuine_video_present:
                    if not link_stuck_since:
                        link_stuck_since=time.monotonic()
                    elif time.monotonic()-link_stuck_since>=LINK_STUCK_GRACE_SECONDS:
                        if link_f5_count>=LINK_F5_LIMIT:
                            # Hết 3 lần F5 vẫn chưa có nguồn sạch/link tải. Trừ chế độ 100%
                            # không logo (strict_no_logo), tải bản trang cung cấp (MP4 đính
                            # kèm / video trang, có thể dính logo) coi như kết quả thay vì bỏ task.
                            if strict_no_logo:
                                raise VideoDownloadError('Đã có video nhưng không lấy được link tải sau 3 lần F5; dừng task.')
                            report('Hết 3 lần F5 vẫn chưa lấy được nguồn không logo; tải bản video trang cung cấp (có thể dính logo) để không mất kết quả…')
                            downloaded=False
                            provenance.update(mode='attachment_mp4',watermark_status='unknown')
                            with download_stage():
                                if attachment_links:
                                    files=[VideoSource(u,url.rsplit('/',1)[-1],(),'assistant_mp4_attachment') for u in attachment_links]
                                    downloaded=download_video(context,page,[],source,cancel,report,steps,files,provenance)
                                if not downloaded:
                                    downloaded=download_video(context,page,tracker.media_urls,source,cancel,report,steps,None,provenance)
                                if not downloaded:
                                    downloaded=download_file_card(page,source,cancel,report,file_baseline)
                            if downloaded:
                                report('Đã tải bản video (có thể dính logo) sau khi F5 không ra nguồn sạch; coi như hoàn thành.')
                                break
                            raise VideoDownloadError('Đã có video nhưng sau 3 lần F5 không lấy được nguồn sạch lẫn bản trang cung cấp; dừng task.')
                        link_f5_count+=1
                        report(f'Có video nhưng chưa lấy được link tải; F5 lần {link_f5_count}/{LINK_F5_LIMIT}, chờ trang load rồi đợi {LINK_AFTER_RELOAD_WAIT}s để bắt link…')
                        steps.navigate(url)
                        steps.wait_until(lambda: page.locator(VIDEO_TILE).count() or page.locator(EDITOR).first.is_visible(),
                                         45, 'Trang chưa load xong sau F5.')
                        steps.wait(LINK_AFTER_RELOAD_WAIT)  # chờ 15s sau khi trang load xong rồi bắt link
                        # Không reset tracker: giữ nguồn đã bắt, reload sẽ bổ sung nguồn mới.
                        link_stuck_since=0
                        video_seen=False; video_seen_at=0
                        last_open=last_download=0
                        continue
                else:
                    link_stuck_since=0
                # Nghĩ là có video nhưng suốt một lúc không phát được và cũng không
                # có kết quả media nào (thường là lượt trả về ảnh): coi là ảnh và gửi lại.
                if video_seen and not ready and not media_present and not treat_as_images and not resume:
                    if not looking_since:
                        looking_since=time.monotonic()
                    elif time.monotonic()-looking_since>=VIDEO_STUCK_GRACE_SECONDS:
                        report('Nghĩ là có video nhưng mãi không phát được/không có nguồn; coi lượt này là ảnh và gửi lại yêu cầu tạo video.')
                        treat_as_images=True; video_received=False; video_seen=False
                        attachment_links=[]; looking_since=0
                elif not treat_as_images:
                    looking_since=0

                if policy_blocked(text_for_limits):
                    report('Dola báo bị chặn bởi chính sách an toàn; dừng task, không thử lại.')
                    raise PolicyBlocked('Dola đã chặn yêu cầu bởi chính sách an toàn nội dung.')
                errors = ['đã hết lượt tạo video', 'đã đạt giới hạn tạo video',
                          'het luot tao video', 'dat gioi han tao video']
                if any(term in text_for_limits.casefold() for term in errors):
                    raise RuntimeError('Dola thông báo đã hết lượt hoặc đạt giới hạn tạo video.')
                # Dola đôi khi trả về ẢNH thay vì video. Nếu lượt cuối có ảnh mà
                # không có video và đã ngừng thay đổi (không phải poster đang tạo),
                # coi như trả lời đã xong nhưng thiếu video để gửi lại yêu cầu,
                # kể cả khi chưa nhận ra nút Share.
                images_only=images_only_reply(receipt,media_present,video_signal,video_seen,text_for_limits)
                if images_only:
                    if not images_only_since:
                        images_only_since=time.monotonic()
                        report('Lượt trả về ảnh, chưa có video; chờ ổn định rồi yêu cầu tạo lại video…')
                        last_report=elapsed
                    elif time.monotonic()-images_only_since>=IMAGE_ONLY_GRACE_SECONDS:
                        finished=True  # đi tiếp vào nhánh gửi lại như trả lời chỉ có chữ
                else:
                    images_only_since=0
                if treat_as_images:
                    # Đã chờ đủ lâu và xác định là ảnh: vào thẳng nhánh gửi lại.
                    finished=True
                # Copy/Like có thể hiện khi Dola còn đang làm video. Lượt cuối có
                # nút Share mới là trả lời cuối: chữ + video, hoặc chỉ chữ.
                generating = is_generation_in_progress(page)
                if generating and not video_seen:
                    text_only_since = 0
                    images_only_since = 0
                    if elapsed - last_report >= 15:
                        report(f'Dola đang trong quy trình tạo video ({elapsed}s); tiếp tục chờ bản tải ở dưới…')
                        last_report = elapsed
                    steps.wait(1)
                    continue

                if not video_seen and not finished:
                    text_only_since=0
                    if elapsed-last_report>=20:
                        state='đang trả về ảnh, chờ ổn định' if images_only else 'lượt cuối chưa có nút Share'
                        report(f'Chờ trả lời • {elapsed}s ({state})')
                        last_report=elapsed
                    steps.wait(1)
                    continue
                if not video_seen:
                    # Player có thể gắn nguồn chậm hơn nút Share một chút.
                    if not text_only_since:
                        text_only_since=time.monotonic()
                        report('Câu trả lời cuối đã có nút Share nhưng chưa có video; kiểm tra lại vài giây…')
                        last_report=elapsed
                    if time.monotonic()-text_only_since < 35:
                        steps.wait(1)
                        continue
                    if not reply_completed:
                        reply_completed=True
                        report('Câu trả lời cuối chỉ có văn bản, không có video.')
                reply_ready=(not resume and not video_received and not video_seen)
                if reply_ready and confirmation_count >= VIDEO_CONFIRMATION_LIMIT:
                    raise RetryLimit('Dola chỉ trả văn bản sau 3 lần thử lại; dừng task, không gửi thêm.')
                if reply_ready:
                    confirmation_count += 1
                    last_confirmation_text=finished_text
                    duration=re.search(r'\d+',str(settings.get('duration','15')))
                    seconds=duration.group(0) if duration else '15'
                    model=str(settings.get('model') or 'Seedance 2.5').strip()
                    confirmation=(f'Đồng ý toàn bộ. Hãy TẠO VIDEO NGAY bây giờ và đính kèm video preview vào hội thoại. '
                                  f'KHÔNG hỏi lại, KHÔNG cần xác nhận thêm, tự chọn phương án tốt nhất và làm luôn. '
                                  f'Video {seconds} giây, model {model}. '
                                  f'Chỉ trả về video preview, tuyệt đối không trả lời bằng chữ hay bằng ảnh.')
                    report(f'Câu trả lời đã xong nhưng chỉ có văn bản; thử lại {confirmation_count}/{VIDEO_CONFIRMATION_LIMIT}…')
                    steps.wait_until(lambda: page.locator(EDITOR).first.is_visible(),30,
                                     'Chưa thấy ô nhập để gửi câu xác nhận tạo video.')
                    # Only a genuine video blocks the retry; an image that merely
                    # looks like a video must not stop us from asking again.
                    def real_video():
                        return bool(genuine_video_url(page, video_baseline) or scan_files(page, file_baseline).get('count'))
                    if real_video():
                        video_seen=True
                        report('Video vừa xuất hiện trên trang; không gửi thêm.')
                        continue
                    confirm_editor=page.locator(EDITOR).first
                    steps.type_prompt(confirm_editor,confirmation)
                    if real_video():
                        video_seen=True
                        confirm_editor.fill('')
                        report('Video vừa xuất hiện trên trang; đã xóa câu xác nhận chưa gửi.')
                        continue
                    reply_baseline=assistant_snapshot(page)
                    def allow_retry_send():
                        tracker.drain()
                        return not real_video()
                    if not steps.submit(confirm_editor,should_send=allow_retry_send):
                        confirm_editor.fill('')
                        confirmation_count-=1
                        video_seen=True
                        report('Đã chặn câu xác nhận: trang đã có video. Không gửi thêm.')
                        continue
                    reply_completed=False
                    text_only_since=0
                    images_only_since=0
                    treat_as_images=False
                    looking_since=0
                    video_baseline=set(active_video_urls(page))  # lượt cũ (ảnh) không tính cho lượt mới
                    pending_reply_text='';pending_reply_since=time.monotonic()
                    report(f'Đã gửi yêu cầu thử lại {confirmation_count}/{VIDEO_CONFIRMATION_LIMIT}. Chờ trả lời…')
                    steps.wait(3)
                    continue
                # A returned file is a result even without a player or Share button.
                # Keep strict no-logo mode; ordinary modes accept the actual MP4 file.
                if file_seen and source_mode != 'no_logo' and elapsed-last_file_download >= 3:
                    last_file_download=elapsed
                    report('Đã nhận bản có thể tải từ Dola; đang tải file đính kèm/kết quả…')
                    file_sources=[VideoSource(u,url.rsplit('/',1)[-1],(),'assistant_mp4_attachment') for u in attachment_links]
                    with download_stage():
                        downloaded=bool(file_sources) and download_video(context,page,[],source,cancel,report,steps,file_sources,provenance)
                        if not downloaded:
                            downloaded=download_file_card(page,source,cancel,report,file_baseline)
                    if downloaded:
                        provenance.update(mode='attachment_mp4',watermark_status='unknown')
                        break
                # MP4 đính kèm có thể dính logo. 100% không logo: không bao giờ tải.
                # Không logo bình thường: chỉ tải sau khi đã cho nguồn sạch một khoảng
                # thời gian mà vẫn chưa có, để ưu tiên bản không logo.
                if source_mode=='legacy':
                    from video_export_policy import use_served_video
                    fallback=bool(video_seen_at) and use_served_video(video_seen_at,time.monotonic())
                    if elapsed-last_download>=5 and video_seen_at:
                        downloaded=False
                        with download_stage():
                            if not fallback:
                                players=[s['url'] for s in states if s['url']]
                                clean=tracker.candidates(url.rsplit('/',1)[-1],players,allow_model=False)
                                if clean:
                                    from video_watermark import WatermarkCheckError
                                    try:
                                        downloaded=download_video(context,page,[],source,cancel,report,steps,clean,provenance)
                                    except WatermarkCheckError:
                                        report('Không kiểm tra được nguồn sạch; sẽ dùng nguồn trang cung cấp theo cách cũ.')
                                        video_seen_at=time.monotonic()-20
                            else:
                                report('Đã chờ nguồn không logo 20 giây; tải video trang cung cấp theo cách cũ (có thể có logo).')
                                if attachment_links:
                                    files=[VideoSource(u,url.rsplit('/',1)[-1],(),'assistant_mp4_attachment') for u in attachment_links]
                                    downloaded=download_video(context,page,[],source,cancel,report,steps,files,provenance)
                                if not downloaded:
                                    downloaded=download_video(context,page,[],source,cancel,report,steps,None,provenance)
                        if downloaded:break
                        last_download=elapsed
                    if elapsed-last_report>=20:
                        report('Đã có video; đang tải nguồn trên trang.' if fallback else 'Đã có video; ưu tiên nguồn không logo tối đa 20 giây, sau đó tải nguồn trang cung cấp.')
                        last_report=elapsed
                    steps.wait(1)
                    continue
                clean_grace_passed=bool(video_seen_at) and (time.monotonic()-video_seen_at>=NO_LOGO_FALLBACK_SECONDS)
                if attachment_links and not strict_no_logo and clean_grace_passed:
                    if elapsed-last_download < 5:
                        steps.wait(1)
                        continue
                    report('Chưa có nguồn không logo sau khi chờ; tải MP4 đính kèm (có thể dính logo).')
                    provenance.update(mode='attachment_mp4',watermark_status='unknown')
                    file_sources=[VideoSource(u,url.rsplit('/',1)[-1],(),'assistant_mp4_attachment') for u in attachment_links]
                    with download_stage():
                        downloaded=download_video(context,page,[],source,cancel,report,steps,file_sources,provenance)
                    if downloaded:
                        break
                    last_download=elapsed
                    steps.wait(1)
                    continue
                if source_mode=='api_preview':
                    if not video_received:
                        video_received=video_signal
                        if not video_received:
                            if elapsed-last_report>=20:
                                report(f'Chờ trả video • {elapsed}s (câu trả lời đã hoàn tất)')
                                last_report=elapsed
                            steps.wait(1)
                            continue
                        # Resolve already captured metadata before refreshing it away.
                        direct=tracker.resolved_clean(url.rsplit('/',1)[-1])
                        if direct:
                            from element_recording import record_api_preview
                            with download_stage():
                                record_api_preview(context,page,direct,source,settings,cancel,report,provenance)
                            break
                        # Nếu đã có bản MP4 đính kèm hoặc card kết quả (đặc biệt trong chế độ Work), tải trực tiếp không cần F5
                        if (file_seen or attachment_links) and not strict_no_logo:
                            downloaded = False
                            with download_stage():
                                if attachment_links:
                                    file_sources = [VideoSource(u, url.rsplit('/',1)[-1], (), 'assistant_mp4_attachment') for u in attachment_links]
                                    downloaded = download_video(context, page, [], source, cancel, report, steps, file_sources, provenance)
                                if not downloaded and file_seen:
                                    downloaded = download_file_card(page, source, cancel, report, file_baseline)
                            if downloaded:
                                provenance.update(mode='attachment_mp4', watermark_status='unknown')
                                break
                        # A placeholder card is not enough to refresh an active generation.
                        if not media_present:
                            video_received=False
                            steps.wait(1)
                            continue
                        with download_stage():
                            refresh_completed_video(page,tracker,steps,cancel,report)
                        last_report=int(time.monotonic()-start-(steps.paused-paused_at_start))
                        # Re-read all page state on the next tick, after navigation.
                        continue
                    tracker.pull_history(url.rsplit('/',1)[-1])
                    api_sources=tracker.resolved_clean(url.rsplit('/',1)[-1])
                    if api_sources:
                        from element_recording import record_api_preview
                        with download_stage():
                            record_api_preview(context,page,api_sources,source,settings,cancel,report,provenance)
                        break
                    # Fallback dự phòng khi API không trả nguồn sạch (chế độ Work hoặc API preview không có fallback)
                    clean_grace = bool(video_seen_at) and (time.monotonic() - video_seen_at >= 15)
                    if clean_grace and not strict_no_logo:
                        downloaded = False
                        with download_stage():
                            if attachment_links:
                                file_sources = [VideoSource(u, url.rsplit('/',1)[-1], (), 'assistant_mp4_attachment') for u in attachment_links]
                                downloaded = download_video(context, page, [], source, cancel, report, steps, file_sources, provenance)
                            if not downloaded and file_seen:
                                downloaded = download_file_card(page, source, cancel, report, file_baseline)
                            if not downloaded:
                                players = [s['url'] for s in states if s['url']]
                                candidates = tracker.candidates(url.rsplit('/',1)[-1], players, allow_model=True)
                                if candidates:
                                    downloaded = download_video(context, page, tracker.media_urls, source, cancel, report, steps, candidates, provenance)
                            if not downloaded and (states or tracker.media_urls or links):
                                downloaded = download_video(context, page, tracker.media_urls, source, cancel, report, steps, None, provenance)
                        if downloaded:
                            break
                    if elapsed-last_report>=20:
                        report(f'Đã nhận video; đang lấy nguồn không logo • {elapsed}s '
                               f'(đã bắt {len(tracker.fallbacks)} API nguồn). Không gửi thêm.')
                        last_report=elapsed
                    steps.wait(1)
                    continue
                if source_mode=='record_element' and any(s['ready'] and s['visible'] for s in states) and not recording_failed:
                    from element_recording import record_element
                    try:
                        record_element(page,source,settings,cancel,report,provenance)
                        break
                    except Cancelled: raise
                    except Exception as exc:
                        detail=re.sub(r'https?://[^\s]+', '[URL]', str(exc))[:900]
                        raise VideoDownloadError('Ghi preview thất bại: '+detail) from None
                players=[s['url'] for s in states if s['url']]
                sources=tracker.candidates(url.rsplit('/',1)[-1],players,allow_model=auto_mode) if require_clean else None
                link_fallback=bool(links) and (not ready or recording_failed)
                if source_mode=='record_element':
                    available=link_fallback or (recording_failed and bool(players or tracker.media_urls))
                else:
                    available=bool(sources) if require_clean else bool(sources or players or tracker.media_urls or links)
                if source_mode=='record_element': available=False
                elif link_fallback and not require_clean: available=True
                if available and elapsed - last_download >= 5:
                    if not ready: report('Đã có nguồn video; thử tải trực tiếp dù trình phát chưa hiển thị được.')
                    downloaded=False
                    with download_stage():
                        if require_clean and sources:
                            # Clean/auto mode: only download confirmed clean sources
                            downloaded=download_video(context,page,tracker.media_urls,source,cancel,report,steps,sources,provenance)
                        elif not require_clean:
                            # Standard mode: download any available source
                            if link_fallback: report('Đã nhận link Xem video; tải nguồn trang cung cấp, chưa xác nhận không watermark.')
                            provenance.update(mode='standard',watermark_status='unknown')
                            downloaded=download_video(context,page,tracker.media_urls,source,cancel,report,steps,None,provenance)
                    if downloaded:
                        break
                    last_download = elapsed
                if any(s['error'] for s in states) and player_retries<2 and elapsed-last_player_retry>=15:
                    page.locator('video').evaluate_all('(es)=>es.filter(e=>e.error).forEach(e=>e.load())')
                    player_retries+=1;last_player_retry=elapsed
                    report(f'Trình phát báo lỗi; tải lại media ({player_retries}/2), không gửi lại prompt.')
                if elapsed - last_report >= 20:
                    if require_clean and (players or tracker.media_urls) and not sources:
                        report(f'Đã có video; đang lấy nguồn không logo • {elapsed}s '
                               f'(đã bắt {len(tracker.fallbacks)} API nguồn).')
                    elif states and not ready:
                        codes=','.join(str(s['error']) for s in states if s['error']) or 'chưa có dữ liệu phát'
                        report(f'Trình phát chưa hiển thị video ({codes}); đang kiểm tra nguồn tải • {elapsed}s')
                    else:
                        report(f'Chờ trả video • {elapsed}s (câu trả lời đã hoàn tất)')
                    last_report = elapsed
                page.wait_for_timeout(1000)
            else:
                ensure_task_time(settings, report)
                raise TimeoutError('Hết thời gian task')
            check_cancel(cancel)
            if video_submit is not None:
                report('Đã tải video; chuyển sang luồng FFmpeg nền, tab được giải phóng.')
                future=video_submit(source, settings, task, url, cancel, report, provenance)
                return DeferredVideo(future, source)
            return finish_video(source, settings, task, url, cancel, report,provenance)
        finally:
            if tracker is not None:
                try:tracker.close()
                except Exception:pass
            if shared_context is None:
                try: context.close()
                except Exception: pass
