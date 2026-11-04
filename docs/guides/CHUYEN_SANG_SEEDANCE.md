# Chrome và thao tác theo Seedance Studio — bản Qt hiện tại

Chạy: `python run_studio.py` trong thư mục toolnew.
Chỉ sửa mã nguồn, không build EXE. Các EXE/RAR cũ không được cập nhật.
Giữ nguyên thư mục `browser/chromium-1243` cạnh mã nguồn/EXE khi di chuyển.
Chrome for Testing 153.0.8010.12 được chép từ Seedance Studio 1.9.17.

## Cài đặt

Trong Cài đặt → CHROME SEEDANCE STUDIO:
- Cơ bản · Form tạo video: chọn chế độ, Create Video, model, tỷ lệ và thời lượng trên web;
  gắn ảnh theo thứ tự, nhập mô tả sau khi lọc chỉ dẫn/thông số như extension gốc, rồi gửi một lần.
- Pro · Chat thường: chọn Pro, gắn ảnh và gửi prompt định dạng giống extension Pro:
  tỷ lệ, thời lượng, model, tạo video trực tiếp và human artifact.
- Tự động: Pro 5/10/15 giây dùng chat; Fast hoặc 30 giây dùng form.
- Chế độ cũ «30 giây (SKILL.md)» được chuyển sang form video 30 giây, không tải file skill.

Không gửi lời chào trước task. Đọc lại prompt để bảo đảm nhập đủ. Mỗi ảnh chỉ
được gắn một lần và chờ bằng chứng đính kèm/upload hoàn tất trước ảnh tiếp theo.
Thông số không có trên form sẽ dừng trước khi gửi, không tự đổi thông số.
30 giây vẫn cần Dola/extension cung cấp lựa chọn tương ứng; không kèm payload
cấu hình do backend Seedance cấp và không đảm bảo Dola tạo được 30 giây.

## Tài khoản và proxy

Không cần chạy GPM/Hidemium hoặc nhập API GPM.
Lúc nạp cấu hình, tài khoản liên kết GPM được cấp đường dẫn Chrome riêng dưới
`data/chrome_profiles`. Giữ thông tin cũ trong `legacy_gpm`; không xóa/chuyển
thư mục GPM và không sao chép cookie đã mã hóa của GPM.
Cookie đã nhập và snapshot phiên cũ vẫn được giữ; nếu không khôi phục được,
đăng nhập lại trong Chrome của tài khoản.
ProxyNo1, ProxyVN và proxy tay vẫn dùng bộ chọn proxy hiện có. Mỗi Chrome giữ
một proxy, các chat dùng chung; nhả key khi Chrome đóng. Cửa sổ đăng nhập luôn hiện.

## Nhiều chat

Tối đa 5 chat/tài khoản theo cài đặt, thao tác độc lập giống thay đổi ở bản 1.9.17.
Một Chrome sở hữu profile/proxy; mỗi chat có kết nối Patchright/CDP riêng để
không dùng chéo Page/Context giữa thread. Lỗi riêng một chat không đóng chat khác.
Chỉ owner đóng Chrome; hủy worker trước khi chờ chúng kết thúc.
Theo dõi hội thoại và tải/xuất video tiếp tục dùng pipeline hiện tại của bản Qt.
Chỉ trả lời câu hỏi thời lượng một lần; không tự gửi lại prompt chỉ vì trả về chữ.
CAPTCHA vẫn chờ người dùng xử lý thủ công.

## Kiểm tra và phạm vi

`python -m unittest test_seedance -v`
Đã kiểm tra bằng trang mô phỏng/profile tạm: chọn form, chặn thời lượng không có,
ảnh đúng thứ tự, nhập đủ prompt, gửi một lần, 3 chat đồng thời (một chat lỗi),
hủy worker, chuyển profile, nhả proxy, nạp cấu hình cũ và lưu Cài đặt Qt.
Chưa gửi yêu cầu tạo video lên Dola thật.

Đối chiếu HUONG_DAN/CAP_NHAT, JavaScript extension Pro và chuỗi đọc được từ các
module biên dịch của Seedance Studio; đây là phần tái hiện hành vi quan sát được,
không phải bản sao toàn bộ backend/extension gốc. Không sửa license của tool.

Nguồn trước sửa: `backup_seedance_20261030_164719`.
File nguồn sửa: studio_qt.py, studio_core.py, dola_browser.py, dola_tabs.py, auto_jobs.py.
File mới: seedance_browser.py, seedance_ui.py, seedance_parallel.py, test_seedance.py và tài liệu này.

## Cập nhật định dạng prompt

`seedance_prompt.py` chuyển hàm `formatDolaPrompt` của extension Pro sang Python.
Pro thay tỷ lệ/thời lượng cũ và tránh lặp chỉ dẫn tạo video/human artifact.
Form video (gồm 30 giây) lọc tiền tố tạo video, chỉ dẫn chat, model, tỷ lệ và
thời lượng theo cùng quy tắc gốc. Nếu lọc xong không còn mô tả, dừng trước khi gửi.
Đã so sánh 42 trường hợp với JavaScript gốc, kết quả trùng khớp.
Kiểm tra: `python -m unittest test_seedance_prompt -v`.
Sao lưu trước thay đổi prompt: `backup_prompt_20261030_172203`.

## Nạp extension Dola30

Bản Dola30 kiểm thử nằm trong `extensions/dola30`. Nạp vào cả phiên đăng nhập
và phiên tạo video; tool kiểm tra service worker để xác nhận nạp thành công.
Tùy chọn trên giao diện: «Nạp extension Dola30 khi mở Chrome».
Cấu hình cũ bị tự tắt do thiếu StudioRelay được bật lại một lần; lựa chọn tắt
của người dùng sau cập nhật vẫn được lưu. Cần đóng và mở lại tool từ mã nguồn.

Đây là bản standalone đã tạo trước đó, không phải extension Pro. Vẫn thiếu
hai JSON skill-pack do backend Seedance gốc cấp; nạp extension không đồng nghĩa
đã mở đầy đủ khả năng tạo video 30 giây.
Kiểm tra: `python -m unittest test_extension_loading -v`.
Sao lưu trước thay đổi bộ nạp: `backup_extension_20261030_172900`.

## Fix tab reuse and extension overlay

The first task reuses the authenticated Dola tab by its CDP target ID. The owner
no longer navigates it to about:blank. The tab stays open for subsequent tasks.
The Dola30 media panel was moved away from the bottom-right Send button; its
host does not intercept clicks outside the panel. Checked in a real browser
with the extension loaded using test_tab_reuse.py and test_extension_loading.py.
