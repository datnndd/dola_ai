# Studio Python — Dola Browser Edition

Ứng dụng Python/Windows có giao diện dựa trên mẫu 3 BIG Studio. Phiên bản này tích hợp điều khiển Dola bằng Playwright vào nút chạy task/batch; không cần tự viết adapter để dùng Dola.

## Mở và chạy

**Thử Chromium của 3 BIG:** Trong Quản lý tài khoản hoặc Cài đặt, bấm
**Dùng Chromium của 3 BIG**. Cả Mở/Đăng nhập và tác vụ video dùng cùng executable
Chromium đi kèm 3 BIG và thư mục profile riêng của tài khoản trong StudioPython.
Tùy chọn này tắt GPM nhưng giữ liên kết GPM để có thể bật lại. Với tài khoản trước
đây dùng GPM, cần đăng nhập Dola một lần trong profile Chromium riêng; không tự
chuyển cookie hoặc dữ liệu đăng nhập từ GPM. Bộ điều khiển vẫn là Playwright;
không thay bằng Patchright hoặc sao chép các cờ can thiệp nhận diện tự động hóa.
Đã kiểm thử Chromium 151.0.7922.34 lưu cookie/localStorage qua đóng/mở với trang
giả lập. Phiên video hiện nạp StudioRelay. Chưa xác nhận khả năng giữ phiên Dola thật.

**Bản sửa mới nhất chỉ có trong mã Python, chưa build lại EXE.** Trong VS Code chạy `run_studio.py`; file này dùng môi trường Python đã chuẩn bị trong workspace nếu Python hiện tại thiếu thư viện. Mã giao diện chính vẫn là `studio.py`, có thể chạy trực tiếp bằng interpreter đủ thư viện.

## GPM Login API v3 (bản mới)

Mở GPM Login trước. Trong tool vào **Cài đặt → GPM Login — cấu hình API v3** hoặc **Quản lý tài khoản → GPM Login — API / liên kết profile**. Nhập port API của bạn (hoặc `http://127.0.0.1:PORT`), bật dùng GPM, bấm **Test API / tải profile**, rồi lưu. Port mặc định trong tool để trống; không tự ghi đè bằng port kiểm thử. Nhập URL có `/api/v1` cũng được chuẩn hóa thành `/api/v3`.

- **Liên kết** chọn profile GPM có sẵn; tài khoản mới liên kết chưa tự được chọn chạy. **GPM ID** ở từng tài khoản cho phép gắn ID vào tài khoản cookie đang có.
- Tài khoản chưa liên kết sẽ tạo profile GPM riêng khi mở lần đầu, lưu ánh xạ ID trong `data/gpm_links`. Không chép thư mục profile Chromium cũ sang GPM. Dùng cookie đã nhập hoặc đăng nhập thủ công lại trên profile GPM.
- Cookie nhập, proxy riêng, tên hiển thị Dola và điều phối nhiều tài khoản tiếp tục hoạt động. Tên profile GPM cũng cập nhật theo tên Dola sau khi xác nhận được.
- GPM mở trình duyệt; Python kết nối qua địa chỉ CDP mà API trả về. Phiên tạo/lấy video nạp StudioRelay; phiên đăng nhập/kiểm tra không nạp extension. GPM hiện dùng cửa sổ hiển thị; tùy chọn ẩn Chromium không áp dụng cho GPM.
- Đóng profile trong GPM trước khi chạy. Nếu API trả profile đang mở sẵn, tool không tiếp quản/đóng cửa sổ đó. Kết thúc tác vụ chỉ gọi đóng profile do phiên tool vừa mở; không xóa profile.
- Khi lần tạo profile có kết quả không rõ, tool dừng tạo trùng; kiểm tra danh sách GPM rồi dùng **GPM ID** gắn profile đã tạo vào tài khoản.
- Bỏ chọn dùng GPM nếu cần quay về Chromium trực tiếp. Các hướng dẫn Chrome/Chromium bên dưới áp dụng cho chế độ trực tiếp.

