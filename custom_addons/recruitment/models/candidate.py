from odoo import models,fields,api
from odoo.exceptions import ValidationError

class RecruitmentCandidate(models.Model):
    _name = "recruitment.candidate"
    _description = "Recruitment Candidate"

    name = fields.Char(
        string="Name",
        required=True,
    )
    
    email = fields.Char(
        string="Email",
        required=True,
    )

    phone = fields.Char(
        string="Phone",
        required=True,
    )

    address = fields.Text(
        string = "Address"
    )

    date_of_birth = fields.Date(
        string = "Date of Birth"
    )

    gender = fields.Selection(
        selection=[
            ('male', 'Male'),
            ('female', 'Female'),
        ],
        string="Gender",
    )

    application_date = fields.Date(
        string="Application Date",
        default=fields.Date.today,
        required=True,
    )

    expected_salary = fields.Float(
        string="Expected Salary",
    )

    experience_years = fields.Float(
        string="Experience Years",
        required=True,
        default=0,
    )

    education = fields.Char(
        string="Education",
    )

    skills = fields.Text(
        string="Skills",
    )

    cv_file = fields.Binary(
        string="CV File",
        attachment=True,
    )

    cv_filename = fields.Char(
        string="CV Filename",
    )

    note = fields.Text(
        string="Note",
    )

    job_position_id = fields.Many2one(
        string="Job Position",
        comodel_name="recruitment.job.position",
    )

    application_ids = fields.One2many(
        comodel_name="recruitment.application",
        inverse_name="candidate_id",
        string="Applications",
    )

    active = fields.Boolean(
        string="Active",
        default=True,
    )

    @api.constrains('email')
    def _check_email(self):
        for record in self:
            if record.email and "@" not in record.email:
                raise ValidationError("Email không hợp lệ!")

    @api.constrains('phone')
    def _check_phone(self):
        for record in self:
            if record.phone and not record.phone.isdigit():
                raise ValidationError("Số điện thoại phải là số!")

    @api.constrains('experience_years')
    def _check_experience_years(self):
        for record in self:
            if record.experience_years < 0:
                raise ValidationError("Số năm kinh nghiệm phải lớn hơn 0!")

    @api.constrains('date_of_birth')
    def _check_dateOfbirth(self):
        for record in self:
            if record.date_of_birth and record.date_of_birth > fields.Date.today():
                raise ValidationError("Ngày sinh phải nhỏ hơn hoặc bằng ngày hiện tại!")