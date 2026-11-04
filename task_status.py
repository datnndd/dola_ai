"""Task status → badge (label, colors) and failure checks. No UI toolkit imports."""
from studio_theme import (BORDER, MUTED, GREEN, CYAN, RED, WARNING,
                          SUCCESS_BG, ERROR_BG, WARNING_BG, ACTIVE_BG)

# A failed task = it ran but produced no video. Done tasks have a video; tasks
# not run yet (Chờ chạy) or still running are not failures.
FAILED_BADGES = {'Lỗi', 'Lỗi 3 lần', 'Lỗi tải video', 'Vi phạm chính sách',
                 'Bị chặn', 'Thiếu điểm', 'Hết giờ', 'Đã dừng'}

# The user can retry every terminal failure; active and successful tasks are excluded.
RETRYABLE_BADGES = set(FAILED_BADGES)


def is_failed_status(status):
    """True only for tasks that finished a run without a video (an error/stopped
    outcome). 'Xong', 'Chờ chạy' and running states are not failures."""
    return status_badge(status)[0] in FAILED_BADGES


def is_retryable_status(status):
    return status_badge(status)[0] in RETRYABLE_BADGES


def status_badge(status):
    value = status.casefold()
    if 'không đủ điểm tín dụng' in value:
        return 'Thiếu điểm', WARNING, WARNING_BG
    if 'hết thời gian task' in value:
        return 'Hết giờ', WARNING, WARNING_BG
    if value.startswith('vi phạm chính sách'):
        return 'Vi phạm chính sách', RED, ERROR_BG
    if value.startswith('lỗi 3 lần'):
        return 'Lỗi 3 lần', RED, ERROR_BG
    if value.startswith('lỗi tải video'):
        return 'Lỗi tải video', WARNING, WARNING_BG
    if 'bị chặn' in value:
        return 'Bị chặn', RED, ERROR_BG
    if 'lỗi' in value:
        return 'Lỗi', RED, ERROR_BG
    if 'hoàn thành' in value:
        return 'Xong', GREEN, SUCCESS_BG
    if 'hủy' in value or 'gián đoạn' in value:
        return 'Đã dừng', WARNING, WARNING_BG
    if value.startswith(('chờ xử lý', 'chờ chạy')):
        return 'Chờ chạy', MUTED, BORDER
    if 'ffmpeg' in value or 'xuất video' in value:
        return 'Xuất video', CYAN, ACTIVE_BG
    return 'Đang chạy', CYAN, ACTIVE_BG