Đã kiểm thử API thật trên GPM v4.3.6 cài tại máy, API v3: list/create/update/start/close, kết nối CDP, extension bật/tắt và giữ nguyên profile mở sẵn. Chi tiết response: `docs/guides/GPM_API_V3_TEST.md`. Không cam kết GPM giúp Google chấp nhận đăng nhập hoặc tránh CAPTCHA.

1. Mở **StudioPython.exe**.
2. Công cụ tự liên kết các profile trong bộ cài 3 BIG Studio ở lần nâng cấp đầu tiên. Chọn tài khoản muốn chạy.
3. Nhập prompt, chọn thời lượng/tỷ lệ, thêm ảnh tham chiếu nếu cần.
4. Giữ bộ xử lý **Dola AI — Tự động trình duyệt** và bật **Hiện cửa sổ trình duyệt khi chạy** nếu muốn quan sát.
5. Bấm **Chạy Task hiện tại** hoặc **Chạy tất cả tác vụ**. Video hoàn tất sẽ xuất hiện trong thư viện.

## Tài khoản và trình duyệt

Trong **Quản lý tài khoản** có các nút:

- **Liên kết từ bản gốc**: liên kết thư mục profile đã đăng nhập, không xuất mật khẩu/cookie ra cấu hình.
- **Profile…**: chọn thư mục user-data-dir riêng của tài khoản.
- **Mở/Đăng nhập**: mở Chrome/Edge đã cài trên máy bằng tiến trình thường, không Playwright, không extension, với đúng thư mục profile của tài khoản. Bạn tự đăng nhập Dola/Google; đóng toàn bộ cửa sổ của profile rồi bấm Kiểm tra. Google có thể từ chối đăng nhập trong trình duyệt tự động. Tool không tự nhập mật khẩu/OTP hoặc xác nhận đăng nhập thành công. Phiên Dola cần được kiểm tra lại trong trình duyệt tạo video; việc dùng cùng thư mục giữa các bản trình duyệt không bảo đảm phiên đăng nhập luôn tương thích.
- **Kiểm tra**: mở Dola trong profile và kiểm tra giao diện đăng nhập.
- **Thêm**: tạo mục tài khoản với profile riêng nằm trong data/profiles.

Mỗi profile chỉ được dùng bởi một tác vụ tại một thời điểm. Nếu Chrome của công cụ gốc đang dùng cùng tài khoản, cần đóng cửa sổ đó trước khi chạy bên này.

**Xóa phiên + GPM** gọi API v3 `/profiles/delete/{id}` cho đúng profile đã liên kết
(kể cả liên kết được lưu trong `data/gpm_links`). Tool xác nhận profile không còn
trong danh sách GPM rồi mới bỏ phiên và file liên kết cục bộ. API lỗi thì giữ phiên
để thử lại. Không xóa khi phiên đang chạy tác vụ/đăng nhập; không tự đóng cưỡng bức
trình duyệt. API v3 gửi `mode=1` (đã kiểm thử trên GPM tại máy). Tài khoản
chưa liên kết GPM chỉ bị bỏ khỏi danh sách; không xóa thư mục Chromium gốc.

Nút Chạy dùng các tài khoản đang được tích chọn. Chọn một tài khoản thì mọi task chạy bằng tài khoản đó; chọn nhiều tài khoản thì tool xáo trộn ngẫu nhiên thứ tự và cấp các tài khoản rảnh cho task. Nếu ít task hơn tài khoản, chỉ một nhóm ngẫu nhiên được sử dụng. Số task chạy song song bằng số nhỏ nhất giữa số task, số tài khoản đã chọn và số luồng trong Cài đặt. Mỗi tài khoản chỉ chạy một task tại một thời điểm.

Trình duyệt được tự tìm từ Chromium của bộ cài 3 BIG Studio hoặc Chrome/Edge trên máy. Có thể chọn lại executable trong **Cài đặt**. Bản EXE có sẵn Python và driver Playwright, nhưng dùng trình duyệt và FFmpeg trên máy.

## Đăng nhập cookie Google và proxy

Trong **Quản lý tài khoản → Đăng nhập cookie Google**, dán cookie của những tài khoản bạn quản lý, mỗi dòng một tài khoản:

```text
SID=gia_tri_tai_khoan_1; HSID=gia_tri_1; SSID=gia_tri_1
SID=gia_tri_tai_khoan_2; HSID=gia_tri_2; SSID=gia_tri_2
```

Đây chỉ là ví dụ định dạng, không phải bộ cookie đăng nhập đầy đủ. Tool cũng nhận các đối tượng JSON nối bằng dấu phẩy **`{...},{...}` không cần ngoặc vuông**, mảng cookie JSON hoặc đối tượng `{"cookies":[...]}` trên một dòng. Mỗi dòng luôn là toàn bộ bộ cookie của một tài khoản; không tách từng cookie thành tài khoản riêng. Định dạng JSON giữ thuộc tính hostOnly, miền/path/hạn dùng; bản xuất có Google, YouTube và google.co.id như mẫu được hỗ trợ. Chuỗi Cookie không mang thuộc tính miền/path/hạn dùng; tool mặc định Google `.google.com`, cookie `__Host-` dùng `accounts.google.com`. Cookie hết hạn, thiếu thành phần hoặc bị Google từ chối không được tự coi là đăng nhập thành công.

- **Import & đăng nhập ngay**: tạo profile riêng cho từng dòng, nhập cookie, mở Dola và bấm đăng nhập Google. Số phiên mở đồng thời theo số luồng trong Cài đặt. Chọn tài khoản Google, chấp thuận hoặc xác minh bổ sung do bạn thao tác thủ công.
- **Lưu — đăng nhập khi chạy video**: lưu profile và cookie mã hóa, thực hiện đăng nhập lúc task dùng tài khoản này bắt đầu.
- Chỉ khi tên trong menu tài khoản của Dola hiện ra, tool đặt tên phiên theo tên đó và ghi **Phiên hợp lệ**. Không coi avatar Google hoặc ô chat khách là bằng chứng đăng nhập Dola. Tên trùng nhau vẫn có ID/profile riêng.
- **Cookie ↻**: thử đăng nhập lại profile cookie đã lưu. **Dừng đăng nhập** dừng các phiên kiểm tra/đăng nhập đang theo dõi.
- **Proxy** ở từng tài khoản: HTTP/HTTPS với user/mật khẩu tùy chọn, hoặc SOCKS5 không xác thực. Proxy ở hộp import áp dụng cho các profile vừa thêm; có thể chỉnh từng profile sau đó. Để trống máy chủ để tắt. Không tự đổi/luân phiên proxy khi bị từ chối.
- Proxy được dùng cho cả đăng nhập cookie, kiểm tra và tạo video. Khi mở Chrome thường, proxy có xác thực có thể hỏi lại user/mật khẩu trong trình duyệt.

Cookie nguồn nằm trong `data/cookie_vault/*.dpapi`; mật khẩu proxy cũng mã hóa bằng Windows DPAPI. Chỉ tài khoản Windows hiện tại giải mã được; cookie không ghi vào log hoặc workspace dạng rõ. Sau import, trình duyệt còn lưu phiên trong profile riêng theo cơ chế của Chromium. Phiên đăng nhập cookie luôn hiện cửa sổ để xử lý thủ công; StudioRelay chỉ nạp trong phiên tạo/lấy video. Nếu Google báo trình duyệt không được hỗ trợ, dùng **Mở/Đăng nhập** để thử đăng nhập thủ công bằng Chrome thường. Cookie không bảo đảm bỏ qua yêu cầu xác minh của Google.

## Nhịp thao tác trình duyệt

Prompt được dán qua sự kiện paste của chính ô nhập, không dùng clipboard Windows. Khoảng nghỉ giữa thao tác được chọn ngẫu nhiên từ 0.6 đến 1.4 lần nhịp cơ sở trong Cài đặt; mặc định là 1.8–4.2 giây. Mỗi batch dùng cấu hình tại lúc bắt đầu.

Trước khi bấm, tool chờ phần tử hiện, cuộn vào vùng nhìn thấy và kiểm tra nút đã
ổn định/bật/không bị che. Nút Gửi được bấm một lần; nếu không xác định được duy nhất
một nút, dùng Enter như giao diện cũ, không thực hiện cả hai. Kết nối GPM qua CDP
được thử lại với khoảng nghỉ tăng dần trong tối đa khoảng 45 giây, không gọi mở
profile lặp lại. Nút Dừng ngắt được khoảng nghỉ kết nối.

