from odoo import models, fields,api
from odoo.exceptions import ValidationError

class RecruitmentApplication(models.Model):
    _name = "recruitment.application"
    _description = "Recruitment Application"
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