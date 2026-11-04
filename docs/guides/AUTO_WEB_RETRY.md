# Auto Web — giữ job và chạy lại tại tool (09/10/2026)

Giao diện Qt hiện tại được giữ nguyên. Tick **Tự chạy lại task lỗi ở cuối hàng đợi** áp dụng ngay cho Auto Web.

- Job lỗi giữ nguyên dòng, ID và lease. Không báo fail rồi claim lại một job mới.
- Không tick: giữ lỗi trên bảng, tiếp tục heartbeat; không claim thêm để thay thế job lỗi. Các job đã nhận vẫn được xử lý.
- Có tick: đưa job lỗi vào lượt thử kế tiếp, đổi tài khoản nếu có tài khoản khác khả dụng; giữ cùng job và chỉ báo done khi có file video. Mỗi lượt thử có timeout riêng.
- Chrome có task thành công hoặc lỗi đều ngừng nhận task; đợi các tab đang chạy kết thúc rồi đóng. Luồng chỉ được cấp task mới sau khi phiên Chrome cũ đã đóng xong. Không giữ Chrome rảnh để chờ job. Profile die không được dùng lại trong lượt Auto.
- **Dừng**: hủy đang chạy, trả job chưa xong qua status release. **Tắt Auto**: ngừng nhận mới, trả job chờ/lỗi và chờ các job đang chạy báo kết quả. Dòng task vẫn giữ trên bảng.
- Đóng ứng dụng khi Auto đang chạy: chờ hủy và gửi báo cáo rồi mới đóng. Nếu mất mạng, nhật ký `data/auto_jobs_v2.json` giữ báo cáo; bật Auto lại để gửi tiếp. Không thể bảo đảm gửi ngay khi web/mạng đang mất kết nối.
- Job bị web hủy hoặc mất lease: dừng, không chạy lại hoặc gửi kết quả đè lên lượt được giao cho máy khác.
- Sau khi đã release, không chạy lại lease cũ từ dòng lịch sử. Bật Auto để web cấp lượt mới hợp lệ.

## Web hiện tại và kế hoạch mở rộng tùy chọn

Đã đối chiếu `C:/Users/ad/Desktop/web/src/services/jobs.js`: claim, heartbeat, report và release đã đủ hỗ trợ. **Không cần cập nhật server để chạy bản này.** Khi tool giữ lỗi để chờ tick, trên web job vẫn ở trạng thái running vì lease vẫn thuộc máy này.

Nếu muốn web hiển thị rõ việc chờ/chạy lại, có thể bổ sung sau:

1. Heartbeat nhận `phase: rendering | waiting_retry` và `local_attempt`, `last_error` (giới hạn độ dài, kiểm tra đúng lease).
2. Lưu các trường tiến độ, hiển thị “Chờ chạy lại tại máy …” ở bảng web; không chuyển về queued, không trừ phí thêm cho mỗi lượt thử cục bộ.
3. Giữ nguyên complete/release idempotent; kiểm thử hai máy, mất mạng, reset/hủy job và heartbeat với lease cũ.

Chưa sửa mã hoặc deploy web. Các kiểm thử tool dùng API và renderer giả lập, không claim job thật hoặc tạo video mất phí.