Trước khi gửi prompt, tải trang lỗi mạng tạm thời hoặc HTTP 5xx được thử lại tối
đa hai lần, nghỉ 2 rồi 4 giây. HTTP 403/429 không bị tải lại liên tục. Nếu trình
duyệt báo mất mạng, tool đợi tối đa hai phút, không tự gửi lại task hoặc tải lại
hội thoại đang tạo video. Kiểm tra này không nhận biết mọi lỗi mạng/proxy.

Khi nhận diện được cửa sổ xác minh/CAPTCHA, tool ngừng thao tác và giữ trình duyệt tối đa 10 phút để bạn xử lý thủ công; tiếp tục khi cửa sổ xác minh đóng. Thời gian này không tính vào thời gian chờ video. Nếu đang ẩn trình duyệt, tool báo cần bật cửa sổ. Nhận diện phụ thuộc giao diện dịch vụ; giảm tốc độ không bảo đảm tránh CAPTCHA và tool không tự giải xác minh.

## Cách Dola được điều khiển

### Nguồn video không logo

Trong **Cài đặt → Nguồn video Dola**, mặc định chọn **Nguồn không logo (chờ metadata)**.
Tool đọc response JSON/SSE đã tải xong của phiên Dola, gồm `video_model` và URL
base64, chọn nguồn được đánh dấu không watermark và đối chiếu với hội thoại/video
đang mở. Chỉ tải khi trình phát có video sẵn sàng. Không tự sửa URL có chữ ký,
không nhập module PYD của 3 BIG Studio, không giải mã token CDN riêng của bản gốc.
Nếu dịch vụ chỉ trả nguồn thường hoặc dữ liệu mã hóa chưa hỗ trợ, tool sẽ chờ rồi
báo thiếu nguồn khi hết timeout; không báo thành công bằng bản tải thường.

Sau khi tải, FFmpeg giải mã kiểm tra toàn bộ video. Khi bật 1080p, bước xuất dùng
Lanczos và tăng nét bằng unsharp. Bản gốc được giữ tại `*.download.mp4`; file JSON
ghi loại nguồn, bằng chứng metadata và đường dẫn bản gốc, không ghi URL có chữ ký.
Khi tắt nâng cấp và chỉnh vùng, bản xuất giữ nguyên dữ liệu file gốc. Nhãn không
watermark dựa trên metadata dịch vụ, không phải kết quả nhận diện logo trong hình.

Có thể chọn **Tải thông thường (có thể có logo)** để dùng nút tải và nguồn trình
phát như trước. Dùng **Lấy kết quả cũ** để thử lấy lại video trong cùng hội thoại.
Tính năng chỉ có trong mã Python; chạy `run_studio.py`, chưa build lại EXE.

### FFmpeg — cắt / làm mờ vùng logo

Vào **Cài đặt → FFmpeg — cắt / làm mờ vùng logo**. Mặc định **Không chỉnh vùng logo**. Có hai tùy chọn: **Cắt mép video** (phần trăm cắt ở trên/dưới/trái/phải) hoặc **Làm mờ vùng chữ nhật** (X/Y/rộng/cao theo phần trăm kích thước gốc, từ góc trái trên). Các thông số ban đầu chỉ là ví dụ, không tự nhận diện vị trí logo. Bấm **Thử xuất video có sẵn…** để chọn MP4 và xem bản xuất trong thư viện mà không tạo yêu cầu Dola mới. Đóng hộp thoại sẽ hủy lượt xuất thử đang chạy. Bấm **Lưu cho batch tiếp theo** để áp dụng tự động sau khi tải video Dola.

FFmpeg xử lý trực tiếp file tải về, giữ âm thanh và tốc độ phát; không quay màn hình. Chỉ quay lại hoặc mã hóa lại không loại được logo đã nằm trong hình. Cắt mất nội dung ở mép; làm mờ để lại một vùng mờ, không khôi phục hình bị che. Khi bật 1080p, video sau cắt được scale và thêm viền theo tỷ lệ đã chọn. Tắt 1080p để giữ kích thước sau cắt (làm tròn số chẵn cho H.264).

