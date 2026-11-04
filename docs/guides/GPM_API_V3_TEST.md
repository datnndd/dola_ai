# Kiểm thử GPM Login API v3 — 25/09/2026

Kiểm thử trực tiếp API GPM đang cài trên máy. Port 19995 đọc từ file `api_port.dat` chỉ được dùng cho kiểm thử; ô cấu hình của tool vẫn để người dùng nhập. Một profile riêng `StudioPython QA API ... checked` được tạo để thử và được giữ lại, ở trạng thái đóng. Không dùng cookie Google thật hoặc tạo video trong lượt kiểm thử GPM này.

## Response thực tế

| Thao tác | Endpoint v3 | Cấu trúc thực tế |
|---|---|---|
| Danh sách | GET `/profiles?page=1&per_page=1` | `success`, `data` là mảng, `pagination`, `message` |
| Tạo | POST `/profiles/create` với `profile_name` | `data.id`, `name`, `raw_proxy`, `profile_path`, `browser_type`, `browser_version`, `note`, `group_id`, `created_at` |
| Cập nhật | POST `/profiles/update/{id}` | `success: true`, `data: {}`, `message: OK`; đã đọc lại danh sách để xác minh tên và proxy đổi thật |
| Mở | GET `/profiles/start/{id}` | `data.profile_id`, `browser_location`, `remote_debugging_address`, `driver_path`, `process_id` |
| Đóng | GET `/profiles/close/{id}` | `success`, `data`, `message`; cần chờ tiến trình thoát hẳn |

Phân trang v3: `pagination.total`, `page`, `page_size`, `total_page`. Response khác tài liệu Global v1 đặt danh sách/phân trang chung trong `data`.

Tham số thêm cờ Chromium trên bản cài này là **`addination_args`**. Đã kiểm tra command line thật: `addition_args` và `additional_args` không được đưa vào lệnh mở. Dùng `addination_args` thì StudioRelay nạp thành công.

Gọi start lần hai khi profile đang chạy vẫn trả `success: true` và phiên hiện tại. Tool kiểm tra PID trước/sau để từ chối tiếp quản profile đã mở, tránh đóng nhầm phiên của người dùng.

## Kiểm thử đạt

- Kết nối Playwright với Chromium GPM 142 qua `remote_debugging_address`, đọc trang thử nghiệm cục bộ.
- Mở phiên không extension → mở phiên video có StudioRelay 1.7 → mở phiên không extension trên cùng profile.
- Thử tiếp quản profile đang mở bị từ chối, trang của phiên đó vẫn hoạt động.
- Cập nhật tên qua `profile_name`; cập nhật và đọc lại proxy thử nghiệm, sau đó xóa cấu hình proxy khỏi profile QA.
- GUI nhập port, Test API lấy danh sách thật, liên kết profile vào workspace thử nghiệm, ngăn liên kết trùng.
- Kiểm thử điều phối tài khoản hiện có vẫn đạt.

Chưa kiểm thử đăng nhập Google bằng cookie thật mới hoặc tạo video đầu-cuối qua GPM. Không đổi các cài đặt fingerprint của GPM qua API; dùng cấu hình mặc định/profile người dùng.

Nguồn đối chiếu: [mẫu API v3](https://github.com/buiducduy111/GPMLoginApiV3Sample), [tài liệu Global v1](https://gpmlogin.com/doc). Các khác biệt ở trên được xác nhận bằng API v3 trên máy, không chỉ thay chuỗi v1 thành v3.
