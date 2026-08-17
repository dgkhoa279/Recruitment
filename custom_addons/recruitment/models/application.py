from odoo import models, fields, api
from odoo.exceptions import ValidationError
from markupsafe import Markup
import base64
import datetime
import io
import re
import logging
from .application_email_parser import ApplicationEmailParser

_logger = logging.getLogger(__name__)

try:
    import pypdf as pdf_module
except ImportError:
    try:
        import PyPDF2 as pdf_module
    except ImportError:
        pdf_module = None


class RecruitmentApplication(models.Model):
    _name = "recruitment.application"
    _description = "Recruitment Application"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "application_date desc"

    name = fields.Char(
        string="Application",
        compute="_compute_name",
        store=True,
        readonly=False,
    )

    candidate_id = fields.Many2one(
        "recruitment.candidate",
        string="Candidate",
        required=True,
        ondelete="cascade",
    )

    job_position_id = fields.Many2one(
        "recruitment.job.position",
        string="Job Position",
        required=True,
        ondelete="restrict",
    )

    department_id = fields.Many2one(
        related="job_position_id.department_id",
        string="Department",
        store=True,
        readonly=True,
    )

    expected_salary = fields.Float(
        string="Expected Salary (VNĐ)",
        default=0.0,
    )

    is_vip_paid = fields.Boolean(
        string="Đã thanh toán",
        default=False,
        copy=False,
        tracking=True,
    )

    payment_amount = fields.Float(
        string="Số tiền đã thanh toán (VNĐ)",
        default=0.0,
        copy=False,
    )

    payment_method = fields.Selection(
        [
            ("vietqr", "VietQR Ngân hàng"),
            ("vnpay", "VNPAY QR"),
            ("momo", "Ví MoMo"),
            ("zalopay", "Ví ZaloPay"),
        ],
        string="Phương thức thanh toán",
        readonly=True,
        copy=False,
    )

    def action_open_payment_wizard(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Thanh toán Đơn ứng tuyển",
            "res_model": "recruitment.payment.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_application_id": self.id,
                "default_amount": 2000000.0,
            },
        }

    def action_confirm_payment_and_hire(self, amount=2000000.0, method="vietqr"):
        self.ensure_one()
        hired_stage = self.env["recruitment.stage"].search(
            [("name", "=ilike", "hired")], limit=1
        )

        method_labels = {
            "vietqr": "VietQR Ngân hàng",
            "vnpay": "VNPAY QR",
            "momo": "Ví MoMo",
            "zalopay": "Ví ZaloPay",
        }
        method_name = method_labels.get(method, method)

        vals = {
            "is_vip_paid": True,
            "payment_amount": amount,
            "payment_method": method,
        }

        if hired_stage:
            vals["stage_id"] = hired_stage.id

        self.write(vals)

        msg = Markup(f"💳 <strong>Thanh toán thành công!</strong><br/>"
                     f"Số tiền: {amount:,.0f} VNĐ qua {method_name}.<br/>"
                     f"Hồ sơ đã được nâng cấp <strong>VIP</strong> và tự động chuyển sang stage <strong>HIRED</strong>! 🎉")
        self.message_post(body=msg)

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Thanh toán Thành công! 🎉",
                "message": f"Hồ sơ ứng viên {self.candidate_id.name} đã được thanh toán & chuyển sang HIRED!",
                "type": "success",
                "sticky": False,
            },
        }

    interview_ids = fields.One2many(
        "recruitment.interview",
        "application_id",
        string="Interviews",
    )

    offer_ids = fields.One2many(
        "recruitment.offer",
        "application_id",
        string="Offers",
    )

    interviewer_user_id = fields.Many2one(
        "res.users",
        string="Interviewer",
        tracking=True,
        help="Assigned interviewer for this application",
    )

    application_date = fields.Date(
        string="Application Date",
        default=fields.Date.today,
        required=True,
    )

    def _default_stage_id(self):
        return self.env['recruitment.stage'].search([], order='sequence asc', limit=1)

    stage_id = fields.Many2one(
        "recruitment.stage",
        string="Stage",
        required=True,
        ondelete="restrict",
        default=_default_stage_id,
    )

    interview_date = fields.Datetime(
        string="Interview Date",
    )

    expected_salary = fields.Float(
        string="Expected Salary",
        related="candidate_id.expected_salary",
        readonly=True,
    )

    proposed_salary = fields.Float(
        string="Proposed Salary",
    )

    source = fields.Selection(
        [
            ("website", "Website"),
            ("linkedin", "LinkedIn"),
            ("referral", "Employee Referral"),
            ("email", "Email"),
            ("other", "Other"),
        ],
        string="Source",
    )

    note = fields.Text(
        string="Note",
    )

    active = fields.Boolean(
        default=True,
    )

    cv_file = fields.Binary(
        string="CV File",
        attachment=True,
    )

    cv_filename = fields.Char(
        string="CV Filename",
    )

    refuse_reason_id = fields.Many2one(
        "recruitment.refuse.reason",
        string="Refuse Reason",
    )

    cv_extracted_text = fields.Text(
        string="Extracted CV Text",
        readonly=True,
        help="Nội dung văn bản bóc tách từ file CV PDF",
    )

    matching_score = fields.Float(
        string="Skill Matching Score (%)",
        readonly=True,
        default=0.0,
        help="Tỷ lệ phần trăm phù hợp kỹ năng so với yêu cầu vị trí tuyển dụng",
    )

    matched_skills = fields.Char(
        string="Matched Skills",
        readonly=True,
        help="Các kỹ năng tìm thấy trong CV",
    )

    missing_skills = fields.Char(
        string="Missing Skills",
        readonly=True,
        help="Các kỹ năng còn thiếu trong CV",
    )

    def _extract_text_from_cv(self, cv_bytes):
        """Helper method to extract text from PDF or raw bytes."""
        if not cv_bytes:
            return ""

        if cv_bytes.startswith(b"%PDF"):
            if not pdf_module:
                return "Chưa cài đặt thư viện đọc PDF (pypdf/PyPDF2)."

            text_pages = []
            try:
                if hasattr(pdf_module, "PdfReader"):
                    reader = pdf_module.PdfReader(io.BytesIO(cv_bytes))
                    pages = reader.pages
                else:
                    reader = pdf_module.PdfFileReader(io.BytesIO(cv_bytes))
                    num_pages = (
                        reader.getNumPages()
                        if hasattr(reader, "getNumPages")
                        else len(reader.pages)
                    )
                    pages = [reader.getPage(i) for i in range(num_pages)]

                for page in pages:
                    extract_fn = getattr(page, "extract_text", None) or getattr(
                        page, "extractText", None
                    )
                    if extract_fn:
                        txt = extract_fn()
                        if txt:
                            text_pages.append(txt)
                return "\n".join(text_pages)
            except Exception as e:
                return f"Không thể trích xuất văn bản từ PDF: {str(e)}"
        else:
            try:
                return cv_bytes.decode("utf-8")
            except Exception:
                return "File CV đính kèm không thuộc định dạng PDF văn bản hợp lệ."

    def action_scan_cv(self):
        """
        Parses PDF CV file, extracts plain text, compares with
        job_position_id.required_skills, calculates matching score (%), and records matched/missing skills.
        """
        for app in self:
            if not app.cv_file:
                app.write({
                    "cv_extracted_text": False,
                    "matching_score": 0.0,
                    "matched_skills": False,
                    "missing_skills": False,
                })
                continue

            cv_bytes = base64.b64decode(app.cv_file)
            extracted_text = app._extract_text_from_cv(cv_bytes)

            req_skills_str = app.job_position_id.required_skills or ""
            required_skills_list = [
                s.strip() for s in req_skills_str.split(",") if s.strip()
            ]

            matched_list = []
            missing_list = []
            score = 0.0

            if required_skills_list:
                cv_text_lower = extracted_text.lower()
                cv_text_compact = re.sub(r"\s+", " ", cv_text_lower)
                cv_text_nospace = re.sub(r"\s+", "", cv_text_lower)

                for skill in required_skills_list:
                    sk_lower = skill.lower()
                    sk_nospace = sk_lower.replace(" ", "")
                    if (
                        sk_lower in cv_text_lower
                        or sk_lower in cv_text_compact
                        or sk_nospace in cv_text_nospace
                    ):
                        matched_list.append(skill)
                    else:
                        missing_list.append(skill)

                score = round(
                    (len(matched_list) / len(required_skills_list)) * 100.0, 1
                )

            matched_str = ", ".join(matched_list) if matched_list else "Không có"
            missing_str = ", ".join(missing_list) if missing_list else "Không có"

            app.write({
                "cv_extracted_text": extracted_text,
                "matching_score": score,
                "matched_skills": matched_str,
                "missing_skills": missing_str,
            })

            # Post a log in Chatter
            body = Markup(
                f"<b>Kết quả quét CV PDF kỹ năng:</b><br/>"
                f"• Điểm phù hợp: <b>{score}%</b><br/>"
                f"• Kỹ năng đạt được: <span style='color: green;'>{matched_str}</span><br/>"
                f"• Kỹ năng còn thiếu: <span style='color: red;'>{missing_str}</span>"
            )
            app.message_post(body=body)

        return True

    def action_send_interview_email(self):
        self.ensure_one()
        template = self.env.ref("recruitment.email_template_interview", raise_if_not_found=False)
        default_date = self.interview_date or (fields.Datetime.now() + datetime.timedelta(days=1))
        return {
            'name': 'Đặt Lịch Phỏng Vấn & Gửi Thư Mời',
            'type': 'ir.actions.act_window',
            'view_mode': 'form',
            'res_model': 'recruitment.interview.wizard',
            'target': 'new',
            'context': {
                'default_application_id': self.id,
                'default_template_id': template.id if template else False,
                'default_scheduled_date': default_date,
            },
        }

    def action_send_offer_email(self):
        self.ensure_one()
        template = self.env.ref("recruitment.email_template_offer", raise_if_not_found=False)
        return {
            'name': 'Soạn Thư Mời Nhận Việc (Job Offer)',
            'type': 'ir.actions.act_window',
            'view_mode': 'form',
            'res_model': 'recruitment.offer.wizard',
            'target': 'new',
            'context': {
                'default_application_id': self.id,
                'default_template_id': template.id if template else False,
                'default_proposed_salary': self.proposed_salary or 15000000.0,
            },
        }

    def action_open_refuse_wizard(self):
        self.ensure_one()
        return {
            'name': 'Từ chối Đơn ứng tuyển',
            'type': 'ir.actions.act_window',
            'view_mode': 'form',
            'res_model': 'recruitment.refuse.wizard',
            'target': 'new',
            'context': {
                'default_application_ids': [self.id],
            },
        }

    def action_restore_application(self):
        """
        Khôi phục lại Đơn ứng tuyển đã bị Từ chối (Archived).
        Kích hoạt lại active = True và xóa bỏ Lý do từ chối.
        """
        for app in self:
            app.write({
                'active': True,
                'refuse_reason_id': False,
            })

    @api.onchange('candidate_id')
    def _onchange_candidate_id(self):
        for record in self:
            record.job_position_id = record.candidate_id.job_position_id

    @api.constrains('application_date', 'interview_date')
    def _check_date(self):
        for record in self:
            if record.application_date and record.interview_date:
                if record.application_date > record.interview_date.date():
                    raise ValidationError("Ngày phỏng vấn phải sau ngày nộp đơn!")
            if record.application_date and record.application_date > fields.Date.today():
                raise ValidationError("Ngày nộp đơn không thể lớn hơn ngày hiện tại!")
            if record.interview_date and record.interview_date.date() < fields.Date.today():
                raise ValidationError("Ngày phỏng vấn không được nhỏ hơn ngày hiện tại!")
    @api.constrains('proposed_salary')
    def _check_salary(self):
        for record in self:
            if record.proposed_salary <=0:
                raise ValidationError("Lương đề xuất phải lớn hơn 0!")

    def _sync_cv_attachment(self):
        for rec in self:
            if rec.cv_file and rec.cv_filename:
                existing = self.env['ir.attachment'].search([
                    ('res_model', '=', 'recruitment.application'),
                    ('res_id', '=', rec.id),
                    ('name', '=', rec.cv_filename),
                ], limit=1)
                if not existing:
                    self.env['ir.attachment'].create({
                        'name': rec.cv_filename,
                        'datas': rec.cv_file,
                        'res_model': 'recruitment.application',
                        'res_id': rec.id,
                    })

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._sync_cv_attachment()
        for rec in records:
            if rec.cv_file and rec.job_position_id:
                rec.action_scan_cv()
        return records

    def write(self, vals):
        res = super().write(vals)
        if 'cv_file' in vals or 'cv_filename' in vals:
            self._sync_cv_attachment()
        if 'cv_file' in vals or 'job_position_id' in vals:
            for rec in self:
                if rec.cv_file and rec.job_position_id:
                    rec.action_scan_cv()
        return res

    @api.depends('candidate_id', 'job_position_id')
    def _compute_name(self):
        for record in self:
            # 3. Kiểm tra xem đã có dữ liệu chưa để tránh lỗi cộng chuỗi rỗng
            if record.candidate_id and record.job_position_id:
                record.name = f"{record.candidate_id.name} - {record.job_position_id.name}"
            elif record.candidate_id:
                record.name = record.candidate_id.name
            else:
                # Giá trị mặc định khi vừa mở form lên chưa chọn gì
                record.name = "Đơn ứng tuyển mới"

    @api.model
    def message_route(self, message, message_dict, model=None, thread_id=None, custom_values=None):
        """
        Lọc email đầu vào: Chỉ điều hướng các email có tiêu đề chứa [APPLICATION] VÀ
        vị trí tuyển dụng trong tiêu đề phải tồn tại sẵn trong hệ thống công ty.
        Tất cả các email rác hoặc ứng tuyển vị trí không tồn tại sẽ bị bỏ qua hoàn toàn.
        """
        subject = message_dict.get('subject', '') or ''
        if '[APPLICATION]' not in subject.upper():
            return []

        match_subj = re.search(r'\[APPLICATION\]\s*(.*)', subject, re.IGNORECASE)
        if match_subj:
            job_pos_name = match_subj.group(1).strip()
            if job_pos_name:
                job_pos = self.env['recruitment.job.position'].search(
                    [('name', '=ilike', job_pos_name)], limit=1
                )
                if not job_pos:
                    _logger.warning("Bỏ qua email ứng tuyển cho vị trí chưa có trong danh mục công ty: '%s'", job_pos_name)
                    return []

        return super().message_route(message, message_dict, model=model, thread_id=thread_id, custom_values=custom_values)

    @api.model
    def message_new(self, msg_dict, custom_values=None):
        """
        Tự động kiểm tra / tạo Candidate và Application khi nhận Email ứng tuyển.
        Yêu cầu Vị trí tuyển dụng (Job Position) phải tồn tại sẵn trong danh mục công ty.
        """
        if custom_values is None:
            custom_values = {}

        parsed_data = ApplicationEmailParser.parse(msg_dict)
        if not parsed_data:
            return None

        name = parsed_data['name']
        email = parsed_data['email']
        phone = parsed_data.get('phone') or '0000000000'
        job_position_name = parsed_data['job_position']
        cv_attachment = parsed_data['cv_attachment']

        # 1. Tìm Vị trí tuyển dụng (Job Position) - Không tự tạo rác nếu không tìm thấy
        job_position = self.env['recruitment.job.position'].search(
            [('name', '=ilike', job_position_name)],
            limit=1,
        )
        if not job_position:
            _logger.warning("Email ứng tuyển bị từ chối do vị trí tuyển dụng '%s' không tồn tại.", job_position_name)
            return None

        # Chuẩn bị dữ liệu nhị phân CV nếu có file đính kèm
        cv_binary = None
        cv_name = None
        cv_content_type = 'application/octet-stream'
        if cv_attachment:
            cv_name = cv_attachment[0]
            content = cv_attachment[1]
            cv_content_type = cv_attachment[2] if len(cv_attachment) > 2 else 'application/octet-stream'
            if isinstance(content, str):
                content = content.encode('utf-8')
            cv_binary = base64.b64encode(content).decode('utf-8')

        # 2. Tìm hoặc tạo Ứng viên (Candidate)
        candidate = self.env['recruitment.candidate'].search(
            [('email', '=ilike', email)],
            limit=1,
        )
        if not candidate:
            candidate_vals = {
                'name': name,
                'email': email,
                'phone': phone,
                'job_position_id': job_position.id,
                'experience_years': 0,
            }
            if cv_binary and cv_name:
                candidate_vals['cv_file'] = cv_binary
                candidate_vals['cv_filename'] = cv_name

            candidate = self.env['recruitment.candidate'].create(candidate_vals)
        else:
            # Nếu Candidate đã tồn tại, cập nhật CV mới nếu có
            if cv_binary and cv_name:
                candidate.write({
                    'cv_file': cv_binary,
                    'cv_filename': cv_name,
                })

        # 3. Cập nhật thông tin tạo Application
        custom_values.update({
            'candidate_id': candidate.id,
            'job_position_id': job_position.id,
            'source': 'email',
        })
        if cv_binary and cv_name:
            custom_values['cv_file'] = cv_binary
            custom_values['cv_filename'] = cv_name

        # 4. Tạo bản ghi Application
        application = super().message_new(msg_dict, custom_values)

        # 5. Đính kèm File CV vào Application vừa tạo và đính kèm ir.attachment
        if cv_binary and cv_name:
            self.env['ir.attachment'].create({
                'name': cv_name,
                'datas': cv_binary,
                'res_model': 'recruitment.application',
                'res_id': application.id,
                'mimetype': cv_content_type,
            })

            # Đính kèm ir.attachment cho Candidate nếu chưa có
            self.env['ir.attachment'].create({
                'name': cv_name,
                'datas': cv_binary,
                'res_model': 'recruitment.candidate',
                'res_id': candidate.id,
                'mimetype': cv_content_type,
            })

        return application