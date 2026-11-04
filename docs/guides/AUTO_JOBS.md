# Auto Web

Tài liệu hiện tại: xem [AUTO_WEB_RETRY.md](AUTO_WEB_RETRY.md).

API đang dùng: https://view.botlc.pro/api/v1/jobs, xác thực X-API-Key.
Nhận job bằng POST /claim; heartbeat giữ lease; báo kết quả qua POST /report.
Job lỗi giữ tại tool, tick Tự chạy lại để thử tiếp; Dừng trả job chưa xong về web.
Video lưu <thư mục đầu ra>/<STT>/<job_id>.mp4.
