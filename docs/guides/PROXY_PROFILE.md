# Proxy theo vòng đời profile Chrome

Khi mở profile, tool lấy proxy qua bộ cấu hình hiện có. Proxy HTTP(S) có
user/password và SOCKS5 được nối qua một bridge HTTP riêng trên 127.0.0.1.
Chrome nhận địa chỉ bridge qua tham số `proxy` của `launch_persistent_context`;
thông tin xác thực upstream chỉ nằm trong bộ nhớ Python. HTTP không xác thực
có thể được truyền trực tiếp. Tắt proxy vẫn dùng mạng máy.

Bridge và lease key tồn tại cùng Chrome. Các tab dùng chung bridge đó; khi
Chrome đóng hoặc mở thất bại, tool đóng bridge và nhả key. Không ghi proxy
vĩnh viễn vào profile và không cần API GPM/Hidemium.

IP ra ngoài do proxy upstream quyết định. Bridge không tự tạo IP mới. Quy tắc
lấy/xoay upstream hiện tại vẫn được giữ; proxy mới được cấp cho lần mở profile
tiếp theo. Không thay upstream đang chạy giữa phiên.

Đây là phần tái hiện cơ chế quan sát được của Seedance Studio (lease + bridge
khi cần + tham số proxy lúc mở Chrome), không phải bản sao mã biên dịch gốc.

Đã thử bằng Chrome thật và upstream giả lập cục bộ: HTTP xác thực, SOCKS5 xác
thực/remote DNS, CONNECT tunnel, HTTP qua SOCKS, đổi upstream khi mở lại,
từ chối sai mật khẩu, và dọn bridge khi mở thất bại/đóng Chrome. Đã kiểm tra
lại việc nhả key và nhiều chat. Chưa đo IP Internet bằng proxy thật.

Chạy kiểm tra: `python -m unittest test_profile_proxy -v`.
Không build EXE. Sao lưu trước sửa: `backup_proxy_bridge_20261030_170758`.