Khi bật cắt/làm mờ cho Dola, video gốc được giữ dưới tên `*.download.mp4` cạnh bản xuất, không hiển thị trùng trong thư viện; đường dẫn cũng ghi trong metadata `.json`. Xuất thử luôn tạo file `ffmpeg_*.mp4` mới và giữ nguyên file được chọn. Hủy/lỗi sẽ dọn file xuất dở. Tính năng này không tự áp dụng cho slideshow cục bộ hoặc adapter bên ngoài.

Tool mở profile → vào Dola → chọn Pro hoặc Nhanh → tải ảnh nếu có → nhập prompt và thông số → gửi yêu cầu → lưu hội thoại → đợi video sẵn sàng → chọn nguồn theo Cài đặt → tải và kiểm tra/xuất MP4 bằng FFmpeg.

Mode 1 và 3 dùng Pro. Mode 2 chọn Nhanh và công cụ Tạo video. Model/thời lượng/tỷ lệ được gửi như yêu cầu trong prompt; Dola có thể không đáp ứng mọi tổ hợp hoặc yêu cầu tài khoản đủ quyền. Tên preset theo mẫu không bảo đảm miễn phí, không giới hạn, 30 giây, hoặc video gốc 1080p. Tùy chọn 1080p là nâng độ phân giải bằng Lanczos. Tool không sửa URL để thay luồng video hoặc loại bỏ watermark của nhà cung cấp.

Bản này dùng Playwright thông thường, không tải mô-đun Python biên dịch của 3 BIG Studio, không giả lập bản quyền hoặc cơ chế cập nhật của bản gốc. Phiên tạo/lấy video nạp StudioRelay.

## StudioRelay trong phiên video

Phiên tạo/lấy video nạp StudioRelay từ `extensions/StudioRelay` và chờ xác nhận service worker. Phiên đăng nhập/kiểm tra không nạp extension. Đóng cửa sổ GPM cũ rồi chạy lại để áp dụng.

## Hủy, lỗi và lấy lại kết quả

- **Auto Web**: khi Dola xác nhận hết lượt Work trước khi tạo video, tool khóa Work của profile đến giờ mở lại, giữ nguyên job và lease rồi chuyển sang profile còn lượt, kể cả khi tắt "Tự chạy lại task lỗi". Nếu mọi profile đều khóa Work, Auto chờ giờ mở lại. Lỗi sau khi gửi mà chưa rõ Dola đã nhận hay chưa sẽ không tự gửi lại để tránh tạo video trùng.
- Hủy dừng phần điều khiển cục bộ; yêu cầu Dola đã nhận có thể vẫn tiếp tục chạy.
- **Chạy Task hiện tại / Chạy tất cả tác vụ** luôn tạo yêu cầu mới bằng tài khoản đang chọn, kể cả khi task có hội thoại từ lần trước. Liên kết cũ được giữ trong lịch sử của task trong workspace.
- **Lấy kết quả cũ (tài khoản gốc)** lấy video từ hội thoại đã lưu của task hiện tại, không gửi prompt mới. Thao tác này tự dùng tài khoản đã tạo hội thoại, kể cả khi ô chọn tài khoản đó đang tắt; tài khoản gốc phải còn trong danh sách.
- Chỉnh sửa prompt sẽ bỏ liên kết hội thoại cũ để tạo yêu cầu mới. Nhân bản task cũng tạo tác vụ mới.
- **Mở hội thoại của Task** mở đúng profile tài khoản đã tạo hội thoại.
- Không tự vượt CAPTCHA, giới hạn tài khoản, credit hay chính sách của Dola. Nếu Dola báo từ chối, mở hội thoại để xem chi tiết.

## Các chức năng khác

Hàng đợi nhiều task; prompt/ảnh riêng; tối đa 10 ảnh/task; 1–8 luồng; tiến độ và nhật ký; thư viện có ảnh thu nhỏ, mở video/thư mục, sao chép đường dẫn, ẩn/hiện lại; tự lưu cấu hình và hàng đợi. Chế độ **Xuất video từ ảnh (cục bộ)** vẫn hoạt động và tạo slideshow, không phải video AI. Adapter Python cho dịch vụ khác vẫn là tùy chọn mở rộng, xem `docs/guides/ADAPTER.md`.

