from odoo import models, fields, api
from odoo.exceptions import ValidationError


class JobPosition(models.Model):
    _name = "recruitment.job.position"
    _description = "Job Position"

    name = fields.Char(
        string="Job Position",
        required=True,
    )

    description = fields.Text(
        string="Description",
    )

    quantity = fields.Integer(
        string="Target Quantity",
        default=1,
    )

    hired_quantity = fields.Integer(
        string="Hired Quantity",
        compute="_compute_hired_quantity",
        store=True,
    )

    application_ids = fields.One2many(
        "recruitment.application",
        "job_position_id",
        string="Applications",
    )

    deadline = fields.Date(
        string="Deadline",
    )

    active = fields.Boolean(
        string="Active",
        default=True,
    )

    required_skills = fields.Char(
        string="Required Skills",
        help="Danh sách kỹ năng / từ khóa bắt buộc (ví dụ: Python, Odoo, HTML, CSS, SQL)",
    )

    min_experience_years = fields.Float(
        string="Min Experience (Years)",
        default=0.0,
    )

    employment_type = fields.Selection(
        [
            ("full_time", "Toàn thời gian (Full-time)"),
            ("part_time", "Bán thời gian (Part-time)"),
            ("contract", "Hợp đồng (Contract)"),
            ("internship", "Thực tập (Internship)"),
        ],
        string="Employment Type",
        default="full_time",
        required=True,
    )

    workplace_type = fields.Selection(
        [
            ("on_site", "Tại văn phòng (On-site)"),
            ("remote", "Làm từ xa (Remote)"),
            ("hybrid", "Linh hoạt (Hybrid)"),
        ],
        string="Workplace Type",
        default="on_site",
        required=True,
    )

    job_level = fields.Selection(
        [
            ("intern", "Intern / Thực tập sinh"),
            ("junior", "Junior"),
            ("middle", "Middle"),
            ("senior", "Senior"),
            ("lead", "Team Lead / Quản lý chuyên môn"),
            ("manager", "Manager / Trưởng phòng"),
        ],
        string="Job Level",
        default="middle",
        required=True,
    )

    salary_min = fields.Float(
        string="Minimum Salary (VNĐ)",
        default=0.0,
    )

    salary_max = fields.Float(
        string="Maximum Salary (VNĐ)",
        default=0.0,
    )

    hr_responsible_id = fields.Many2one(
        "res.users",
        string="HR Responsible",
        default=lambda self: self.env.user,
    )

    _sql_constraints = [
        ("unique_name", "unique(name)", "Tên vị trí này đã tồn tại !"),
    ]

    @api.depends("application_ids.stage_id", "application_ids.active")
    def _compute_hired_quantity(self):
        hired_stage = self.env["recruitment.stage"].search(
            [("name", "=ilike", "hired")], limit=1
        )
        for record in self:
            if hired_stage:
                record.hired_quantity = len(
                    record.application_ids.filtered(
                        lambda a: a.stage_id.id == hired_stage.id
                    )
                )
            else:
                record.hired_quantity = 0

    @api.constrains("quantity")
    def _check_quantity(self):
        for record in self:
            if record.quantity <= 0:
                raise ValidationError("Số lượng tuyển dụng phải lớn hơn 0 !")

    @api.constrains("deadline")
    def _check_deadline(self):
        for record in self:
            if record.deadline and record.deadline < fields.Date.today():
                raise ValidationError("Ngày hết hạn phải lớn hơn ngày hiện tại !")

    @api.constrains("salary_min", "salary_max")
    def _check_salary_range(self):
        for record in self:
            if (
                record.salary_min > 0
                and record.salary_max > 0
                and record.salary_max < record.salary_min
            ):
                raise ValidationError("Lương tối đa không được nhỏ hơn lương tối thiểu!")