"""Observable Basic/Create Video and Pro-chat flows from Seedance Studio."""
import re
import time
from seedance_prompt import format_dola_prompt

EDITOR = 'textarea:visible, [contenteditable="true"]:visible'
MENUS = '[role="menu"]:visible, [role="listbox"]:visible, [data-slot="dropdown-menu-content"]:visible'
FLOW_LABELS = {'Tự động': 'auto', 'Cơ bản · Form tạo video': 'video_form', 'Pro · Chat thường': 'pro_chat'}


def visible(locator):
    return next((item for item in locator.all() if item.is_visible()), None)


def form_flow(settings):
    value = settings.get('seedance_flow', 'auto')
    if value != 'auto':
        if value not in ('video_form', 'pro_chat'):
            raise ValueError('Luồng tự động Dola không hợp lệ.')
        return value == 'video_form'
    return str(settings.get('duration', '15')).strip().rstrip('s') == '30' and settings.get('thirty_strategy', 'merge') != 'merge'


def prompt_text(task, settings):
    text = str(task.get('prompt', '')).strip()
    if not text:
        raise ValueError('Prompt rỗng; chưa gửi yêu cầu.')
    video_form = form_flow(settings)
    duration = str(settings.get('duration', '15')).strip().rstrip('s')
    if not video_form and duration not in ('5', '10', '15'):
        raise ValueError('Pro chat hỗ trợ 5/10/15 giây. Chọn form tạo video để dùng 30 giây.')
    formatted = format_dola_prompt(text, {
        'mode': 'credit_video' if video_form else 'single',
        'duration': duration + 's',
        'aspectRatio': settings.get('ratio', '16:9'),
        'model': settings.get('model', 'Seedance 2.5'),
    })
    if not formatted:
        raise ValueError('Prompt không còn mô tả sau khi lọc thông số; chưa gửi yêu cầu.')
    return formatted


PRO_WORDS = r'(?:Work|Pro|Công\s*việc|Chuyên\s*nghiệp|ワーク|工作|プロ|专业)'
CHAT_WORDS = r'(?:Chat|Fast|Trò\s*chuyện|Nhanh|高速|快速|チャット|聊天)'

PRO_PATTERN = re.compile(
    r'(?:^|[^\w])' + PRO_WORDS + r'(?:[^\w]|$)',
    re.I
)
CHAT_PATTERN = re.compile(
    r'(?:^|[^\w])' + CHAT_WORDS + r'(?:[^\w]|$)',
    re.I
)


def _is_pro_mode(mode):
    return str(mode or '').strip().lower() == 'pro'


def _is_item_disabled_or_limited(item):
    if item is None:
        return False
    if (item.get_attribute('aria-disabled') == 'true' or
        item.get_attribute('data-disabled') is not None or
        item.get_attribute('disabled') is not None):
        return True
    txt = ' '.join(str(item.inner_text() or '').split()).lower()
    return any(k in txt for k in ('limit', 'giới hạn', 'hết lượt', 'het luot', 'quota', '0/'))


