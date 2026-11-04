# Kiểm thử Studio Python — Dola Browser Edition

## Cập nhật xử lý FFmpeg

Đã kiểm thử bằng clip tổng hợp hai giây: tắt chỉnh vùng, cắt 25% mép dưới, làm mờ góc phải dưới; kiểm tra kích thước, sai khác pixel trong/ngoài vùng làm mờ, âm thanh AAC và thời lượng. Đã kiểm tra xuất 1080p từ clip không âm thanh, kiểm tra toàn bộ file giải mã không lỗi, từ chối tọa độ sai, giữ nguyên SHA-256 của bản gốc, lưu metadata đường dẫn gốc trong nhánh hoàn tất Dola. Kiểm tra hủy tiến trình đang mã hóa và lỗi đầu vào: không xuất file dở, không đổi bản gốc.

Giao diện dùng workspace tạm riêng: mở nút FFmpeg từ Cài đặt, chọn chế độ, lưu/đọc cấu hình, xuất thử qua worker và cập nhật giao diện (bộ xuất giả lập trong kiểm thử UI). Backend được kiểm thử bằng FFmpeg thật ở trên. Không dùng lượt tạo video Dola mới, không sửa dữ liệu tài khoản/hàng đợi thật, không build EXE.

## Cập nhật nhập prompt nhanh

Đã chuyển sang dán toàn bộ prompt qua sự kiện paste của trình soạn thảo Dola, không dùng clipboard chung Windows. Với Dola ở phiên khách trên trình duyệt thật, prompt thử 10.608 ký tự được chèn khoảng 1,5 giây (nghỉ thao tác cấu hình 0,2 giây), giữ chính xác tiếng Việt, emoji và dòng trống trong các đoạn văn. Đã xóa prompt sau kiểm thử, không gửi tạo video. Các kết quả gõ từng ký tự bên dưới là lịch sử kiểm thử của bản trước.

Ngày kiểm thử: 25/09/2026, Windows, Python 3.13, Playwright 1.63.0.

## Tạo video mới bằng mã Python

Đã chạy `render_dola` của ứng dụng với profile Dola đang đăng nhập, chế độ Pro, yêu cầu Seedance 2.5 / 15 giây / 9:16.

Prompt: “Một chú chó corgi chạy chậm qua đồng cỏ xanh lúc bình minh, ánh sáng tự nhiên, phong cách điện ảnh, không chữ.”

Mã Python tự mở Chromium, chọn Pro, gửi prompt, lấy được hội thoại mới, chờ video đính kèm, tải và xuất video 1080p thành công. Không dùng bộ sinh video, mô-đun .pyd hay EXE của 3 BIG Studio để tạo yêu cầu này.

File kiểm thử: `downloads/Kiem_thu_Python_Corgi_1080p.mp4`.

FFmpeg kiểm tra toàn bộ file: H.264, 1080 × 1920, 24 fps, thời lượng khoảng 15,09 giây, AAC stereo 44,1 kHz, 361 khung hình được giải mã không lỗi. Khung hình giây thứ 3 có chó trên đồng cỏ, phù hợp nội dung prompt. Video giữ logo Dola AI của nhà cung cấp. 1080p là bản nâng độ phân giải, không phải cam kết nguồn AI gốc 1080p.

## Kiểm thử qua giao diện và hàng đợi

Đã chạy nhánh Dola của nút batch trong GUI với task lấy lại hội thoại đã hoàn thành. Kết quả 1/1 thành công: cấp đúng tài khoản, mở hội thoại cũ, dùng nút tải xuống chính thức của Dola, lưu MP4, cập nhật trạng thái, lưu workspace và hiển thị thư viện. Lượt này không gửi prompt mới.

Đã kiểm tra thêm: tạo/nhân bản task và tách danh sách ảnh, chuyển preset, batch xuất ảnh cục bộ hai task đồng thời, metadata, lưu trạng thái, ẩn/hiện thư viện giữ nguyên file, hủy tiến trình, adapter tùy chọn trả lỗi thật, khóa profile và nhả khóa.

## Sửa điều phối tài khoản

Đã chạy kiểm thử offline qua hàm xử lý nút Chạy và bộ điều phối, thay thao tác tạo video bằng bộ giả lập để không gửi yêu cầu Dola mới:

- Task có hội thoại cũ của A vẫn tạo yêu cầu mới trên B khi chỉ chọn B.
- Nút lấy kết quả cũ dùng đúng A, độc lập với ô chọn B.
- Kiểm tra profile C không ngăn chạy tài khoản B đang chọn.
- Một task với ba tài khoản có thể được phân cho cả A/B/C qua các lần xáo trộn; hai task dùng hai tài khoản khác nhau.
- Bảy task, ba tài khoản, cấu hình hai luồng: hoàn tất đủ bảy task, tối đa hai task đồng thời và không có tài khoản nào bị dùng đồng thời cho hai task.

Các kiểm thử này xác nhận điều phối, chưa phải lượt tạo video thật đồng thời trên nhiều tài khoản.

## Tốc độ nhập và thao tác trình duyệt

Đã kiểm tra trên Chromium thật với trang HTML cục bộ và profile tạm: nhập tuần tự đúng tiếng Việt, nhiều dòng và emoji; độ trễ giữa ký tự; bấm chuột đúng một lần; không bấm khi màn hình xác minh giả lập đang hiện và tiếp tục sau khi nó đóng; hủy khoảng nghỉ trong dưới 0,6 giây; báo rõ khi cần xác minh mà trình duyệt đang ẩn. Không gửi yêu cầu Dola mới trong lượt kiểm thử này. Nhận diện CAPTCHA trên dịch vụ thực phụ thuộc giao diện hiện hành.

## Kiểm tra bản EXE

PyInstaller đã đóng gói Python và driver Playwright vào StudioPython.exe. Lệnh chẩn đoán offline trong chính EXE đã mở/đóng Chromium thành công: `ok=true`, `frozen=true`, FFmpeg được tìm thấy. Bản kiểm thử dùng Chromium 151.0.7922.34 hiện có trên máy.

## Tích hợp StudioRelay trong mã nguồn

Đã sao chép và đối chiếu SHA-256 của 44 file extension từ runtime 3 BIG Studio. Kiểm thử Chromium thật trên cùng profile tạm theo chuỗi: phiên thường → phiên video → phiên thường. Hai phiên thường không có extension service worker; phiên video có đúng một worker StudioRelay, phiên bản 1.7. Không truy cập Dola, không dùng tài khoản thật và không gửi prompt trong kiểm thử này. Các lần kiểm thử tạo video thật ghi ở trên diễn ra trước khi thêm extension. Chưa build EXE cho thay đổi này.

## Cookie Google và proxy (chỉ mã nguồn)

Đã kiểm thử chuỗi Cookie, JSON, dữ liệu lỗi, DPAPI mã hóa/giải mã, mật khẩu proxy không nằm dạng rõ trong cấu hình và proxy được truyền đến Chromium. Với Chromium thật và trang thử nghiệm cục bộ: hai bộ cookie giả được import vào hai profile riêng; nút đăng nhập và nút Google được bấm; chỉ tên trong menu tài khoản Dola mới được nhận là thành công; avatar Google và tên cuộc trò chuyện không tạo kết quả dương tính giả. GUI đã kiểm thử import hai dòng, hàng đợi đăng nhập, đổi tên phiên, mở hộp proxy và không lưu cookie rõ trong workspace.

Bộ chọn tên được kiểm tra đọc trên một tài khoản Dola thực đang có phiên đăng nhập. Chromium cũng đã kết nối thành công qua HTTP proxy có xác thực trên máy chủ thử nghiệm cục bộ (không chuyển tiếp ra Internet). Chưa thử xác thực Google bằng cookie thật mới hoặc kết nối proxy bên ngoài; kết quả còn phụ thuộc cookie, Google/Dola và proxy của người dùng. Không build lại EXE.

## Giới hạn dịch vụ

Luồng tạo mới đã kiểm thử với Pro/text-to-video. Mode 2, yêu cầu 30 giây và nhiều ảnh tham chiếu chưa được kiểm thử tạo video đầu-cuối trên dịch vụ. Các nhánh UI tương ứng đã triển khai nhưng model, thời lượng, quota, đăng nhập, CAPTCHA và khả năng xử lý ảnh phụ thuộc Dola/tài khoản. Không cam kết miễn phí, không giới hạn hoặc sạch logo như nhãn quảng cáo của công cụ gốc.

Nếu dịch vụ đổi giao diện, các selector tập trung ở `dola_browser.py` cần cập nhật. Chưa có cơ chế hủy render từ xa; hủy trong tool chỉ dừng phần điều khiển cục bộ và giữ hội thoại để lấy lại kết quả.
