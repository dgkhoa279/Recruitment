from odoo import models, fields, api
from odoo.exceptions import ValidationError
import datetime


class RecruitmentOfferWizard(models.TransientModel):
    _name = "recruitment.offer.wizard"
    _description = "Job Offer Scheduling & Email Wizard"

    application_id = fields.Many2one(
        "recruitment.application",
        string="Application",
        required=True,
    )
    candidate_id = fields.Many2one(
        related="application_id.candidate_id",
        string="Candidate",
        readonly=True,
    )
    job_position_id = fields.Many2one(
        related="application_id.job_position_id",
        string="Job Position",
        readonly=True,
    )
    proposed_salary = fields.Float(
        string="Proposed Salary (VNĐ)",
        required=True,
        default=15000000.0,
    )
    salary_period = fields.Selection(
        [
            ("monthly", "Hàng tháng (Monthly)"),
            ("yearly", "Hàng năm (Yearly)"),
        ],
        string="Salary Period",
        default="monthly",
        required=True,
    )
    offer_date = fields.Date(
        string="Offer Date",
        required=True,
        default=fields.Date.context_today,
    )
    expiration_date = fields.Date(
        string="Expiration Date",
        required=True,
        default=lambda self: fields.Date.context_today(self) + datetime.timedelta(days=7),
    )
    start_date = fields.Date(
        string="Expected Start Date",
        default=lambda self: fields.Date.context_today(self) + datetime.timedelta(days=14),
    )
    template_id = fields.Many2one(
        "mail.template",
        string="Email Template",
        domain="[('model', '=', 'recruitment.application')]",
    )
    subject = fields.Char(
        string="Subject",
    )
    body_html = fields.Html(
        string="Email Body",
    )

    def _render_email_content(self):
        for rec in self:
            if not rec.application_id:
                continue
            candidate_name = rec.application_id.candidate_id.name or "Ứng viên"
            job_name = rec.application_id.job_position_id.name or "Tuyển dụng"
            salary_str = f"{rec.proposed_salary:,.0f} VNĐ / {'tháng' if rec.salary_period == 'monthly' else 'năm'}"
            exp_date_str = rec.expiration_date.strftime('%d/%m/%Y') if rec.expiration_date else "Sẽ thông báo sau"
            start_date_str = rec.start_date.strftime('%d/%m/%Y') if rec.start_date else "Sẽ thông báo sau"

            if not rec.subject:
                rec.subject = f"Thư mời nhận việc (Job Offer) - Vị trí {job_name}"

            if not rec.body_html:
                rec.body_html = f"""
                <div style="margin: 0px; padding: 0px;">
                    <p style="margin: 0px; padding: 0px; font-size: 13px;">
                        Chào <strong>{candidate_name}</strong>,<br/><br/>
                        Chúc mừng bạn! Qua quá trình phỏng vấn và đánh giá, chúng tôi rất ấn tượng với năng lực của bạn và trân trọng gửi lời mời bạn gia nhập công ty với vị trí <strong>{job_name}</strong>.<br/><br/>
                        <strong>Thông tin Thư mời nhận việc (Offer Details):</strong><br/>
                        - <strong>Vị trí:</strong> {job_name}<br/>
                        - <strong>Mức lương đề xuất:</strong> {salary_str}<br/>
                        - <strong>Ngày dự kiến bắt đầu làm việc:</strong> {start_date_str}<br/>
                        - <strong>Hạn phản hồi offer:</strong> {exp_date_str}<br/><br/>
                        Vui lòng xem xét và phản hồi lại email này trước ngày <strong>{exp_date_str}</strong> để xác nhận việc nhận offer.<br/><br/>
                        Rất mong được đồng hành cùng bạn!<br/><br/>
                        Trân trọng,<br/>
                        Bộ phận Tuyển dụng
                    </p>
                </div>
                """

    @api.onchange('application_id', 'proposed_salary', 'salary_period', 'expiration_date', 'start_date', 'template_id')
    def _onchange_offer_fields(self):
        if not self.application_id:
            return
        candidate_name = self.application_id.candidate_id.name or "Ứng viên"
        job_name = self.application_id.job_position_id.name or "Tuyển dụng"
        salary_str = f"{self.proposed_salary:,.0f} VNĐ / {'tháng' if self.salary_period == 'monthly' else 'năm'}"
        exp_date_str = self.expiration_date.strftime('%d/%m/%Y') if self.expiration_date else "Sẽ thông báo sau"
        start_date_str = self.start_date.strftime('%d/%m/%Y') if self.start_date else "Sẽ thông báo sau"

        self.subject = f"Thư mời nhận việc (Job Offer) - Vị trí {job_name}"
        self.body_html = f"""
        <div style="margin: 0px; padding: 0px;">
            <p style="margin: 0px; padding: 0px; font-size: 13px;">
                Chào <strong>{candidate_name}</strong>,<br/><br/>
                Chúc mừng bạn! Qua quá trình phỏng vấn và đánh giá, chúng tôi rất ấn tượng với năng lực của bạn và trân trọng gửi lời mời bạn gia nhập công ty với vị trí <strong>{job_name}</strong>.<br/><br/>
                <strong>Thông tin Thư mời nhận việc (Offer Details):</strong><br/>
                - <strong>Vị trí:</strong> {job_name}<br/>
                - <strong>Mức lương đề xuất:</strong> {salary_str}<br/>
                - <strong>Ngày dự kiến bắt đầu làm việc:</strong> {start_date_str}<br/>
                - <strong>Hạn phản hồi offer:</strong> {exp_date_str}<br/><br/>
                Vui lòng xem xét và phản hồi lại email này trước ngày <strong>{exp_date_str}</strong> để xác nhận việc nhận offer.<br/><br/>
                Rất mong được đồng hành cùng bạn!<br/><br/>
                Trân trọng,<br/>
                Bộ phận Tuyển dụng
            </p>
        </div>
        """

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._render_email_content()
        return records

    def action_send_offer_and_create_record(self):
        self.ensure_one()

        if self.proposed_salary <= 0:
            raise ValidationError("Mức lương đề xuất phải lớn hơn 0.")

        if self.expiration_date and self.offer_date and self.expiration_date < self.offer_date:
            raise ValidationError("Hạn phản hồi không được trước ngày gửi offer.")

        app = self.application_id

        # 1. Update application proposed_salary
        app.write({'proposed_salary': self.proposed_salary})

        # 2. Create Job Offer Record
        unique_name = f"Offer - {app.candidate_id.name} - {app.job_position_id.name}"
        existing = self.env['recruitment.offer'].search([('name', '=', unique_name)], limit=1)
        if existing:
            unique_name = f"{unique_name} ({fields.Date.today().strftime('%Y%m%d')})"

        self.env['recruitment.offer'].create({
            'name': unique_name,
            'application_id': app.id,
            'offer_date': self.offer_date,
            'expiration_date': self.expiration_date,
            'start_date': self.start_date,
            'proposed_salary': self.proposed_salary,
            'salary_period': self.salary_period,
            'status': 'sent',
        })

        # 3. Move application to Offer stage
        offer_stage = self.env['recruitment.stage'].search([('name', '=ilike', 'Offer')], limit=1)
        if offer_stage and app.stage_id != offer_stage:
            app.write({'stage_id': offer_stage.id})

        # 4. Send Email to candidate
        mail_values = {
            'subject': self.subject or f"Thư mời nhận việc (Job Offer) - Vị trí {app.job_position_id.name}",
            'body_html': self.body_html,
            'email_to': app.candidate_id.email,
            'email_from': self.env.user.email_formatted or self.env.company.email,
            'res_id': app.id,
            'model': 'recruitment.application',
        }
        mail = self.env['mail.mail'].sudo().create(mail_values)

        # 5. Create Mail Activity for HR to follow up
        activity_type = self.env.ref("mail.mail_activity_data_todo", raise_if_not_found=False)
        if not activity_type:
            activity_type = self.env['mail.activity.type'].search([('category', '=', 'default')], limit=1)

        activity_summary = f"Theo dõi Offer - {app.candidate_id.name}"
        activity_note = f"""
        <p><strong>Đã gửi Thư mời nhận việc (Offer):</strong></p>
        <ul>
            <li><strong>Ứng viên:</strong> {app.candidate_id.name}</li>
            <li><strong>Mức lương đề xuất:</strong> {self.proposed_salary:,.0f} VNĐ / {'tháng' if self.salary_period == 'monthly' else 'năm'}</li>
            <li><strong>Ngày dự kiến đi làm:</strong> {self.start_date.strftime('%d/%m/%Y') if self.start_date else ''}</li>
            <li><strong>Hạn chót ứng viên phản hồi:</strong> {self.expiration_date.strftime('%d/%m/%Y') if self.expiration_date else ''}</li>
        </ul>
        """

        app.activity_schedule(
            activity_type_id=activity_type.id if activity_type else False,
            summary=activity_summary,
            note=activity_note,
            date_deadline=self.expiration_date,
            user_id=self.env.user.id,
        )
        # Send only after all transactional business records are valid. This
        # minimizes external side effects when an earlier ORM operation fails.
        mail.sudo().send()

        return {'type': 'ir.actions.act_window_close'}
