# 🚀 Hướng Dẫn Sử Dụng: IMAP Migration & Checker Tool

`imap_migrate.py` là một công cụ mạnh mẽ được viết bằng Python, thiết kế để tự động hóa quy trình kiểm tra và chuyển đổi (migration) hàng loạt tài khoản email. Công cụ này là lớp giao diện (wrapper) chuyên nghiệp cho phần mềm `imapsync`, hỗ trợ quản lý qua file CSV và cấu hình tập trung.

---

## 🌟 Tính Năng Nổi Bật

- **Hai chế độ vận hành**:
    - `Check Mode`: Kiểm tra đăng nhập thần tốc cho cả máy chủ Nguồn và Đích.
    - `Sync Mode`: Chạy đồng bộ dữ liệu email sử dụng bộ engine `imapsync`.
- **Đa luồng (Parallel Processing)**: Hỗ trợ xử lý song song nhiều tài khoản cùng lúc để tiết kiệm thời gian.
- **Xác thực Admin Proxy (SASL PLAIN)**: Cho phép dùng tài quản Admin để đăng nhập thay cho người dùng (hỗ trợ Zimbra, Google Workspace, v.v.).
- **Tối ưu hóa cho Gmail**: Tích hợp sẵn bộ flag tối ưu để migrate từ Gmail (tự động loại bỏ thư mục hệ thống như Important, Starred, v.v.).
- **Giao diện hiện đại**: Sử dụng ANSI Colors và Icons giúp dễ dàng theo dõi trạng thái.
- **Hệ thống Logs chuyên sâu**: Tự động phân tách log tổng quát và log chi tiết cho từng tài khoản.

---

## 📋 Yêu Cầu Hệ Thống

1.  **Python 3.6+**: Không cần cài thêm thư viện ngoài (sử dụng thư viện chuẩn).
2.  **imapsync**: Cần được cài đặt sẵn trên hệ thống để sử dụng tính năng `--sync`.
3.  **Hệ điều hành**: Linux, macOS hoặc Windows (khuyên dùng Terminal hỗ trợ ANSI colors).

---

## ⚙️ Cấu Hình (config.py)

Mọi cấu hình hệ thống hiện được tách riêng ra file `config.py` để đảm bảo an toàn và dễ quản lý.

### Các bước khởi tạo cấu hình:
1. Copy file mẫu: `cp config.py.example config.py`
2. Chỉnh sửa `config.py` với các thông số của bạn:

| Thông số | Ý nghĩa | Ghi chú |
| :--- | :--- | :--- |
| `host1`, `port1` | Địa chỉ & Port máy chủ Nguồn | Mặc định 993 (SSL) |
| `ssl1` | Sử dụng SSL/TLS cho máy nguồn | `True` hoặc `False` |
| `authuser1` | Email Admin máy nguồn | Để trống nếu không dùng Proxy Auth |
| `gmail1` | Chế độ Gmail máy nguồn | `1` để bật bộ flag tối ưu, `0` để tắt |
| `host2`, `port2` | Địa chỉ & Port máy chủ Đích | |
| `ssl2` | Sử dụng SSL/TLS cho máy đích | |
| `authuser2` | Email Admin máy đích | Thường dùng cho Destination là Zimbra/Kerio |
| `imapsync_bin` | Đường dẫn lệnh `imapsync` | VD: `"imapsync"` hoặc `"/usr/local/bin/imapsync"` |
| `max_workers` | Số luồng mặc định | Kiểm soát tải cho máy chủ |

> [!TIP]
> File `config.py` đã được thêm vào `.gitignore` để tránh việc vô tình lộ mật khẩu lên server Git.

---

## 🗂️ Định dạng File CSV

File danh sách tài khoản phải có định dạng CSV (không yêu cầu header, dòng comment bắt đầu bằng `#` sẽ được bỏ qua).

**Cấu trúc 4 cột bắt buộc:**
`email_source`, `password_source`, `email_dest`, `password_dest`

