"""Recognize credit refusal / usage-limit messages, not ordinary pricing information."""
from datetime import datetime, timedelta, timezone
import re
import unicodedata


def _plain(text):
    value = unicodedata.normalize('NFKD', str(text or '').casefold())
    value = ''.join(c for c in value if not unicodedata.combining(c)).replace('đ', 'd')
    return re.sub(r'\s+', ' ', value)


def usage_limit_reached(text):
    """Dola hết lượt dùng của tài khoản, ví dụ:
    'Bạn đã đạt giới hạn 7 ngày miễn phí. Hãy quay lại vào 22:32, 14 tháng Mười, 2026 (GMT+07:00)…'"""
    value = _plain(text)
    vietnamese = (re.search(r'(?:dat|vuot|het)\s+(?:gioi han|han muc|luot)', value)
                  and re.search(r'quay lai|thu lai|mien phi|che do nhanh', value))
    english = re.search(r'(?:reached|hit|exceeded|used up)\s+(?:your\s+|the\s+)?(?:\w+\s+){0,3}(?:limit|quota)', value) \
        or re.search(r'(?:usage|free|daily|weekly)\s+limit\s+(?:reached|exceeded)', value)
    return bool(vietnamese or english)


def is_work_limit_reached(text):
    """Kiểm tra xem Dola có báo đạt giới hạn / hết lượt của chế độ Work hay không.
    Bao gồm các thông báo giới hạn chế độ Work / Pro / Deep Thinking, gợi ý chuyển sang Chat / Nhanh,
    hoặc giới hạn 7 ngày / theo ngày mà Chat vẫn dùng được."""
    value = _plain(text)
    if not value:
        return False
    # 1. Nhắc cụ thể tới Work / Pro và giới hạn / hết lượt
    has_work_pro = bool(re.search(r'\b(?:work|pro)\b|tac vu phuc tap|chuyen sau|deep thinking', value))
    has_limit = bool(re.search(r'gioi han|han muc|het luot|limit|quota|dat gioi han|vuot qua', value))
    if has_work_pro and has_limit:
        return True
    # 2. Gợi ý chuyển sang chế độ Chat / Nhanh
    has_chat_switch = bool(re.search(r'chuyen sang|switch to|dung che do|tiep tuc voi|che do nhanh|che do chat|chat thuong', value))
    if has_chat_switch and has_limit:
        return True
    # 3. Thông báo giới hạn sử dụng chung của Dola (usage_limit_reached)
    if usage_limit_reached(text):
        return True
    return False


_VN_MONTHS = {'mot': 1, 'gieng': 1, 'hai': 2, 'ba': 3, 'tu': 4, 'bon': 4, 'nam': 5, 'sau': 6, 'bay': 7,
              'tam': 8, 'chin': 9, 'muoi': 10, 'muoi mot': 11, 'muoi hai': 12, 'chap': 12}
_EN_MONTHS = {name: i for i, name in enumerate(
    ('january', 'february', 'march', 'april', 'may', 'june', 'july', 'august',
     'september', 'october', 'november', 'december'), 1)}
_EN_MONTHS.update({name[:3]: i for name, i in list(_EN_MONTHS.items())})


def _hour(hour, marker):
    hour = int(hour)
    if marker in ('pm', 'ch') and hour < 12:
        return hour + 12
    if marker in ('am', 'sa') and hour == 12:
        return 0
    return hour