def select_mode(page, mode, steps, report=None):
    is_pro = _is_pro_mode(mode)
    selectors = (
        '[data-valid-btn="mode-select-action-btn"]:visible, '
        '[data-testid="chat_input_action_mode"]:visible, '
        '[data-testid="mode_select_button"]:visible'
    )

    def trigger():
        btn = visible(page.locator(selectors))
        if btn is not None:
            return btn
        for candidate in page.locator('[data-testid="chat_input"] button:visible, button:visible').all():
            try:
                txt = ' '.join(str(candidate.inner_text() or '').split())
                if not txt:
                    continue
                if re.search(r'^(?:⚡\s*|⚛\s*)?(?:Chat|Work|Fast|Pro|Nhanh|Chuyên\s*nghiệp|高速|快速|チャット|ワーク|聊天|工作)(?:\s*(?:[>›•·–—|-]|Mới|Moi|New|新着))*$', txt, re.I):
                    return candidate
            except Exception:
                continue
        return visible(page.locator('button:has-text("Chat >"):visible, button:has-text("Work >"):visible'))

    try:
        steps.wait_until(lambda: trigger() is not None, 5, 'Chưa thấy nút chọn chế độ.')
    except TimeoutError:
        return 'pro' if is_pro else 'fast'
    trig = trigger()
    if trig is None:
        return 'pro' if is_pro else 'fast'

    def matches_mode(elem, check_pro=None):
        if elem is None:
            return False
        target_pro = is_pro if check_pro is None else check_pro
        text = ' '.join(str(elem.inner_text() or '').split())
        aria = str(elem.get_attribute('aria-label') or '')
        combined = f'{text} {aria}'.strip()
        if target_pro:
            return bool(PRO_PATTERN.search(combined) and not CHAT_PATTERN.search(combined))
        else:
            return bool(CHAT_PATTERN.search(combined) and not PRO_PATTERN.search(combined))

    if matches_mode(trig):
        return 'pro' if is_pro else 'fast'

    trig_text = ' '.join(str(trig.inner_text() or '').split())
    trig_aria = str(trig.get_attribute('aria-label') or '')
    combined = f'{trig_text} {trig_aria}'.strip()
    is_mode_switch = bool(PRO_PATTERN.search(combined) or CHAT_PATTERN.search(combined))
    if not is_mode_switch:
        return 'pro' if is_pro else 'fast'

    steps.click(trig)

    def find_option_for(target_is_pro):
        tid = 'deep-thinking-action-item-' + ('4' if target_is_pro else '0')
        holder = visible(page.get_by_test_id(tid))
        if holder is not None:
            return visible(holder.locator('[role="menuitem"], [role="menuitemradio"], button')) or holder

        target_pat = PRO_PATTERN if target_is_pro else CHAT_PATTERN
        other_pat = CHAT_PATTERN if target_is_pro else PRO_PATTERN

        candidates = []
        for sel in ('[role="menuitem"]:visible', '[role="menuitemradio"]:visible', '[role="option"]:visible',
                    f'{MENUS} button:visible', f'{MENUS} [data-testid]:visible', f'{MENUS} li:visible'):
            candidates.extend(page.locator(sel).all())

        for cand in candidates:
            try:
                if not cand.is_visible() or cand == trig:
                    continue
                txt = ' '.join(str(cand.inner_text() or '').split())
                if target_pat.search(txt) and not other_pat.search(txt):
                    return cand
            except Exception:
                continue

        target_words = r'^\s*(?:Work|Pro|Công\s*việc|Chuyên\s*nghiệp)\b' if target_is_pro else r'^\s*(?:Chat|Fast|Trò\s*chuyện|Nhanh)\b'
        name_re = re.compile(target_words, re.I)
        direct = (visible(page.get_by_role('menuitem', name=name_re)) or
                  visible(page.get_by_role('menuitemradio', name=name_re)) or
                  visible(page.get_by_role('option', name=name_re)))
        if direct is not None:
            return direct

        for choice in page.get_by_text(name_re).all():
            try:
                if choice.is_visible() and choice != trig:
                    txt = ' '.join(str(choice.inner_text() or '').split())
                    if not other_pat.search(txt):
                        return choice
            except Exception:
                continue

        return visible(page.locator(MENUS).get_by_text(target_pat))

    def option():
        return find_option_for(is_pro)

    try:
        steps.wait_until(lambda: option() is not None, 5, 'Chưa thấy chế độ đã chọn.')
    except TimeoutError:
        try:
            steps.click(trig)
        except Exception:
            pass
        return 'pro' if is_pro else 'fast'

    opt = option()

    if is_pro and opt is not None and _is_item_disabled_or_limited(opt):
        if report:
            report('Chế độ Work đang bị khóa/hết hạn mức; dừng task theo cấu hình chỉ dùng chế độ Work.')
        raise RuntimeError('Chế độ Work đang bị khóa hoặc đạt giới hạn trên Dola; dừng task theo cấu hình chỉ dùng chế độ Work.')

    _enabled(opt, 'chế độ')
    steps.click(opt)
    steps.wait_until(lambda: matches_mode(trigger(), is_pro),
                     10, 'Dola chưa xác nhận chế độ; chưa gửi prompt.')
    return 'pro' if is_pro else 'fast'