Dữ liệu được lưu trong `data/workspace.json`. Video mặc định nằm trong `downloads/`. FFmpeg được tìm trên PATH hoặc trong bộ cài 3 BIG Studio; có thể chọn lại trong Cài đặt. Giữ thư mục ứng dụng ở nơi có quyền ghi.

## Chạy mã nguồn

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe studio.py
```

Đóng gói:

```powershell
python -m pip install pyinstaller
python -m PyInstaller --noconfirm --onefile --windowed --name StudioPython --collect-all customtkinter --collect-all playwright studio.py
```

Mã chính: studio.py (giao diện, hàng đợi), dola_browser.py (Dola), browser_runtime.py (chọn Patchright/Playwright), engine.py (FFmpeg và adapter tùy chọn).

## Phạm vi kiểm thử

Đã kiểm tra luồng đọc profile Dola hiện có, chọn Pro, gửi prompt, lưu hội thoại, lấy video từ cuộc trò chuyện, xuất 1080p, khóa profile, khởi tạo giao diện và cài đặt, lưu hàng đợi, chạy batch cục bộ, hủy tiến trình và thư viện. Chi tiết kết quả tạo mới bằng mã Python được ghi trong `docs/guides/KIEM_THU_PYTHON.md`. Các chế độ/model khác và upload nhiều ảnh cần đối chiếu thêm theo khả năng tài khoản thực tế.

Tham chiếu: [Playwright persistent profiles](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-persistent-context), [CustomTkinter](https://customtkinter.tomschimansky.com/documentation/), [FFmpeg](https://ffmpeg.org/ffmpeg-filters.html).

Trong Quản lý tài khoản có ô chọn xóa riêng cho từng phiên, **Chọn tất cả**, **Bỏ chọn** và **Xóa các phiên đã chọn**. Các phiên được xóa lần lượt; lỗi một phiên không dừng các phiên còn lại. Lựa chọn xóa không thay đổi lựa chọn tài khoản chạy task. API lỗi hiển thị mã như `INVALID_MODE` hoặc `PROFILE_NOT_FOUND`.

## Nhập prompt

Prompt được dán qua sự kiện paste của trang, không dùng clipboard Windows; nếu phiên bản editor không xử lý sự kiện này thì dùng fallback nội bộ của Playwright. Sau khi dán, tool chờ ngẫu nhiên trước khi chuyển đến nút Gửi; thời gian nghỉ trước/sau click, hover và giữ chuột cũng ngẫu nhiên.

Trong giao diện có mục **Chế độ Dola: Pro/Fast**. Fast chọn Nhanh và bỏ qua bước chọn Pro; Pro luôn mở bộ chọn và chọn Pro trước khi gửi.

## Kiểm tra session

Lần đầu một tài khoản được dùng trong batch, tool kiểm tra session sau khi mở trang. Khi xác nhận thành công, dấu thời gian được lưu vào tài khoản và các task tiếp theo dùng lại phiên đó, không kiểm tra lại. Nút **Kiểm tra** thủ công sẽ xác nhận lại và cập nhật dấu thời gian; nút **Mở/Đăng nhập** sẽ xóa dấu cũ để buộc kiểm tra lại sau lần đăng nhập mới.

Nếu Dola hiện lại màn hình đăng nhập trong lúc task đang chạy, tool sẽ import lại cookie vào đúng profile, tải lại Dola và thử khôi phục tối đa 3 lần. Khi đã gửi prompt, tool không gửi lại prompt để tránh tạo task trùng; nếu khôi phục thất bại, task dừng và báo lỗi.

Nút **Kiểm tra** cũng dùng cơ chế này: tài khoản cookie bị đăng xuất sẽ được import lại, tải lại Dola và xác nhận lại tối đa 3 lần trước khi báo phiên lỗi.

## Mở Dola khi khởi chạy GPM

Tool gửi `startup_urls=https://www.dola.com/chat` qua API cập nhật profile và truyền URL vào `addination_args` khi mở. Trường `startup_urls` được tài liệu Global mô tả; bản legacy v3 có thể bỏ qua hoặc từ chối. Vì vậy sau khi kết nối CDP, tool chọn tab Dola có sẵn hoặc tự mở Dola nếu chưa có, trước khi chờ extension/kiểm tra tài khoản. Không cần tự nhập địa chỉ.