def retry_after(text):
    """Thời điểm (epoch) Dola cho dùng lại, đọc từ câu báo giới hạn; None khi câu không có giờ.
    Nhận '22:32, 14 tháng Mười, 2026 (GMT+07:00)', '22:32 14/10/2026', 'October 14, 2026 at 10:32 PM (GMT+07:00)'."""
    value = _plain(text)
    month_words = '|'.join(sorted(_VN_MONTHS, key=len, reverse=True))
    found = None
    match = re.search(r'(\d{1,2}):(\d{2})\s*(sa|ch|am|pm)?,?\s*(?:ngay\s+)?(\d{1,2})\s+thang\s+('
                      + month_words + r'|\d{1,2}),?\s*(?:nam\s+)?(\d{4})', value)
    if match:
        hour, minute, marker, day, month, year = match.groups()
        month = int(month) if month.isdigit() else _VN_MONTHS[month]
        found = (int(year), month, int(day), _hour(hour, marker), int(minute))
    if found is None:
        match = re.search(r'(\d{1,2}):(\d{2})\s*(sa|ch|am|pm)?,?\s*(?:ngay\s+)?(\d{1,2})[/-](\d{1,2})[/-](\d{4})', value)
        if match:
            hour, minute, marker, day, month, year = match.groups()
            found = (int(year), int(month), int(day), _hour(hour, marker), int(minute))
    if found is None:
        months = '|'.join(sorted(_EN_MONTHS, key=len, reverse=True))
        match = re.search(r'(\d{1,2}):(\d{2})\s*(am|pm)?,?\s*(?:on\s+)?(' + months + r')\.?\s+(\d{1,2}),?\s*(\d{4})', value)
        if match:
            hour, minute, marker, month, day, year = match.groups()
        else:
            match = re.search(r'(' + months + r')\.?\s+(\d{1,2}),?\s*(\d{4}),?\s*(?:at\s+)?(\d{1,2}):(\d{2})\s*(am|pm)?', value)
            if match:
                month, day, year, hour, minute, marker = match.groups()
        if match:
            found = (int(year), _EN_MONTHS[month], int(day), _hour(hour, marker), int(minute))
    if found is None:
        return None
    zone = re.search(r'(?:gmt|utc)\s*([+-])\s*(\d{1,2})(?::?(\d{2}))?', value)
    try:
        if zone:
            sign = 1 if zone.group(1) == '+' else -1
            offset = timedelta(hours=int(zone.group(2)), minutes=int(zone.group(3) or 0)) * sign
            return datetime(*found, tzinfo=timezone(offset)).timestamp()
        return datetime(*found).timestamp()  # không ghi múi giờ: giờ của máy
    except ValueError:
        return None


def insufficient_video_credits(text):
    value = _plain(text)
    credit = r'(?:diem(?: tin dung)?|tin dung|credits?)'
    if not re.search(credit, value):
        return False
    direct = (
        r'(?:khong du|thieu|het|can them)\s+(?:\d+\s+)?(?:diem|tin dung)',
        r'(?:diem(?: tin dung)?|tin dung).{0,35}(?:khong du|da het|con lai la 0)',
        r'(?:insufficient|not enough|out of|need more)\s+(?:video\s+)?credits?',
        r'(?:do not|don\W?t) have enough.{0,20}credits?',
        r'credits?.{0,25}(?:insufficient|exhausted|depleted)',
    )
    if any(re.search(pattern, value) for pattern in direct):
        return True
    required = re.search(r'(?:can(?: phai)?(?: dung| su dung| co| tieu ton)?|requires?|costs?|needs?)\s+(\d+(?:[.,]\d+)?)\s+(?:video\s+)?'+credit, value)
    remaining = re.search(r'(?:chi con|con lai|only have|have only|remaining[: ]+)\s*(\d+(?:[.,]\d+)?)', value)
    if remaining is None:
        remaining = re.search(r'(\d+(?:[.,]\d+)?)\s+(?:video\s+)?credits?\s+(?:left|remaining)', value)
    if required and remaining:
        return float(required[1].replace(',', '.')) > float(remaining[1].replace(',', '.'))
    if 'diem tin dung' in value and any(marker in value for marker in (
        'theo thong so hien tai', 'hay thay doi cac thong so', 'giam thoi luong',
    )):
        return True
    if 'credits' in value and any(marker in value for marker in (
        'change the parameters and try again', 'adjust the parameters and try again',
        'reduce the duration', 'with the current settings',
    )):
        return True
    return bool(required and any(marker in value for marker in (
        'hay thay doi cac thong so', 'giam thoi luong', 'change the parameters and try again',
        'adjust the parameters and try again', 'reduce the duration',
    )))
