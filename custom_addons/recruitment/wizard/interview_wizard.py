from odoo import models, fields, api
from odoo.exceptions import ValidationError
import datetime


class RecruitmentInterviewWizard(models.TransientModel):
    _name = "recruitment.interview.wizard"
    _description = "Interview Scheduling & Email Wizard"

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
    interviewer_user_id = fields.Many2one(
        "res.users",
        string="Interviewer User",
        default=lambda self: self.env.user,
        help="System user who will conduct the interview and receive the notification activity",
    )
    interviewer = fields.Char(
        string="Interviewer Name",
        required=True,
        default="Bộ phận Tuyển dụng",
    )
    scheduled_date = fields.Datetime(
        string="Scheduled Date",
        required=True,
        default=fields.Datetime.now,
    )
    interview_type = fields.Selection(
        [
            ("online", "Online"),
            ("offline", "Offline"),
            ("phone", "Phone"),
        ],
        string="Interview Type",
        default="online",
        required=True,
    )
    location = fields.Char(
        string="Location / Meeting URL",
        required=True,
        default="Google Meet / Văn phòng công ty",
        help="Google Meet link, Zoom URL or meeting room name",
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

    @api.onchange('interviewer_user_id')
    def _onchange_interviewer_user_id(self):
        if self.interviewer_user_id:
            self.interviewer = self.interviewer_user_id.name

    def _render_email_content(self):
        for rec in self:
            if not rec.application_id:
                continue
            candidate_name = rec.application_id.candidate_id.name or "Ứng viên"
            job_name = rec.application_id.job_position_id.name or "Tuyển dụng"
            interviewer_name = rec.interviewer or "Bộ phận Tuyển dụng"
            location_info = rec.location or "Sẽ thông báo sau"
            date_str = fields.Datetime.to_string(rec.scheduled_date) if rec.scheduled_date else "Sẽ thông báo sau"

            if not rec.subject:
                rec.subject = f"Thư mời phỏng vấn vị trí {job_name}"

            if not rec.body_html:
                rec.body_html = f"""
                <div style="margin: 0px; padding: 0px;">
                    <p style="margin: 0px; padding: 0px; font-size: 13px;">
                        Chào <strong>{candidate_name}</strong>,<br/><br/>
                        Cảm ơn bạn đã quan tâm và nộp hồ sơ ứng tuyển vào vị trí <strong>{job_name}</strong> tại công ty chúng tôi.<br/><br/>
                        Qua quá trình xem xét hồ sơ, chúng tôi trân trọng mời bạn tham dự buổi phỏng vấn.<br/><br/>
                        <strong>Thông tin phỏng vấn:</strong><br/>
                        - <strong>Thời gian:</strong> {date_str}<br/>
                        - <strong>Hình thức:</strong> {rec.interview_type.capitalize() if rec.interview_type else 'Online'}<br/>
                        - <strong>Người phỏng vấn:</strong> {interviewer_name}<br/>
                        - <strong>Địa điểm / Link họp:</strong> {location_info}<br/><br/>
                        Vui lòng phản hồi lại email này để xác nhận sự tham gia của bạn.<br/><br/>
                        Trân trọng,<br/>
                        Bộ phận Tuyển dụng
                    </p>
                </div>
                """

    @api.onchange('application_id', 'interviewer', 'interviewer_user_id', 'scheduled_date', 'interview_type', 'location', 'template_id')
    def _onchange_interview_fields(self):
        if not self.application_id:
            return
        candidate_name = self.application_id.candidate_id.name or "Ứng viên"
        job_name = self.application_id.job_position_id.name or "Tuyển dụng"
        interviewer_name = self.interviewer or "Bộ phận Tuyển dụng"
        location_info = self.location or "Sẽ thông báo sau"
        date_str = fields.Datetime.to_string(self.scheduled_date) if self.scheduled_date else "Sẽ thông báo sau"

        self.subject = f"Thư mời phỏng vấn vị trí {job_name}"
        self.body_html = f"""
        <div style="margin: 0px; padding: 0px;">
            <p style="margin: 0px; padding: 0px; font-size: 13px;">
                Chào <strong>{candidate_name}</strong>,<br/><br/>
                Cảm ơn bạn đã quan tâm và nộp hồ sơ ứng tuyển vào vị trí <strong>{job_name}</strong> tại công ty chúng tôi.<br/><br/>
                Qua quá trình xem xét hồ sơ, chúng tôi trân trọng mời bạn tham dự buổi phỏng vấn.<br/><br/>
                <strong>Thông tin phỏng vấn:</strong><br/>
                - <strong>Thời gian:</strong> {date_str}<br/>
                - <strong>Hình thức:</strong> {self.interview_type.capitalize() if self.interview_type else 'Online'}<br/>
                - <strong>Người phỏng vấn:</strong> {interviewer_name}<br/>
                - <strong>Địa điểm / Link họp:</strong> {location_info}<br/><br/>
                Vui lòng phản hồi lại email này để xác nhận sự tham gia của bạn.<br/><br/>
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

    def action_send_mail_and_create_interview(self):
        self.ensure_one()

        if self.scheduled_date < fields.Datetime.now():
            raise ValidationError("Ngày giờ phỏng vấn không được chọn ở quá khứ.")

        app = self.application_id

        # 1. Update application interview_date and interviewer_user_id
        assigned_user = self.interviewer_user_id or self.env.user
        app.write({
            'interview_date': self.scheduled_date,
            'interviewer_user_id': assigned_user.id,
        })

        # 2. Create Interview record
        unique_name = f"Interview - {app.candidate_id.name} - {fields.Datetime.to_string(self.scheduled_date)}"
        existing = self.env['recruitment.interview'].search([('name', '=', unique_name)], limit=1)
        if existing:
            unique_name = f"{unique_name} ({fields.Datetime.now().strftime('%H%M%S')})"

        self.env['recruitment.interview'].create({
            'name': unique_name,
            'application_id': app.id,
            'scheduled_date': self.scheduled_date,
            'interview_type': self.interview_type,
            'location': self.location,
            'interviewer': self.interviewer,
            'status': 'scheduled',
        })

        # 3. Move application to First Interview stage if currently in New / Screening
        interview_stage = self.env['recruitment.stage'].search([('name', 'ilike', 'Interview')], limit=1)
        if interview_stage and app.stage_id != interview_stage:
            app.write({'stage_id': interview_stage.id})

        # 4. Send Email to candidate
        mail_values = {
            'subject': self.subject or f"Thư mời phỏng vấn vị trí {app.job_position_id.name}",
            'body_html': self.body_html,
            'email_to': app.candidate_id.email,
            'email_from': self.env.user.email_formatted or self.env.company.email,
            'res_id': app.id,
            'model': 'recruitment.application',
        }
        mail = self.env['mail.mail'].create(mail_values)
        mail.send()

        # 5. Create Mail Activity notification for Interviewer
        activity_type = self.env.ref("mail.mail_activity_data_meeting", raise_if_not_found=False)
        if not activity_type:
            activity_type = self.env['mail.activity.type'].search([('category', '=', 'meeting')], limit=1)

        assigned_user = self.interviewer_user_id or self.env.user
        deadline_date = self.scheduled_date.date() if self.scheduled_date else fields.Date.today()

        activity_summary = f"Phỏng vấn ứng viên {app.candidate_id.name}"
        activity_note = f"""
        <p><strong>Lịch phỏng vấn đã được xếp thành công:</strong></p>
        <ul>
            <li><strong>Ứng viên:</strong> {app.candidate_id.name}</li>
            <li><strong>Vị trí:</strong> {app.job_position_id.name}</li>
            <li><strong>Thời gian:</strong> {fields.Datetime.to_string(self.scheduled_date)}</li>
            <li><strong>Hình thức:</strong> {self.interview_type.capitalize() if self.interview_type else 'Online'}</li>
            <li><strong>Địa điểm / Link họp:</strong> {self.location}</li>
        </ul>
        """

        app.activity_schedule(
            activity_type_id=activity_type.id if activity_type else False,
            summary=activity_summary,
            note=activity_note,
            date_deadline=deadline_date,
            user_id=assigned_user.id,
        )

        return {'type': 'ir.actions.act_window_close'}
