# Kết nối dịch vụ AI

Adapter là một script Python do bạn cung cấp. Chọn file `.py` và `python.exe` trong **Cài đặt**, thêm tên tài khoản trong **Quản lý**, sau đó chọn **AI qua adapter Python** ở khối chạy tác vụ.

Ứng dụng chạy `[python.exe, adapter.py]` bằng subprocess, không dùng shell. Chỉ chọn mã mà bạn tin cậy. Python của adapter có thể dùng môi trường ảo riêng với SDK của nhà cung cấp.

## Đầu vào

Đọc một JSON UTF-8 từ stdin đến EOF:

```json
{
  "protocol": 1,
  "task": {
    "id": "id-duy-nhat",
    "name": "Task #1",
    "prompt": "Mô tả video",
    "images": ["C:/Images/photo.png"],
    "status": "Chờ xử lý"
  },
  "settings": {
    "mode": 1,
    "model": "Seedance 2.5",
    "duration": "15",
    "ratio": "9:16",
    "upscale": true,
    "output": "C:/VideoOutput",
    "concurrency": 2,
    "engine": "adapter",
    "ffmpeg": "C:/Tools/ffmpeg.exe",
    "adapter": "C:/Adapters/provider.py",
    "python": "C:/Python/python.exe",
    "timeout": 1800
  },
  "account": {"id": "id-tai-khoan", "name": "ten-goi-nho"}
}
```

Không có cookie/session tự động đi kèm. Adapter tự lấy thông tin xác thực hợp lệ từ biến môi trường hoặc kho bảo mật của nhà cung cấp; dùng `account.id`/`account.name` để chọn tài khoản tương ứng. Không ghi thông tin bí mật vào stdout/stderr vì stderr có thể hiện trong nhật ký lỗi.

## Đầu ra

Khi hoàn thành, tải video xuống ổ đĩa, đóng tệp rồi trả **một JSON duy nhất** trên stdout, mã thoát 0:

```json
{"video_path": "C:/VideoOutput/result.mp4"}
```

`video_path` phải tuyệt đối, có đuôi `.mp4`, tồn tại và không rỗng. Nếu nằm ngoài thư mục xuất, ứng dụng sao chép sang thư mục xuất với tên riêng. Không viết log tiến độ lên stdout. Lỗi có thể trả JSON `{"error": "Mô tả lỗi"}` với mã thoát 0, hoặc kết thúc với mã khác 0 và mô tả trên stderr.

Batch giới hạn đồng thời theo cả số luồng và số tài khoản đã chọn. Mỗi tài khoản chỉ xử lý một task tại một thời điểm; tài khoản được đưa về cuối hàng đợi sau mỗi task. Ứng dụng không tự retry lời gọi có thể bị tính phí.

Hủy task hoặc timeout sẽ dừng cây tiến trình adapter cục bộ. Nếu nhà cung cấp đã nhận yêu cầu, việc dừng local **không bảo đảm** hủy công việc từ xa; adapter cần thiết kế riêng chức năng hủy tại nhà cung cấp.
