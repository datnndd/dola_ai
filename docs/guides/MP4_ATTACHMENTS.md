MP4 attachment support — 09/10/2026
- Nhận link .mp4/.MP4, tên file trong download/title và thẻ file có nút Download.
- Link tải không có đuôi vẫn được nhận nếu thẻ ghi tên MP4.
- Có file thì không gửi thêm câu xác nhận tạo video.
- Tải qua URL hoặc sự kiện download của trình duyệt; kiểm tra MP4 trước khi xử lý FFmpeg.
- Không nhận file người dùng đính kèm trong ô nhập; bỏ tệp đã có trước lúc gửi prompt.
- Chế độ API preview cũng nhận MP4 đính kèm; ghi provenance watermark unknown, không khẳng định file sạch logo.
- Chế độ no_logo nghiêm ngặt vẫn yêu cầu nguồn không logo như cấu hình.
