"""Upload a document and require a ready composer card before sending a prompt."""
import re
import time
from pathlib import Path

from browser_runtime import BrowserError, BrowserTimeout
from engine import Cancelled

ROOT = '[data-studio-file-composer="active"]'
CARD = '[data-testid="attachment_file_item"]'
NAME = '[data-testid="message_nested_content_file_name"]'
DELETE = '[data-testid="message_nested_content_file_delete"]'
UPLOAD = '[data-testid="upload_file_button"]'

_SNAPSHOT = r'''filename => {
    const visible=el=>!!(el.getClientRects().length && getComputedStyle(el).visibility!=='hidden');
    const editors=[...document.querySelectorAll('[contenteditable="true"]')].filter(visible);
    const editor=editors[editors.length-1];
    document.querySelectorAll('[data-studio-file-composer]').forEach(el=>el.removeAttribute('data-studio-file-composer'));
    if(!editor) return {found:false,cards:[]};
    let root=null;
    for(let node=editor.parentElement;node && node!==document.body;node=node.parentElement) {
        // Never count an attachment from a previously sent message as this upload.
        if(node.querySelector('.list_items,[data-message-author-role],[data-message-role], [data-testid^="conversation-turn"]')) break;
        if(node.querySelector('[data-testid="upload_file_button"]')) {
            root=root || node;
            if(node.querySelector('[data-testid="carousel-item"],[data-testid="attachment_file_item"]')) {root=node;break;}
        }
    }
    if(!root) return {found:false,cards:[]};
    root.setAttribute('data-studio-file-composer','active');
    const cards=[...root.querySelectorAll('[data-testid="attachment_file_item"]')]
        .filter(el=>el.querySelector('[data-testid="message_nested_content_file_name"]')?.textContent.trim()===filename)
        .map(el=>({available:el.getAttribute('data-available'),
            subtitle:el.querySelector('[data-testid="message_nested_content_file_subtitle"]')?.textContent.trim() || '',
            busy:!!el.querySelector('[role="progressbar"],[aria-busy="true"],[data-loading="true"],[class*="spin"],[class*="loading"]')}));
    return {found:true,cards};
}'''


class AttachmentError(RuntimeError):
    pass


def snapshot(page, filename):
    return page.evaluate(_SNAPSHOT, filename)


def failed(card):
    return bool(re.search(r'không thể tải|tải lên thất bại|upload failed|failed to upload|couldn.t upload|unsupported|không hỗ trợ',
                          card['subtitle'], re.I))


def ready(card):
    return (card['available'] == 'true' and not card['busy'] and not failed(card)
            and not re.search(r'đang (?:tải|xử lý)|uploading|processing|\d+\s*%', card['subtitle'], re.I))


def matching_cards(page, filename):
    return page.locator(ROOT).locator(CARD).filter(
        has=page.locator(NAME).filter(has_text=re.compile(r'^\s*'+re.escape(filename)+r'\s*$')))


def remove_cards(page, filename, steps):
    """Click the site's delete action; never just remove DOM nodes."""
    for _ in range(20):
        steps.guard()
        before=snapshot(page, filename)
        if not before['found']:
            raise AttachmentError('Không xác định được khung đính kèm trong ô nhập.')
        count=len(before['cards'])
        if not count:return
        card=matching_cards(page,filename).first
        card.hover(timeout=3000)
        delete=card.locator(DELETE).first
        if delete.is_visible():
            delete.click(timeout=3000)
        else:
            # Dola's supplied SVG has !hidden. Dispatch its actual click handler
            # so React removes the upload from application state as well.
            delete.dispatch_event('click',timeout=3000)
        until=time.monotonic()+5
        while time.monotonic()<until:
            steps.guard()
            state=snapshot(page,filename)
            if state['found'] and len(state['cards'])<count:break
            steps.wait(.2)
        else:
            raise AttachmentError('Không đóng được thẻ file cũ; chưa tải lại để tránh trùng file.')
    raise AttachmentError('Có quá nhiều thẻ file trùng; chưa gửi prompt.')


def choose_file(page, path, steps):
    def use_input():
        for node in page.locator(ROOT).locator('input[type="file"]').all():
            accept=(node.get_attribute('accept') or '').lower().replace(' ','').split(',')
            if any(value in ('','*','*/*','.md','text/*','text/markdown','text/plain','application/octet-stream') for value in accept):
                # Selecting the same filename twice otherwise may not fire change.
                node.set_input_files([],timeout=8000)
                node.set_input_files(str(path),timeout=8000)
                return True
        return False
    if use_input():return
    button=page.locator(ROOT).locator(UPLOAD).first
    try:
        with page.expect_file_chooser(timeout=3000) as chooser:
            button.click(timeout=3000)
        chooser.value.element.set_input_files([],timeout=8000)
        chooser.value.set_files(str(path),timeout=8000)
        return
    except BrowserTimeout:
        # Some layouts open an upload menu before the system file chooser.
        if use_input():return
    menu=page.get_by_role('menuitem',name=re.compile(
        r'tải.*(?:tệp|tập tin|máy)|(?:upload|add|attach).*(?:file|device|computer)',re.I)).filter(visible=True)
    if menu.count():
        with page.expect_file_chooser(timeout=5000) as chooser:
            menu.first.click(timeout=3000)
        chooser.value.element.set_input_files([],timeout=8000)
        chooser.value.set_files(str(path),timeout=8000)
        return
    raise AttachmentError('Không mở được ô chọn file từ nút đính kèm.')


def upload_document(page, path, steps, cancel, report, *, attempts=3, timeout=120):
    path=Path(path)
    if not path.is_file() or path.stat().st_size == 0:
        raise AttachmentError('Không tìm thấy file SKILL.md hợp lệ; chưa nhập/gửi prompt.')
    name=path.name
    last_error=''
    for attempt in range(1,attempts+1):
        steps.guard()
        if cancel.is_set():raise Cancelled('Đã dừng đính kèm file.')
        # Remove *all* matching cards before a retry, even if a late upload
        # changed one to ready. Do not affect image references or other files.
        remove_cards(page,name,steps)
        report(f'Đang tải {name} • lần {attempt}/{attempts}…')
        try:
            choose_file(page,path,steps)
        except BrowserError:
            last_error='Không chọn được file trong trình duyệt.'
        except AttachmentError as exc:
            last_error=str(exc)
        else:
            deadline=time.monotonic()+timeout
            stable=None
            while time.monotonic()<deadline:
                steps.guard()
                if cancel.is_set():raise Cancelled('Đã dừng đính kèm file.')
                state=snapshot(page,name)
                cards=state['cards']
                if any(failed(card) for card in cards):
                    last_error='Dola báo Không thể tải lên.'
                    break
                if len(cards)>1:
                    last_error='Dola tạo nhiều thẻ file trùng.'
                    break
                if state['found'] and len(cards)==1 and ready(cards[0]):
                    stable=stable or time.monotonic()
                    if time.monotonic()-stable>=.8:
                        report(f'Đã xác nhận {name} tải lên thành công; bắt đầu nhập prompt.')
                        return True
                else:stable=None
                steps.wait(.25)
            else:last_error=f'Chưa xác nhận được upload sau {timeout:g} giây.'
        report(last_error+' Đang đóng thẻ file để '+('thử lại.' if attempt<attempts else 'dừng task.'))
        remove_cards(page,name,steps)
        if attempt<attempts:steps.wait(1)
    raise AttachmentError(f'Không tải được {name} sau {attempts} lần; chưa nhập/gửi prompt. '+last_error)
