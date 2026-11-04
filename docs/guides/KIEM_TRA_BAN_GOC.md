# Kiểm tra cơ chế tự động hóa của 3 BIG Studio

## Đã xác nhận trực tiếp từ bộ cài trên máy

- Bộ cài đang có phiên bản ứng dụng 2.7.29.
- Có gói `patchright-1.62.3.dist-info`. Metadata ghi Patchright là biến thể Python của Playwright.
- Mô-đun `browser.pyd` tham chiếu `patchright.async_api`, `async_playwright`, `chromium`, `launch_persistent_context` và `headless`.
- Mô-đun `pro_generator.pyd` tham chiếu `patchright.async_api`, `async_playwright`, `show_browser`, `headless` và thư mục trình duyệt `ms-playwright`.
- Mô-đun `video_worker.pyd` cũng tham chiếu `patchright.async_api`.
- Giao diện có tùy chọn “Hiện cửa sổ trình duyệt khi chạy” và địa chỉ Dola `https://www.dola.com/chat`.

Kết luận: bản gốc có cơ chế điều khiển Chromium bằng Patchright/Playwright. Không cần giả định phải có API công khai mới xây được luồng tạo video. Chưa có bằng chứng để khẳng định toàn bộ thao tác tạo video chỉ đi qua UI hoặc chỉ đi qua API.

## Kiểm thử động ngày 25/09/2026

Đã thao tác dựa trên ảnh toàn cửa sổ do người dùng cung cấp, nhập một prompt mới vào task trống, bật hiện trình duyệt và bấm Chạy tất cả. Chỉ một task được chạy.

Prompt: “Một chú mèo cam đi dạo trong khu vườn lúc bình minh, ánh sáng tự nhiên, máy quay chuyển động chậm, phong cách điện ảnh.”

Các mốc xác nhận từ nhật ký ứng dụng (giờ máy, UTC+7):

| Thời gian | Hành vi quan sát được |
| --- | --- |
| 13:51:11 | Bắt đầu batch một task, cấp phát độc quyền một tài khoản |
| 13:51:13–14 | Khởi chạy `patchright/driver/node.exe`, sau đó Chromium đi kèm bộ cài |
| 13:51:17 | Điều hướng tới `https://www.dola.com/chat` |
| 13:51:35–37 | Mở menu từ chế độ Nhanh, chọn “Mô hình Pro nâng cao” |
| 13:51:41 | Ứng dụng báo prompt đã được gửi thành công |
| 13:51:45 | Lấy được mã hội thoại |
| 13:51:50 | Ứng dụng báo GPU task đã bắt đầu render |
| 13:54:02 | Vẫn đang chờ render, bộ đếm 131 giây |
| 13:55:42 | Nhật ký báo render xong sau 230 giây và bắt đầu tải video |
| 13:55:43 | Tải xong MP4 gốc, 17.118.805 byte; bắt đầu Lanczos + Unsharp |
| 13:55:58 | Xuất xong bản 1080p, 14.848.487 byte |
| 13:55:59 | Batch hoàn tất, 1/1 task thành công |

Tiến trình Chromium được xác nhận dùng `--remote-debugging-pipe`, không có cờ `--headless`, và `--user-data-dir` trỏ tới thư mục tài khoản bên trong bộ cài. Cửa sổ có tiêu đề “Mèo cam bình minh - Dola - Google Chrome for Testing”. Đây là bằng chứng động rằng prompt thử đã tạo hội thoại trên Dola.

Các tên “Pro Trick / 0 Credit” là cách bản gốc ghi nhật ký; thử nghiệm này chưa xác minh cách tính phí, quyền hạn hoặc tên model sinh video thực tế phía dịch vụ. Không coi nhãn đó là chứng minh việc tạo video miễn phí hay không giới hạn.

Hạn chế quan sát: chụp cửa sổ bằng công cụ Windows vẫn báo `SetIsBorderRequired / 0x80004002`; bộ đọc cửa sổ Chromium cũng gặp lỗi ánh xạ định danh Chrome. Thông tin kiểm thử được đối chiếu bằng nhật ký Copy của ứng dụng, cây tiến trình, tiêu đề cửa sổ và thư mục đầu ra.

## Kết quả đã kiểm chứng

Lượt thử hoàn thành sau khoảng 4 phút 48 giây. Đã kiểm tra file MP4 thật bằng FFmpeg: 15,07 giây, H.264, 1080 × 1920, 24 fps, âm thanh AAC stereo 32 kHz, 14.848.487 byte. Giải mã toàn bộ 361 khung hình không lỗi. Kiểm tra khung hình tại giây thứ 3 cho thấy mèo cam trong vườn hoa dưới ánh nắng, phù hợp prompt. Bản sao video được lưu ở `../Video_thu_meo_cam_1080p.mp4`.

Kết quả này được tạo bằng **3 BIG Studio gốc**, không phải bằng bản StudioPython.exe mới. Thông tin này mô tả lượt thử bản gốc. Bản Python hiện đã tích hợp luồng điều khiển Dola; xem KIEM_THU_PYTHON.md.

## Hướng triển khai bản Python tiếp theo

Thay phần adapter chung bằng lớp điều khiển trình duyệt có profile riêng từng tài khoản, khởi chạy Chromium hiển thị/ẩn, thao tác trên các phần tử đã quan sát được của Dola, chờ trạng thái thành công/thất bại và tải kết quả. Cần kiểm chứng selector và luồng thực tế; không tự đánh dấu thành công, không dùng slideshow thay cho kết quả AI. Phần điều khiển trình duyệt đã được triển khai trong lần cập nhật tiếp theo; xem KIEM_THU_PYTHON.md.
