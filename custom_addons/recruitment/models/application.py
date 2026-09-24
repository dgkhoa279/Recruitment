from odoo import models, fields, api
from odoo.exceptions import ValidationError
from markupsafe import Markup, escape
import base64
import datetime
import re
import logging
from .application_email_parser import ApplicationEmailParser

_logger = logging.getLogger(__name__)


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

        # -------------------------------------------------------------
        # TỰ ĐỘNG KHỞI TẠO & XÁC NHẬN HÓA ĐƠN TRONG MODULE INVOICING (ACCOUNT)
        # -------------------------------------------------------------
        invoice_msg_part = ""
        try:
            if "account.move" in self.env:
                partner = getattr(self.candidate_id, "partner_id", None)
                if not partner:
                    partner = self.env["res.partner"].create({
                        "name": self.candidate_id.name or f"Ứng viên APP#{self.id}",
                        "email": self.candidate_id.email,
                        "phone": self.candidate_id.phone,
                    })
                    if hasattr(self.candidate_id, "partner_id"):
                        self.candidate_id.partner_id = partner.id

                job_name = self.job_position_id.name if self.job_position_id else "Vị trí tuyển dụng"
                invoice_vals = {
                    "move_type": "out_invoice",
                    "partner_id": partner.id,
                    "invoice_date": fields.Date.context_today(self),
                    "ref": f"Thanh toan VIP APP#{self.id} - {method_name}",
                    "invoice_line_ids": [(0, 0, {
                        "name": f"Phí xử lý & Đơn ứng tuyển VIP - Vị trí {job_name}",
                        "quantity": 1,
                        "price_unit": amount,
                    })],
                }
                invoice = self.env["account.move"].create(invoice_vals)
                invoice.action_post()

                # Tự động ghi nhận thanh toán (Register Payment)
                journal = self.env["account.journal"].search([("type", "in", ["bank", "cash"])], limit=1)
                if journal and "account.payment.register" in self.env:
                    payment_wizard = self.env["account.payment.register"].with_context(
                        active_model="account.move",
                        active_ids=invoice.ids,
                    ).create({
                        "amount": amount,
                        "payment_date": fields.Date.context_today(self),
                        "journal_id": journal.id,
                    })
                    payment_wizard.action_create_payments()

                inv_url = f"/web#id={invoice.id}&model=account.move&view_type=form"
                invoice_msg_part = f"<br/>🧾 <strong>Hóa đơn Kế toán đã khởi tạo:</strong> <a href='{inv_url}'><strong>{invoice.name}</strong></a> (Trạng thái: <strong>Paid</strong>) 🎉"
        except Exception as e:
            _logger.exception("Lỗi khi tự động khởi tạo Hóa đơn Invoicing: %s", str(e))
            invoice_msg_part = f"<br/>⚠️ <em>Lỗi khởi tạo Hóa đơn: {str(e)}</em>"

        msg = Markup(f"💳 <strong>Thanh toán thành công!</strong><br/>"
                     f"Số tiền: {amount:,.0f} VNĐ qua {method_name}.<br/>"
                     f"Hồ sơ đã được nâng cấp <strong>VIP</strong> và tự động chuyển sang stage <strong>HIRED</strong>! 🎉"
                     f"{invoice_msg_part}")
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

    can_invite_interview = fields.Boolean(
        string="Can Invite Interview",
        compute="_compute_action_availability",
    )

    can_send_offer = fields.Boolean(
        string="Can Send Offer",
        compute="_compute_action_availability",
    )

    employee_id = fields.Many2one(
        "hr.employee",
        string="Employee",
        readonly=True,
        copy=False,
        ondelete="set null",
        help="Employee record created when this application reaches Hired.",
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

    cv_extracted_text = fields.Text(
        string="CV Extracted Text",
        readonly=True,
        help="Văn bản được trích xuất từ CV để tìm kiếm, đánh giá và lập chỉ mục RAG.",
    )

    refuse_reason_id = fields.Many2one(
        "recruitment.refuse.reason",
        string="Refuse Reason",
    )

    matching_score = fields.Float(
        string="Skill Matching Score (%)",
        readonly=True,
        default=0.0,
        help="Tỷ lệ phần trăm phù hợp kỹ năng so với yêu cầu vị trí tuyển dụng",
    )

    rag_similarity_score = fields.Float(
        string="RAG Similarity Score (%)",
        readonly=True,
        default=0.0,
        help="Điểm semantic retrieval gần nhất giữa JD và các CV chunks.",
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

    ai_summary = fields.Text(
        string="AI Executive Summary",
        readonly=True,
        help="Đoạn tóm tắt có cấu trúc do AI provider đang cấu hình sinh ra.",
    )

    rag_index_status = fields.Selection(
        [
            ("not_indexed", "Not Indexed"),
            ("pending", "Pending"),
            ("indexing", "Indexing"),
            ("ready", "Ready"),
            ("failed", "Failed"),
        ],
        default="not_indexed",
        readonly=True,
        copy=False,
    )
    rag_index_error = fields.Text(readonly=True, copy=False)
    rag_indexed_at = fields.Datetime(readonly=True, copy=False)
    rag_source_checksum = fields.Char(readonly=True, copy=False, index=True)

    def action_scan_cv(self):
        """
        Parses PDF CV file, extracts plain text, compares with
        job_position_id.required_skills, calculates matching score (%), and records matched/missing skills.
        """
        ai_service = self.env["recruitment.ai.service"]
        for app in self:
            if not app.cv_file:
                self.env["recruitment.cv.chunk"].sudo().search([
                    ("document_type", "=", "cv"),
                    ("application_id", "=", app.id),
                ]).unlink()
                app.write({
                    "cv_extracted_text": False,
                    "matching_score": 0.0,
                    "matched_skills": False,
                    "missing_skills": False,
                    "ai_summary": False,
                    "rag_index_status": "not_indexed",
                    "rag_index_error": False,
                    "rag_indexed_at": False,
                    "rag_source_checksum": False,
                })
                continue

            cv_bytes = base64.b64decode(app.cv_file)

            # Prefer structured analysis through the configured provider adapter.
            ai_result = ai_service.analyze_cv(
                cv_bytes=cv_bytes,
                job_title=app.job_position_id.name or "Vị trí Tuyển dụng",
                req_skills=app.job_position_id.required_skills or "Chưa chỉ định",
                job_desc=app.job_position_id.description or "Không có mô tả công việc",
            )
            if ai_result:
                score = float(ai_result.get('matching_score', 0.0))
                matched_str = ai_result.get('matched_skills', 'Không có')
                missing_str = ai_result.get('missing_skills', 'Không có')
                extracted_text = ai_result['corrected_text']
                summary = ai_result.get('ai_summary', '')

                # Application luôn có candidate_id; chỉ bổ sung các thông tin còn thiếu.
                candidate_vals = {}
                if ai_result.get('candidate_name') and not app.candidate_id.name:
                    candidate_vals['name'] = ai_result['candidate_name']
                if ai_result.get('phone') and not app.candidate_id.phone:
                    candidate_vals['phone'] = ai_result['phone']
                if ai_result.get('email') and not app.candidate_id.email:
                    candidate_vals['email'] = ai_result['email']
                if candidate_vals:
                    app.candidate_id.write(candidate_vals)

                app.write({
                    "cv_extracted_text": extracted_text,
                    "matching_score": score,
                    "matched_skills": matched_str,
                    "missing_skills": missing_str,
                    "ai_summary": summary,
                    "rag_index_status": "pending",
                    "rag_index_error": False,
                })

                body = Markup(
                    f"<b>Kết quả Quét CV:</b><br/>"
                    f"• Độ phù hợp: <b>{score}%</b><br/>"
                    f"• Kỹ năng đạt được: <span style='color: green;'>{escape(matched_str)}</span><br/>"
                    f"• Kỹ năng còn thiếu: <span style='color: red;'>{escape(missing_str)}</span><br/><br/>"
                    f"📝 <b>AI Tóm tắt Đánh giá:</b><br/>{escape(summary)}"
                )
                app.message_post(body=body)
                continue

            # Deterministic keyword fallback keeps basic scanning available when
            # the configured provider is temporarily unavailable.
            extracted_text = ai_service.extract_document_text(cv_bytes)

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
                "ai_summary": False,
                "rag_index_status": "pending",
                "rag_index_error": False,
            })

            # Post a log in Chatter
            body = Markup(
                f"<b>Kết quả quét CV PDF kỹ năng:</b><br/>"
                f"• Điểm phù hợp: <b>{score}%</b><br/>"
                f"• Kỹ năng đạt được: <span style='color: green;'>{escape(matched_str)}</span><br/>"
                f"• Kỹ năng còn thiếu: <span style='color: red;'>{escape(missing_str)}</span><br/><br/>"
                f"💡 <i>AI provider chưa sẵn sàng; kết quả trên chỉ dùng đối sánh từ khóa cục bộ.</i>"
            )
            app.message_post(body=body)

        return True

    def action_send_interview_email(self):
        self.ensure_one()
        if not self.can_invite_interview:
            raise ValidationError(
                "Không thể mời phỏng vấn khi hồ sơ đã ở giai đoạn Offer hoặc Hired."
            )
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
        if not self.can_send_offer:
            raise ValidationError(
                "Không thể gửi thư mời nhận việc khi hồ sơ đã ở giai đoạn Hired."
            )
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

    @api.depends("active", "stage_id", "stage_id.sequence")
    def _compute_action_availability(self):
        offer_stage = self.env.ref(
            "recruitment.stage_offer",
            raise_if_not_found=False,
        )
        hired_stage = self.env.ref(
            "recruitment.stage_hired",
            raise_if_not_found=False,
        )
        offer_sequence = offer_stage.sequence if offer_stage else 40
        for application in self:
            application.can_invite_interview = bool(
                application.active
                and application.stage_id
                and application.stage_id.sequence < offer_sequence
            )
            application.can_send_offer = bool(
                application.active
                and application.stage_id
                and application.stage_id != hired_stage
            )

    def _get_or_create_employee_job(self):
        self.ensure_one()
        position = self.job_position_id
        if not position:
            return self.env["hr.job"]

        department = position.department_id
        company = department.company_id or self.env.company
        job_model = self.env["hr.job"].sudo().with_context(active_test=False)
        job = job_model.search([
            ("name", "=ilike", position.name),
            ("department_id", "=", department.id or False),
            ("company_id", "=", company.id),
        ], limit=1)
        if job:
            if not job.active:
                job.active = True
            return job

        values = {
            "name": position.name,
            "department_id": department.id or False,
            "company_id": company.id,
            "no_of_recruitment": position.quantity,
        }
        if position.description:
            values["description"] = position.description
        if position.required_skills:
            values["requirements"] = position.required_skills
        return job_model.create(values)

    def _find_existing_employee(self):
        self.ensure_one()
        employee_model = self.env["hr.employee"].sudo()

        previous_application = self.sudo().with_context(active_test=False).search([
            ("id", "!=", self.id),
            ("candidate_id", "=", self.candidate_id.id),
            ("employee_id", "!=", False),
        ], order="id desc", limit=1)
        if previous_application.employee_id:
            return previous_application.employee_id.sudo()

        email = (self.candidate_id.email or "").strip()
        if not email:
            return employee_model
        return employee_model.search([
            ("active", "=", True),
            "|",
            ("work_email", "=ilike", email),
            ("private_email", "=ilike", email),
        ], limit=1)

    def _prepare_employee_values(self):
        self.ensure_one()
        candidate = self.candidate_id
        position = self.job_position_id
        department = position.department_id
        company = department.company_id or self.env.company
        employee_job = self._get_or_create_employee_job()

        values = {
            "name": candidate.name,
            "company_id": company.id,
            "department_id": department.id or False,
            "job_id": employee_job.id or False,
            "job_title": position.name or False,
        }
        optional_values = {
            "work_email": candidate.email,
            "private_email": candidate.email,
            "mobile_phone": candidate.phone,
            "private_phone": candidate.phone,
            "private_street": candidate.address,
            "birthday": candidate.date_of_birth,
            "gender": candidate.gender,
            "study_field": candidate.education,
        }
        values.update({
            field_name: value
            for field_name, value in optional_values.items()
            if value
        })

        note_parts = []
        if candidate.skills:
            note_parts.append(f"Kỹ năng: {candidate.skills}")
        if candidate.note:
            note_parts.append(candidate.note)
        if note_parts:
            values["notes"] = "\n\n".join(note_parts)

        employee_type_by_contract = {
            "internship": "trainee",
            "contract": "contractor",
        }
        values["employee_type"] = employee_type_by_contract.get(
            position.employment_type,
            "employee",
        )
        return values

    def _attach_candidate_cv_to_employee(self, employee):
        self.ensure_one()
        cv_data = self.candidate_id.cv_file or self.cv_file
        if not cv_data:
            return
        filename = (
            self.candidate_id.cv_filename
            or self.cv_filename
            or f"CV - {self.candidate_id.name}.pdf"
        )
        attachment_model = self.env["ir.attachment"].sudo()
        existing = attachment_model.search([
            ("res_model", "=", "hr.employee"),
            ("res_id", "=", employee.id),
            ("name", "=", filename),
        ], limit=1)
        if not existing:
            attachment_model.create({
                "name": filename,
                "datas": cv_data,
                "res_model": "hr.employee",
                "res_id": employee.id,
            })

    def _ensure_employee_for_hired_application(self):
        hired_stage = self.env.ref(
            "recruitment.stage_hired",
            raise_if_not_found=False,
        )
        if not hired_stage:
            hired_stage = self.env["recruitment.stage"].search(
                [("name", "=ilike", "Hired")],
                limit=1,
            )

        for application in self.filtered(
            lambda app: hired_stage
            and app.stage_id == hired_stage
            and not app.employee_id
        ):
            employee = application._find_existing_employee()
            created = not employee
            if not employee:
                employee = self.env["hr.employee"].sudo().create(
                    application._prepare_employee_values()
                )

            application.with_context(
                skip_hired_employee_creation=True,
            ).write({"employee_id": employee.id})
            application._attach_candidate_cv_to_employee(employee)

            action = "được tạo" if created else "được liên kết"
            employee_url = f"/web#id={employee.id}&model=hr.employee&view_type=form"
            application.message_post(body=Markup(
                "Hồ sơ nhân viên <a href='{url}'><strong>{name}</strong></a> "
                "đã {action} tự động khi ứng viên chuyển sang "
                "<strong>Hired</strong>."
            ).format(
                url=escape(employee_url),
                name=escape(employee.name),
                action=escape(action),
            ))

    @api.model
    def _backfill_hired_employees(self):
        hired_stage = self.env.ref(
            "recruitment.stage_hired",
            raise_if_not_found=False,
        )
        if hired_stage:
            self.search([
                ("stage_id", "=", hired_stage.id),
                ("employee_id", "=", False),
            ])._ensure_employee_for_hired_application()
        return True

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._sync_cv_attachment()
        for rec in records:
            if rec.cv_file and rec.job_position_id:
                rec.action_scan_cv()
        records._ensure_employee_for_hired_application()
        return records

    def write(self, vals):
        res = super().write(vals)
        if 'cv_file' in vals or 'cv_filename' in vals:
            self._sync_cv_attachment()
        if not self.env.context.get("skip_ai_reindex") and ('cv_file' in vals or 'job_position_id' in vals):
            for rec in self:
                if rec.cv_file and rec.job_position_id:
                    rec.action_scan_cv()
        if (
            "stage_id" in vals
            and not self.env.context.get("skip_hired_employee_creation")
        ):
            self._ensure_employee_for_hired_application()
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