def _enabled(item, label):
    if item.get_attribute('aria-disabled') == 'true' or item.get_attribute('data-disabled') is not None:
        raise RuntimeError(f'Dola đang khóa {label}; chưa gửi prompt.')


def dropdown(page, key, fallback, target, label, steps):
    def control():
        return visible(page.locator(f'[data-input-engine-actionbar-control-key="{key}"]:visible')) or \
               visible(page.get_by_role('button', name=fallback))
    steps.wait_until(lambda: control() is not None, 30, f'Không thấy {label}; chưa gửi prompt.')
    if target.search(control().inner_text().strip()):
        return
    steps.click(control())
    def choice():
        menus = page.locator(MENUS)
        return visible(menus.get_by_role('menuitem').filter(has_text=target)) or \
               visible(menus.get_by_role('menuitemradio').filter(has_text=target)) or \
               visible(menus.get_by_role('option').filter(has_text=target)) or visible(menus.get_by_text(target))
    steps.wait_until(lambda: choice() is not None, 10, f'Dola không có {label} đã chọn; chưa gửi prompt.')
    _enabled(choice(), label)
    steps.click(choice())
    steps.wait_until(lambda: control() is not None and bool(target.search(control().inner_text().strip())),
                     10, f'Dola chưa áp dụng {label}; chưa gửi prompt.')


def prepare(page, settings, steps, report):
    mode = str(settings.get('dola_mode', 'pro')).strip().lower()
    select_mode(page, mode, steps, report=report)

    if not form_flow(settings):
        mode_label = 'Work' if mode == 'pro' else 'Chat'
        report(f'Chế độ {mode_label} đã sẵn sàng; thông số video được gửi trong prompt.')
        return
    active = '[data-input-engine-actionbar-control-key="video-duration"]:visible'
    if visible(page.locator(active)) is None:
        label = re.compile(r'^(?:動画を作成|Create videos?|Tạo video|生成视频|创建视频)(?:\s*(?:Mới|New))?$', re.I)
        def create():
            return visible(page.get_by_role('button', name=label)) or visible(page.get_by_text(label))
        if create() is None:
            # Same fallback as the reference enterVideoSkill: toolbar Skills,
            # then its Create Video menu item. Exclude the sidebar entry.
            for item in page.get_by_role('button', name=re.compile(r'^(?:Skills?|Kỹ năng|技能)\s*[>›]?$', re.I)).all():
                box = item.bounding_box() if item.is_visible() else None
                if box and box['y'] > (page.viewport_size or {'height': page.evaluate('innerHeight')})['height'] * .35:
                    steps.click(item)
                    break
        steps.wait_until(lambda: create() is not None, 45, 'Chưa thấy Create Video; chưa gửi prompt.')
        steps.click(create())
    steps.wait_until(lambda: visible(page.locator(active)) is not None, 45, 'Form video chưa sẵn sàng; chưa gửi prompt.')
    match = re.search(r'2\.[05]', str(settings.get('model', 'Seedance 2.5')))
    if not match:
        raise ValueError('Model chưa được hỗ trợ.')
    version = match.group()
    dropdown(page, 'video-model', re.compile(r'^(?:Model|モデル|模型|Mô hình|(?:Dreamina\s+)?Seedance)', re.I),
             re.compile(r'(?:Dreamina\s+)?Seedance\s*' + re.escape(version) + r'(?:\D|$)', re.I), 'model', steps)
    ratio = str(settings.get('ratio', '16:9'))
    dropdown(page, 'video-ratio', re.compile(r'^(?:Ratio|Aspect ratio|Tỉ lệ|Tỷ lệ|比率|比例|\d+\s*:\s*\d+)', re.I),
             re.compile(r'^\s*' + re.escape(ratio) + r'\s*$'), 'tỷ lệ', steps)
    duration = str(settings.get('duration', '15')).strip().rstrip('s')
    if not duration.isdigit():
        raise ValueError('Thời lượng không hợp lệ.')
    dropdown(page, 'video-duration', re.compile(r'^\d+\s*(?:s|秒|giây)$', re.I),
             re.compile(r'^\s*' + duration + r'\s*(?:s|秒|giây)\s*$', re.I), 'thời lượng', steps)
    report(f'Form video: Seedance {version} · {ratio} · {duration}s')