Mở/Đăng nhập cũng kết nối CDP tạm thời để mở đúng trang rồi ngắt kết nối điều khiển, giữ cửa sổ để bạn đăng nhập thủ công. Khi mở hội thoại cũ, tab được chuyển tới đúng URL hội thoại.

Tham chiếu trường `startup_urls`: https://gpmlogin.com/doc (Global; không coi là xác nhận hỗ trợ trên mọi bản legacy v3).

## Chạy trực tiếp bằng Chromium của 3 BIG

Tùy chọn Chromium đi kèm 3 BIG dùng `use_gpm=false`, `use_3big_browser=true`. Bấm Chạy để mở Dola bằng profile riêng đã lưu, không cần bấm Đăng nhập/Kiểm tra trước. `skip_login_precheck=true` bỏ bước tự import cookie và xác thực trước mỗi task. Nếu Dola hiện yêu cầu đăng nhập, tool dùng cookie đã lưu để khôi phục; nếu không có cookie thì chờ bạn đăng nhập ngay trong cửa sổ task, tối đa 10 phút và có thể hủy. StudioRelay vẫn được nạp khi tạo/lấy video.

Đây là Chromium đi kèm 3 BIG; không dùng profile Google Chrome cá nhân. Dữ liệu GPM cũ được giữ; phiên GPM không tự chuyển sang profile Chromium.

## Đăng nhập tài khoản mới và proxy HTTP

Trong Quản lý tài khoản bấm **Đăng nhập**, nhập proxy tùy chọn theo dạng `host:port` hoặc `host:port:user:pass`. Chrome mở Dola bằng profile mới. Hoàn tất đăng nhập Google/Dola trong cửa sổ này; tool liên tục kiểm tra tên Dola và ô soạn thảo. Khi tên ổn định, tool đóng trình duyệt và lưu profile theo tên tài khoản. Chưa nhận được tên thì chưa thêm vào danh sách; đóng trình duyệt hoặc bấm **Dừng đăng nhập** để hủy và dọn thư mục profile mới. Nếu Chrome còn khóa file, nhật ký báo chưa dọn được thư mục để xử lý sau.

Khi thêm tài khoản mới (kể cả bằng cookie Facebook), lần tải Dola đầu có thể thử lại tối đa 5 lần nếu kết nối tạm thời bị ngắt (nghỉ 2, 4, 4, 4, 4 giây). Tool không tự thử lại thao tác đăng nhập Facebook.

Proxy từng tài khoản có thể đổi bằng nút **Proxy**; cùng proxy được dùng cho đăng nhập và chạy video. Mật khẩu HTTP proxy được mã hóa bằng DPAPI. Màn hình thiết lập Chrome lần đầu được bỏ qua; Google vẫn có thể yêu cầu đăng nhập/xác minh.

**Mở/Đăng nhập** trên tài khoản đã lưu chỉ mở đúng profile và trang Dola để bạn thao tác. Tool không đọc tên, kiểm tra session hay tự import cookie trong thao tác này; cửa sổ được giữ đến khi bạn đóng hoặc bấm Dừng. **Kiểm tra** là thao tác riêng. Nút **Đăng nhập** để thêm tài khoản mới vẫn chờ tên Dola rồi lưu profile.

Nút **Proxy** của mỗi tài khoản dùng một ô duy nhất, hiển thị đầy đủ và nhận `host:port` hoặc `host:port:user:pass`, giống hộp thêm tài khoản. Proxy cũ tự điền lại; để trống và bấm Lưu proxy để tắt. Thay đổi chỉ áp dụng cho tài khoản đang chỉnh.

Các nút GPM được ẩn mặc định. Trong Cài đặt bật **Hiện tùy chọn GPM (nâng cao)** để truy cập lại cấu hình; mở lại Quản lý tài khoản để hiện nút GPM ID.

