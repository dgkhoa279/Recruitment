from odoo import models, fields, api
from odoo.exceptions import ValidationError
import base64
import datetime
from .application_email_parser import ApplicationEmailParser


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
        return records

    def write(self, vals):
        res = super().write(vals)
        if 'cv_file' in vals or 'cv_filename' in vals:
            self._sync_cv_attachment()
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
        Lọc email đầu vào: Chỉ điều hướng các email có tiêu đề chứa [APPLICATION].
        Tất cả các email khác (spam, thông báo...) sẽ bị bỏ qua hoàn toàn, không tạo bản ghi rác.
        """
        subject = message_dict.get('subject', '') or ''
        if '[APPLICATION]' not in subject.upper():
            return []
        return super().message_route(message, message_dict, model=model, thread_id=thread_id, custom_values=custom_values)

    @api.model
    def message_new(self, msg_dict, custom_values=None):
        """
        Tự động kiểm tra / tạo Candidate và Application khi nhận Email ứng tuyển.
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

        # 1. Tìm hoặc tạo Vị trí tuyển dụng (Job Position)
        job_position = self.env['recruitment.job.position'].search(
            [('name', '=ilike', job_position_name)],
            limit=1,
        )
        if not job_position:
            job_position = self.env['recruitment.job.position'].create({
                'name': job_position_name,
            })

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