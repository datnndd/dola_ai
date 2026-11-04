# Chạy Auto qua web

Trong Cài đặt, chọn thư mục lưu video, nhập **API key của web** (`ss_…`, tạo ở trang Tài khoản → API key) và **Mã máy**. Tool dùng API v1 của web tại `https://view.botlc.pro/api/v1/jobs` (web chuẩn, đẩy lên từ thư mục `Desktop\web`; đi qua Cloudflare nên tool gửi User-Agent `StudioPython/1.0`), key gửi trong header `X-API-Key` (không nằm trên URL).

Tool chỉ **nhận job và trả kết quả**. Mọi việc quản lý (chạy lại, hủy, tạm dừng, xem lỗi) làm trên web.

- **Nhận job**: `POST /api/v1/jobs/claim` với `type: video`, `same_row: true`, `limit` = số tab đang rảnh, `worker` = mã máy. Không có job thì chờ rồi hỏi lại.
- **Gia hạn**: mỗi 2 phút tool gửi heartbeat cho các job đang chạy. Nếu web báo job đã bị hủy / xóa dòng / giao cho máy khác (`LEASE_LOST`…), tool dừng task đó ngay.
- **Thời lượng**: job video có `duration` (`15s`/`30s`) theo dòng trên web; tool chạy đúng thời lượng đó (15s không dùng chế độ skill30). Web cũ chưa gửi `duration` thì dùng cấu hình trong Cài đặt.
- Video lưu tại `<thư mục đã chọn>/<STT>/<job_id>.mp4`. File cũ cùng tên được giữ trong `_previous`.

## Báo kết quả (`POST /api/v1/jobs/report`, gộp tối đa 50 mục)

| Kết quả trên tool | Báo lên web | Trên web |
|---|---|---|
| Xong | `done` + `folder`, `path` | Ghi Folder, tạo lượt đăng nếu bật tự động |
| Lỗi của job (vi phạm chính sách, hết thời gian, lỗi tải video…) | `failed` + `error`, `retry: false` | Job chuyển sang **Lỗi**, bấm **Chạy lại** trên web để chạy lại |
| Tool dừng (bấm Dừng/Tắt Auto khi job chưa chạy, Chrome/profile lỗi, tài khoản hết điểm, đóng tool giữa chừng) | `release` | Job quay lại hàng đợi, không tính lượt thử |

Job lỗi / trả lại / bị web từ chối được **xóa khỏi bảng** của tool ngay sau khi web nhận báo cáo. Job xong vẫn giữ trong bảng với trạng thái `✓ Hoàn thành • đã báo API`.

- Báo cáo chưa gửi được (mất mạng) nằm trong `data/auto_jobs_v2.json`; tool ngừng nhận thêm job tới khi gửi được. Gửi lại an toàn nhờ `lease` (web trả `duplicate`). Mở Auto lại để gửi báo cáo còn chờ sau khi thoát ứng dụng.
- Web từ chối hẳn (job đã hủy, đã giao máy khác…) thì tool bỏ báo cáo, không gửi lại; video đã làm vẫn giữ trên máy.
- Lỗi mạng / web bận (429, 5xx) khi nhận job: Auto vẫn bật, thử lại sau 15 giây. Key sai (401): Auto dừng nhận job.
- Báo cáo cũ (nhận trước khi đổi sang API v1, không có lease) vẫn được gửi qua cổng cũ `/api/jobs`.

Tài khoản hết điểm thì khóa đến 00:00 như trước và job được trả lại web; Chrome lỗi thì bỏ tài khoản đó đến hết lượt Auto.
