# Auto Sheet — tạo video

Mở **Cài đặt → Cài đặt Auto Sheet**. Tool đọc Google Sheets API v4 bằng file JSON service account, giống ContentStudio. Nếu còn cấu hình ContentStudio trên máy, link Sheet, tên tab và đường dẫn JSON được dùng làm mặc định. Khóa riêng không được sao chép vào cấu hình hoặc nhật ký. Chia sẻ Sheet với `client_email` quyền Editor.

- **Chạy Auto** giữ luồng API web hiện có.
- **Chạy Auto Sheet** bật luồng Sheet riêng. Hai nguồn không chạy cùng lúc.
- **Lấy nhiệm vụ từ Sheet** chỉ đọc một lượt tất cả dòng hợp lệ, tách F thành task 30s và G thành task 15s rồi lưu vào bảng để chạy thủ công. Không mở Chrome, không bật Auto, không trừ F/G và không ghi kết quả lên Sheet. Bấm lại bỏ qua nhiệm vụ cùng Sheet/tab/STT/prompt/thời lượng/thứ tự còn trong bảng. Vì đây là bản sao chỉ đọc, Auto Sheet vẫn có thể nhận số lượng đó nếu bật sau này.
- **Chạy đã chọn / tất cả** màu xanh; hai nút Auto màu tối.

## Cột và số lượng

Nhận từ trên xuống khi A có STT nguyên dương duy nhất, B có prompt và F hoặc G có số nguyên dương. F là 30s, G là 15s. Dòng có F=2, G=2 tạo bốn job riêng. Tool lưu các job xuống máy rồi xóa F/G trong một lần ghi có biên nhận. Chỉ cấp job mới vào tab khi còn chỗ. Các job của dòng đã nhận nhưng chưa chạy được giữ trong hàng đợi trên máy.

Ví dụ STT 20:

    <thư mục đầu ra>/20/30/20_30-1.mp4
    <thư mục đầu ra>/20/30/20_30-2.mp4
    <thư mục đầu ra>/20/15/20_15-1.mp4
    <thư mục đầu ra>/20/15/20_15-2.mp4

Thành công ghi đường dẫn thư mục máy tính vào L (30s) hoặc M (15s). Đây không phải tải video lên Google Drive. Nếu ô đã có thư mục khác, tool thêm dòng đường dẫn, không xóa đường dẫn cũ. Tên file tăng tiếp để tránh ghi đè.

Nếu bật **Trả lại số lượng sau khi từng video kết thúc lỗi**, mỗi video lỗi cộng đúng 1 về F/G tương ứng. Bao gồm lỗi chính sách, lỗi tải, hết giờ và các lỗi khác. Video đang chạy hoặc job chưa chạy không được trả sớm. Nếu còn bật Auto Sheet, số lượng này sẽ được nhận lại để chạy tiếp. Tắt nút Auto cho các video đang chạy hoàn tất; nút Dừng tất cả hủy tác vụ đang chạy.

## Chống ghi lặp

Một hàng đợi thực hiện lần lượt các API; lưu nhật ký `data/sheet_jobs_*.json`. Mỗi thay đổi F/G/L/M và biên nhận `studio_video_op` được Google áp dụng chung trong một `spreadsheets.batchUpdate`. Mất phản hồi thì đọc biên nhận để đối chiếu, không tự gửi lại phép cộng/xóa chưa rõ kết quả. Nếu chưa xác nhận được, tool dừng nhận thêm; giữ nguyên nhật ký để kiểm tra. Không xóa nhật ký trong khi có job chưa xử lý xong. Sau khi sắp xếp Sheet, tool tìm lại bằng STT và đối chiếu prompt; STT trùng hoặc prompt đổi sẽ bị chặn ghi.

Chỉ chạy **một máy nhận job trên cùng Sheet**. Khóa tiến trình dùng chung ngăn hai bản tool trên cùng tài khoản Windows chạy đồng thời, nhưng không khóa được máy khác hay thao tác sửa F/G thủ công đúng lúc đang ghi. Chạy nhiều máy cần dịch vụ claim có khóa phía máy chủ. Biên nhận dùng developer metadata của Sheet và chịu hạn mức lưu trữ Google.

Tham chiếu API: [batchUpdate](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/batchUpdate), [developer metadata](https://developers.google.com/workspace/sheets/api/guides/metadata).

## Nguồn video

Đã khôi phục mặc định **Bản StudioPython gốc: nguồn API → FFmpeg** theo thư mục `outputs/StudioPython`. Dùng fallback API lấy metadata và URL nguồn, tải MP4 rồi xuất H.264/AAC bằng FFmpeg, kiểm tra giải mã toàn bộ file. Không quét OCR và không tự ghi preview Chrome. StudioRelay giống bản tham khảo và chỉ được nạp vào phiên video khi bật extension. Nhãn nguồn không watermark do metadata/URL cung cấp, không phải xác nhận từ ảnh; logo đã có sẵn trong nguồn vẫn giữ nguyên.