## Proxy HTTP và tốc độ Google

Ô proxy trong **Đăng nhập** được nhớ khi bấm **Lưu proxy** hoặc **Mở Dola để đăng nhập**, kể cả lần đăng nhập đó không thành công. Mở lại tool sẽ điền sẵn giá trị; để trống rồi lưu để bỏ proxy mặc định. Mật khẩu vẫn được mã hóa DPAPI trên đĩa.

Mặc định hiện dùng **GPM Login API v3** cho đăng nhập, kiểm tra phiên và chạy video. Khi mở phiên bản này lần đầu, cấu hình cũ được chuyển sang GPM và hiện lại các nút GPM; giữ port, proxy, tài khoản và các liên kết profile đã lưu. Port có thể sửa trong **Cài đặt → GPM Login** hoặc **Đăng nhập → Cấu hình GPM / port**.

**Đăng nhập** tạo profile GPM mới với proxy đã nhập, chờ tên và ô soạn thảo Dola, đổi tên profile GPM theo tên nhận được rồi lưu tài khoản cùng GPM ID. Hủy khi chưa nhận được tên sẽ dọn profile GPM mới; nếu API chưa xóa được, nhật ký ghi ID để xử lý. **Mở/Đăng nhập** trên tài khoản cũ chỉ mở GPM/Dola để bạn thao tác, không tự nhận tên hay kiểm tra session.

Ô proxy giữ nguyên dạng một dòng `host:port` hoặc `host:port:user:pass`. Tool chuyển thành `raw_proxy` của GPM khi tạo/mở profile; sửa proxy hoặc để trống để tắt sẽ được áp dụng trước lần mở tiếp theo. Mật khẩu lưu trên đĩa vẫn mã hóa. Profile Chromium cục bộ không tự chuyển session sang GPM: liên kết một GPM ID đã đăng nhập hoặc đăng nhập trong profile GPM mới. Tùy chọn Chromium/Patchright của 3 BIG vẫn có trong Cài đặt khi cần chuyển lại.

Google có thể từ chối trình duyệt tự động, phiên bản cũ, extension hoặc kết nối proxy. Chế độ thủ công không bảo đảm đăng nhập thành công; xem https://support.google.com/accounts/answer/7675428. Không tự thay đổi proxy khi chuyển từ đăng nhập sang chạy tác vụ.

Ô proxy và mật khẩu proxy hiển thị ký tự rõ để dễ chỉnh sửa; dữ liệu mật khẩu lưu trên đĩa vẫn mã hóa DPAPI. Chrome trực tiếp dùng HTTP/1.1 khi có proxy HTTP/HTTPS (`--disable-http2`, `--disable-quic`). Với proxy được cung cấp, Google từng timeout 18 giây; chỉ tắt QUIC vẫn timeout, chuyển HTTP/1.1 tải trang khoảng 1 giây trong lượt kiểm tra. Google Search vẫn có thể chuyển sang trang xác minh. Đóng cửa sổ Chrome của tool rồi mở lại để áp dụng cờ kết nối mới.
## Cập nhật Chrome trực tiếp — 27/09/2026

Chạy `run_studio.py`. Ứng dụng hiện dùng Google Chrome cài trên máy, profile riêng từng tài khoản; không cần mở GPM hoặc nhập API/port. Cấu hình cũ được chuyển khi mở app; tài khoản GPM được cấp đường dẫn Chrome riêng và cần đăng nhập lại, dữ liệu GPM gốc không bị xóa. Các nút GPM và Facebook qua GPM không còn hiển thị. StudioRelay không tự nạp trong Chrome thường.

Trong Quản lý tài khoản, **Đóng Chrome đăng nhập** dừng và đóng những phiên đăng nhập do tool quản lý. Phiên tạo video tự đóng khi hoàn tất, lỗi hoặc hủy. Chrome cá nhân ngoài tool không bị đóng. Bỏ tài khoản khỏi danh sách vẫn giữ thư mục profile trên máy. Các phần mô tả GPM bên dưới là lịch sử chức năng cũ.
