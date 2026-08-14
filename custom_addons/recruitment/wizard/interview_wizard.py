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

    COMPANY_ADDRESS = "111A Pasteur, Phường Sài Gòn, TP. Hồ Chí Minh"

    @api.onchange('interview_type')
    def _onchange_interview_type(self):
        if self.interview_type == 'offline':
            self.location = self.COMPANY_ADDRESS
        elif self.interview_type == 'phone':
            cand_phone = self.application_id.candidate_id.phone if self.application_id and self.application_id.candidate_id else ""
            self.location = f"Trao đổi qua điện thoại / Zalo ({cand_phone})" if cand_phone else "Trao đổi qua điện thoại / Zalo"
        elif self.interview_type == 'online':
            default_meet = self.env['ir.config_parameter'].sudo().get_param('recruitment.google_meet_default_url', default='')
            if default_meet:
                self.location = default_meet
            elif not self.location or self.location == self.COMPANY_ADDRESS or not self.location.startswith("http"):
                self.location = "https://meet.google.com"
        self._onchange_interview_fields()

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
            if location_info.startswith(("http://", "https://")):
                location_fmt = f'<a href="{location_info}" target="_blank">{location_info}</a>'
            else:
                location_fmt = location_info

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
                        - <strong>Địa điểm / Link họp:</strong> {location_fmt}<br/><br/>
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
        if location_info.startswith(("http://", "https://")):
            location_fmt = f'<a href="{location_info}" target="_blank">{location_info}</a>'
        else:
            location_fmt = location_info

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
                - <strong>Địa điểm / Link họp:</strong> {location_fmt}<br/><br/>
                Vui lòng phản hồi lại email này để xác nhận sự tham gia của bạn.<br/><br/>
                Trân trọng,<br/>
                Bộ phận Tuyển dụng
            </p>
        </div>
        """

    def action_generate_google_meet_link(self):
        """
        Tự động gọi Google Calendar API để tạo phòng họp dùng sau (Google Meet) trên Google Cloud
        và điền trực tiếp link thật (ví dụ: https://meet.google.com/eoy-mikq-ocb) vào ô location.
        """
        self.ensure_one()
        import json
        import requests
        import uuid
        import datetime
        import logging

        _logger = logging.getLogger(__name__)

        # 1. Tìm Google OAuth Access Token từ Google Calendar / Gmail OAuth Server
        access_token = None

        user = self.env.user
        if hasattr(user, 'google_calendar_account_id') and user.google_calendar_account_id and user.google_calendar_account_id.calendar_token:
            access_token = user.google_calendar_account_id.calendar_token

        if not access_token:
            fetchmail = self.env['fetchmail.server'].sudo().search([('server_type', '=', 'gmail'), ('google_gmail_refresh_token', '!=', False)], limit=1)
            if fetchmail:
                try:
                    access_token, dummy = fetchmail._fetch_gmail_access_token(fetchmail.google_gmail_refresh_token)
                except Exception as err:
                    _logger.warning("Không lấy được access token từ fetchmail: %s", str(err))

        if not access_token:
            mail_server = self.env['ir.mail_server'].sudo().search([('smtp_authentication', '=', 'gmail'), ('google_gmail_refresh_token', '!=', False)], limit=1)
            if mail_server:
                try:
                    access_token, dummy = mail_server._fetch_gmail_access_token(mail_server.google_gmail_refresh_token)
                except Exception as err:
                    _logger.warning("Không lấy được access token từ mail_server: %s", str(err))

        candidate_name = self.application_id.candidate_id.name if self.application_id and self.application_id.candidate_id else "Ứng viên"
        job_name = self.application_id.job_position_id.name if self.application_id and self.application_id.job_position_id else "Tuyển dụng"
        scheduled_dt = self.scheduled_date or fields.Datetime.now()

        # 2. Gọi Google Calendar API để sinh phòng họp Google Meet dùng sau
        if access_token:
            try:
                start_iso = scheduled_dt.isoformat() + 'Z'
                end_iso = (scheduled_dt + datetime.timedelta(hours=1)).isoformat() + 'Z'

                url = "https://www.googleapis.com/calendar/v3/calendars/primary/events?conferenceDataVersion=1"
                headers = {
                    'Authorization': f'Bearer {access_token}',
                    'Content-Type': 'application/json',
                }
                body = {
                    'summary': f"Phỏng vấn {candidate_name} - Vị trí {job_name}",
                    'description': f"Buổi phỏng vấn tuyển dụng vị trí {job_name} cho ứng viên {candidate_name}.",
                    'start': {'dateTime': start_iso},
                    'end': {'dateTime': end_iso},
                    'conferenceData': {
                        'createRequest': {
                            'requestId': f"odoo-meet-{uuid.uuid4().hex[:8]}",
                            'conferenceSolutionKey': {'type': 'hangoutsMeet'},
                        }
                    }
                }
                response = requests.post(url, headers=headers, json=body, timeout=10)
                if response.status_code in (200, 201):
                    res_data = response.json()
                    conf_data = res_data.get('conferenceData', {})
                    entry_points = conf_data.get('entryPoints', [])
                    for ep in entry_points:
                        if ep.get('entryPointType') == 'video':
                            meet_url = ep.get('uri')
                            if meet_url:
                                self.write({
                                    'location': meet_url,
                                    'interview_type': 'online',
                                })
                                self._onchange_interview_fields()
                                return {
                                    'type': 'ir.actions.act_window',
                                    'res_model': 'recruitment.interview.wizard',
                                    'res_id': self.id,
                                    'view_mode': 'form',
                                    'target': 'new',
                                }
                else:
                    _logger.warning("Google Calendar API trả về lỗi code %s: %s", response.status_code, response.text)
            except Exception as e:
                _logger.warning("Lỗi khi gọi Google Calendar API: %s", str(e))

        # 3. Nếu chưa lấy được token hoặc API bị từ chối, báo ValidationError với hướng dẫn chi tiết
        raise ValidationError(
            "Không thể tự động gọi Google Calendar API để tạo phòng họp Google Meet"
        )

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
        interview_stage = self.env.ref('recruitment.stage_first_interview', raise_if_not_found=False)
        if not interview_stage:
            interview_stage = self.env['recruitment.stage'].search([('name', '=ilike', 'First Interview')], limit=1)
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
