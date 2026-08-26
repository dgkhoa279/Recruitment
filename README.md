# Odoo 17 — Recruitment Management Module

> **Hệ thống Quản lý Tuyển dụng Nhân sự Tự động hóa & Tích hợp AI trên nền tảng Odoo 17.**

**Ngữ cảnh dự án**: Đây là dự án thực tế được hoàn thành trong kỳ thực tập doanh nghiệp của tôi, nhằm giải quyết bài toán tự động hóa quy trình tuyển dụng, phân quyền 3 cấp bảo mật và tích hợp các công nghệ hiện đại (AI CV Scanning, Facebook Graph API, VNPAY QR Gateway) cho doanh nghiệp.

---

## 📸 Screenshots & Demo

### Kanban View — Quản lý Tiến trình Tuyển dụng (Stages Pipeline)
![Application Kanban View](https://raw.githubusercontent.com/dgkhoa/Recruitment_Dev/main/docs/screenshots/kanban_view.png)
*Giao diện Kanban trực quan cho phép theo dõi ứng viên qua 6 vòng tuyển dụng, hiển thị % AI Matching Score và nhãn VIP.*

### Form View — Chi tiết Đơn ứng tuyển & Kết quả Quét CV AI Engine
![Application Form View](https://raw.githubusercontent.com/dgkhoa/Recruitment_Dev/main/docs/screenshots/form_view.png)
*Form chi tiết đơn ứng tuyển tích hợp bóc tách CV tự động bằng AI, gợi ý kỹ năng đạt/thiếu và tóm tắt nhận xét.*

### Dashboard & Analytics — Thống kê Tuyển dụng (Graph & Pivot)
![Recruitment Analytics](https://raw.githubusercontent.com/dgkhoa/Recruitment_Dev/main/docs/screenshots/analytics_view.png)
*Báo cáo phân tích số lượng ứng viên theo vị trí và vòng tuyển dụng dạng biểu đồ cột và bảng Pivot.*

---

## 🚀 Key Features

- **Quản lý Vị trí Tuyển dụng (Job Positions)**: Tạo vị trí tuyển dụng, quản lý chỉ tiêu, hạn nộp, định biên phòng ban và khung lương ngân sách.
- **Quản lý Hồ sơ Ứng viên & Quy trình (Applicants & Stages)**: Theo dõi tiến trình ứng tuyển qua Kanban 6 bước (`New` → `Screening` → `First Interview` → `Second Interview` → `Offer` → `Hired`).
- **Sàng lọc & Quét CV Thông minh (AI CV Scanning)**: Tự động bóc tách kỹ năng từ file CV PDF bằng Google Gemini / OpenAI Vision, chấm điểm % phù hợp và gợi ý Top ứng viên bằng Vector Cosine Similarity (768 chiều).
- **Tự động hóa Email & Tiếp nhận CV**: Tiếp nhận CV tự động qua Gmail Fetchmail (lọc tiêu đề `[APPLICATION]`), tự động gửi mail mời phỏng vấn, thư nhận việc (Offer) và mail từ chối kèm lý do.
- **Tích hợp Mạng xã hội (Facebook Integration)**: Đăng tin tuyển dụng tự động lên Facebook Fanpage chính thức của công ty qua Facebook Graph API v19.0.
- **Thanh toán QR & Tự động xuất Hóa đơn Kế toán**: Tích hợp VNPAY QR Payment Gateway, tự động khởi tạo Hóa đơn Kế toán (`account.move`) và xác nhận thanh toán khi ứng viên trúng tuyển/VIP.
- **Báo cáo Thống kê (Recruitment Analytics)**: Thống kê hiệu quả tuyển dụng toàn công ty qua các góc nhìn Graph Chart và Pivot Table.

---

## 🛠️ Tech Stack & Highlights

### Công nghệ Sử dụng (Tech Stack)
- **Core Framework**: Odoo 17.0 Community Edition
- **Backend Language**: Python 3.10+ (ORM, Custom Controllers, REST API Integration)
- **Database**: PostgreSQL 14+
- **Frontend / UI**: OWL (Odoo Web Library), QWeb XML Templates, SCSS, JavaScript ES6
- **Third-party APIs**: Google Gemini API, OpenAI API, Facebook Graph API v19.0, VNPAY QR Gateway, Gmail OAuth2

### Điểm nhấn Kỹ thuật (Technical Highlights)
- **Models & Quan hệ ORM**:
  - Khai báo 10 Custom Models (`recruitment.job.position`, `recruitment.candidate`, `recruitment.application`, `recruitment.interview`, `recruitment.offer`, `recruitment.stage`, `recruitment.refuse.reason`, `recruitment.ai.tool`...).
  - Sử dụng quan hệ `Many2one`, `One2many`, và trường `related` (VD: `department_id` tự động kế thừa từ `job_position_id`).
  - Decorators chuẩn: `@api.depends` (tính `hired_quantity`, `name`), `@api.onchange`, và `@api.constrains` (ràng buộc logic ngày phỏng vấn, lương tối thiểu/tối đa, email/SĐT hợp lệ).
- **Inheritance & Mixins**:
  - Kế thừa `mail.thread` và `mail.activity.mixin` trên model `recruitment.application` để mở tính năng **Chatter, Log Notes, Tracking Changes** và gửi Email tự động.
  - Kế thừa `res.config_settings` để tạo trang Cấu hình API Keys (Facebook Page Token, AI API Keys) trong Odoo Settings.
  - Kế thừa module `payment` chuẩn của Odoo để xây dựng custom provider `payment_vnpay`.
- **Views Giao diện Custom**:
  - Xây dựng 6 loại Views: **Form View**, **Tree/List View**, **Kanban View**, **Search View (Filters & Group By)**, **Graph View**, **Pivot View**.
  - Custom **OWL Component** cho widget AI Copilot (JS/SCSS/QWeb XML).
- **Security & Phân quyền 3 Lớp**:
  - **ACL (`ir.model.access.csv`)**: Phân quyền chi tiết CRUD cho 3 nhóm người dùng (`Interviewer`, `HR Officer`, `HR Manager`). Khóa quyền xóa vĩnh viễn dữ liệu (`perm_unlink=0`) để bảo toàn dữ liệu kiểm toán.
  - **Record Rules (`ir.rule`)**: Lọc dữ liệu theo domain force (`department_id` thuộc phòng ban người dùng) để Interviewer chỉ thấy dữ liệu phòng ban mình.
  - **UI Groups (`groups="..."`)**: Ẩn/hiện nút bấm (Quét AI, Offer, Thanh toán, Facebook), ẩn thông tin lương nhạy cảm và menu cấu hình theo từng Role.

---

## ⚡ Installation & Setup

### Bước 1: Clone Repository & Chuẩn bị Môi trường

```bash
# 1. Clone source code
git clone https://github.com/dgkhoa/Recruitment_Dev.git
cd Recruitment_Dev

# 2. Tạo Virtual Environment & Cài đặt thư viện
python3 -m venv venv
source venv/bin/activate
pip install -r odoo/requirements.txt
pip install pypdf requests
```

### Bước 2: Cấu hình Odoo Config (`config/odoo.conf`)

```ini
[options]
db_host = localhost
db_port = 5432
db_user = odoo
db_password = False
db_name = recruitment
addons_path = ./odoo/addons,./custom_addons
```

### Bước 3: Khởi chạy Server & Cài đặt Module

```bash
./venv/bin/python ./odoo/odoo-bin -c ./config/odoo.conf -d recruitment -i recruitment
```

Truy cập hệ thống tại: `http://localhost:8069`

---

## 👤 Author & License

### Tác giả
**Võ Đăng Khoa**
- **Email**: vodangkhoa270904@gmail.com
- **GitHub**: [github.com/dgkhoa](https://github.com/dgkhoa)

### License
Được phát hành dưới giấy phép [LGPL-3.0](https://www.gnu.org/licenses/lgpl-3.0.html), tuân theo chính sách License của Odoo Community.
