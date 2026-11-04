# Theo dõi và tải video qua API hội thoại

Chạy mã nguồn bằng `python run_studio.py`. Chưa build EXE.

Luồng mặc định đọc `/im/chain/batch_single` và dự phòng `/im/chain/single`
ngay trong tab Dola đang đăng nhập, bằng POST đọc lịch sử (cmd 3101/3100).
Không dùng nút Share để kết luận hoàn tất, không F5 liên tục và không gửi lại
prompt chỉ vì tạm thời chưa lấy được file.

Đọc `has_video_gen`, câu hỏi xác nhận, mã lỗi, trạng thái công cụ và kết quả.
Hỗ trợ cả `creation_block` truyền thống lẫn `file_block` MP4 của Agent Hub
đang được Dola trả về. Chỉ đọc phản hồi của Dola trong lượt hiện tại; không
thực thi nội dung lệnh, file thao tác hoặc thinking từ hội thoại.

Ưu tiên nguồn không watermark qua fallback API và `video_model.video_list`;
giải mã `main_url`, chọn bitrate cao. Nếu không tải được nguồn ưu tiên, dùng
`download_url`/tệp MP4 do Dola trả về và thông báo có thể có logo, như luồng gốc.
Chế độ người dùng chọn riêng «Nguồn không logo» vẫn nghiêm ngặt: nếu không có
bằng chứng nguồn sạch thì báo lỗi tải, không tự đổi sang nguồn không rõ logo.

Tải bằng aiohttp trong luồng I/O riêng, có proxy truyền từ chính phiên Chrome,
không dựa vào cấu hình APIRequestContext của kết nối CDP. File ghi theo khối
vào `.part`, kiểm tra đủ dữ liệu/MP4, rồi mới đổi tên. Có timeout kết nối/đọc,
hủy task, xóa file tải dở và kiểm tra/xuất bằng FFmpeg như pipeline hiện có.
Không ghi thông tin xác thực proxy vào cấu hình task lưu trên đĩa.

Auto chỉ thử lại lỗi trước khi gửi. Khi đã bắt đầu click Gửi hoặc đang phục
hồi hội thoại cũ, lỗi đọc/tải được báo về; không tự tạo video lần nữa. Dùng
«Lấy video» để đọc lại đúng hội thoại nếu CDN/API tạm lỗi.

Đã kiểm tra parser, trạng thái, dự phòng hai API, thứ tự nguồn, giữ proxy,
tải thiếu dữ liệu, file sai định dạng, hủy tải, dọn `.part`, chế độ không logo
và chặn tự gửi lại sau lỗi tải: `python -m unittest test_seedance_video -v`.
Kiểm tra Qt và dùng lại tab có extension cũng đã qua.

Kiểm thử thật bằng `recover_dog_cat_api.py`: đọc lại hội thoại chó mèo
38418452609922833, tự tải qua proxy và xuất MP4 720×1280 thành công; 0 prompt mới.
Kết quả lưu trong `dog_cat_api_recovery.json` và `downloads/dog_cat_api_test`.
Không có thao tác lấy thủ công từ preview trong lượt kiểm thử này.

Sao lưu trước sửa: `backup_api_download_20261030_180611`.