def upload_images(page, images, steps, report):
    from pathlib import Path
    if len(images) > 10 or any(not Path(image).is_file() for image in images):
        raise ValueError('Ảnh bị thiếu hoặc vượt quá 10 ảnh; chưa gửi prompt.')
    scope = page.locator('[data-testid="chat_input"]')
    if not scope.count():
        if images:
            raise RuntimeError('Không thấy vùng đính kèm Dola; chưa gửi prompt.')
        return
    deletes = scope.locator('[data-testid="attachment-delete-btn"]')
    while deletes.count():
        previous = deletes.count()
        steps.click(deletes.first)
        steps.wait_until(lambda: deletes.count() < previous, 5, 'Chưa xóa được ảnh nháp cũ; chưa gửi prompt.')
    if not images:
        return
    editor = visible(scope.locator(EDITOR))
    if editor is not None:
        editor.click()
    inputs = scope.locator('input[type="file"]').first
    def count():
        return max(deletes.count(), scope.locator('[data-testid="attachment_area"] img').count())
    pending = set()
    failures = []
    def started(request):
        if request.method in ('POST', 'PUT') and any(part in request.url for part in
            ('/alice/resource/prepare_upload', '/upload/v1/', 'ibytedtos.com', 'tos-s3')):
            pending.add(request)
    def ended(request):
        pending.discard(request)
    def failed(request):
        if request in pending:
            failures.append('Tải ảnh lỗi mạng; chưa gửi prompt.')
        ended(request)
    def response_done(response):
        if response.request in pending and response.status >= 400:
            failures.append(f'Tải ảnh lỗi HTTP {response.status}; chưa gửi prompt.')
    callbacks = {'request': started, 'requestfinished': ended, 'requestfailed': failed, 'response': response_done}
    for event, callback in callbacks.items():
        page.on(event, callback)
    try:
        for index, image in enumerate(images, 1):
            before = count()
            steps.wait_until(lambda: inputs.count() and inputs.is_enabled(), 30, 'Ô tải ảnh chưa sẵn sàng.')
            report(f'Đang gắn ảnh {index}/{len(images)} theo thứ tự…')
            inputs.set_input_files(image)
            quiet = None
            def uploaded():
                nonlocal quiet
                if failures:
                    raise RuntimeError(failures[0])
                if visible(scope.get_by_text(re.compile(r'upload failed|tải lên thất bại', re.I))) is not None:
                    raise RuntimeError('Dola báo tải ảnh thất bại; chưa gửi prompt.')
                if pending or count() <= before:
                    quiet = None
                    return False
                quiet = quiet if quiet is not None else time.monotonic()
                return time.monotonic() - quiet >= 2
            steps.wait_until(uploaded, 180, f'Chưa xác nhận ảnh {index} tải xong; chưa gửi prompt.')
    finally:
        for event, callback in callbacks.items():
            page.remove_listener(event, callback)


def clarification(text, settings):
    if re.search(r'generating|queued|rendering|in progress|đang (?:tạo|xử lý)|生成中|作成中', text, re.I):
        return ''
    if not re.search(r'(?:choose|select|specify)[^.\n]{0,80}(?:duration|seconds|length)|'
                     r'(?:chọn|cho biết|bao nhiêu|xác nhận)[^.\n]{0,80}(?:giây|thời lượng)|'
                     r'(?:何秒|秒数|長さ).{0,40}(?:指定|選択|必要|ですか)', text, re.I):
        return ''
    return (f"Hãy tạo video {settings.get('duration', '15')} giây, tỷ lệ {settings.get('ratio', '16:9')}, "
            f"model {settings.get('model', 'Seedance 2.5')}. Đây là xác nhận thông số đã chọn.")
