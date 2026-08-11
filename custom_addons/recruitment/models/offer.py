from odoo import models, fields, api
from odoo.exceptions import ValidationError


class RecruitmentOffer(models.Model):
    _name = "recruitment.offer"
    _description = "Recruitment Job Offer"
    _order = "offer_date desc"

    _sql_constraints = [
        (
            "salary_positive",
            "CHECK(proposed_salary > 0)",
            "Proposed salary must be greater than 0.",
        ),
        (
            "offer_name_unique",
            "UNIQUE(name)",
            "Offer name must be unique.",
        ),
    ]

    name = fields.Char(
        string="Offer Reference",
        required=True,
    )

    application_id = fields.Many2one(
        "recruitment.application",
        string="Application",
        required=True,
        ondelete="cascade",
    )

    candidate_id = fields.Many2one(
        related="application_id.candidate_id",
        string="Candidate",
        store=True,
        readonly=True,
    )

    job_position_id = fields.Many2one(
        related="application_id.job_position_id",
        string="Job Position",
        store=True,
        readonly=True,
    )

    offer_date = fields.Date(
        string="Offer Date",
        required=True,
        default=fields.Date.context_today,
    )

    expiration_date = fields.Date(
        string="Expiration Date",
        required=True,
    )

    start_date = fields.Date(
        string="Expected Start Date",
    )

    proposed_salary = fields.Float(
        string="Proposed Salary",
        required=True,
    )

    salary_period = fields.Selection(
        [
            ("monthly", "Monthly"),
            ("yearly", "Yearly"),
        ],
        string="Salary Period",
        default="monthly",
        required=True,
    )

    status = fields.Selection(
        [
            ("draft", "Draft"),
            ("sent", "Sent"),
            ("accepted", "Accepted"),
            ("rejected", "Rejected"),
            ("expired", "Expired"),
        ],
        string="Status",
        default="draft",
        required=True,
    )

    note = fields.Text(
        string="Note",
    )

    # =========================================================
    # CONSTRAINTS
    # =========================================================

    @api.constrains("offer_date", "expiration_date")
    def _check_expiration_date(self):
        for record in self:
            if (
                record.offer_date
                and record.expiration_date
                and record.expiration_date < record.offer_date
            ):
                raise ValidationError(
                    "Expiration date cannot be before the offer date."
                )

    @api.constrains("offer_date", "start_date")
    def _check_start_date(self):
        for record in self:
            if (
                record.offer_date
                and record.start_date
                and record.start_date < record.offer_date
            ):
                raise ValidationError(
                    "Start date cannot be before the offer date."
                )

    @api.constrains("status", "start_date")
    def _check_accepted_offer(self):
        for record in self:
            if record.status == "accepted" and not record.start_date:
                raise ValidationError(
                    "An accepted offer must have a start date."
                )