> [!IMPORTANT]
> **Lưu ý về Mật khẩu khi dùng Proxy Auth (authuser):**
> Nếu bạn điền `authuser1` hoặc `authuser2` trong CONFIG, cột mật khẩu tương ứng trong CSV **PHẢI** là mật khẩu của tài khoản Admin đó, không phải của người dùng cuối.

**Ví dụ `accounts.csv`:**
```csv
# Nguồn, Pass_Nguồn/Admin, Đích, Pass_Đích/Admin
user1@gmail.com, admin_secret_pass, user1@company.com, admin_secret_pass
user2@gmail.com, admin_secret_pass, user2@company.com, admin_secret_pass
```

---

## 🚀 Cách Sử Dụng

Cú pháp lệnh:
```bash
python3 imap_migrate.py <file_csv> <mode> [options]
```

### 1. Chế độ Kiểm tra (--check)
Dùng để test thử mật khẩu và kết nối trước khi migrate.
```bash
python3 imap_migrate.py accounts.csv --check --workers 5
```

### 2. Chế độ Đồng bộ (--sync)
Thực hiện chạy `imapsync` thực tế.
```bash
python3 imap_migrate.py accounts.csv --sync --workers 3
```

### Các tham số bổ trợ:
- `-w`, `--workers`: Số lượng tài khoản chạy song song (mặc định lấy từ `config.py`).
- `--dry-run`: (Chỉ cho `--sync`) In ra các câu lệnh `imapsync` sẽ chạy mà không thực thi thật. Giúp kiểm tra cấu hình flags.

---

## ⚠️ Giải Thích Kỹ Thuật

### 🛡️ Cơ chế Proxy Authentication
Khi `authuser` được thiết lập, công cụ sử dụng cơ chế **SASL PLAIN**. Nó sẽ "đăng nhập bằng user A, nhưng xác thực bằng quyền của Admin B".
- Điều này cực kỳ hữu ích khi bạn không có mật khẩu của từng người dùng cuối.
- Yêu cầu máy chủ IMAP phải hỗ trợ quyền Master User hoặc Admin Proxy.

### 📧 Chế độ Gmail (`gmail1: 1`)
Khi bật chế độ này, công cụ sẽ tự động thêm các flags tối ưu cho Gmail (tự động bỏ qua các thư mục hệ thống gây trùng lặp và khớp thư mục thông minh).

---

## 📝 Hệ Thống Logs

Toàn bộ quá trình được lưu lại trong thư mục `logs/` (cấu hình trong `log_dir`):

1.  **Log Tổng hợp (`tool_YYYYMMDD_HHMMSS.log`)**: Ghi lại diễn biến chính của toàn bộ phiên làm việc.
2.  **Log Chi tiết từng tài khoản**: Trong chế độ `--sync`, mỗi tài khoản sẽ có một file log riêng chứa toàn bộ output debug của lệnh `imapsync`.

---

## 📊 Trạng Thái Kết Thúc (Exit Codes)
- `0`: Tất cả tài khoản xử lý thành công.
- `1`: Có ít nhất một tài khoản bị lỗi hoặc gặp lỗi hệ thống.

---

## 📞 Hỗ trợ & Liên hệ Chuyên nghiệp

> [!IMPORTANT]
> Nếu bạn cần sự giúp đỡ từ chuyên gia hoặc các dịch vụ email doanh nghiệp quy mô lớn, tôi cung cấp các gói dịch vụ sau:
> - **Setup Email Chuyên Nghiệp**: Tư vấn và triển khai hệ thống email cho doanh nghiệp (Zimbra, Google Workspace, Microsoft 365, Mail Server riêng).
> - **Migrate Dữ Liệu Email**: Di chuyển dữ liệu email an toàn, không gián đoạn dịch vụ với số lượng tài khoản lớn.
> - **Cung cấp Máy Chủ Email**: Giải pháp máy chủ email riêng hiệu năng cao, bảo mật và ổn định.

**Thông tin liên hệ:**
- **Bùi Mạnh**
- **Telegram:** [@manhbt93](https://t.me/manhbt93)
- **Gmail:** [buitrongmanh@gmail.com](mailto:buitrongmanh@gmail.com) (Phản hồi trong vòng 1h)
