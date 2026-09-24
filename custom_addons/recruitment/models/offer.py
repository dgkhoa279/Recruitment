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
            "Offer reference must be unique.",
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


    @api.constrains("status", "start_date")
    def _check_rejected_offer(self):
        for record in self:
            if record.status == "rejected" and record.start_date:
                raise ValidationError(
                    "A rejected offer cannot have a start date."
                )


    @api.constrains("status", "start_date")
    def _check_expired_offer(self):
        for record in self:
            if record.status == "expired" and record.start_date:
                raise ValidationError(
                    "An expired offer cannot have a start date."
                )


    @api.constrains("offer_date")
    def _check_offer_date(self):
        for record in self:
            if not record.offer_date:
                raise ValidationError(
                    "Offer date is required."
                )

    @api.constrains("application_id", "status")
    def _check_active_offer(self):
        active_statuses = ("draft", "sent", "accepted")

        for record in self:
            if (
                not record.application_id
                or record.status not in active_statuses
            ):
                continue

            existing_offer = self.search(
                [
                    ("application_id", "=", record.application_id.id),
                    ("id", "!=", record.id),
                    ("status", "in", active_statuses),
                ],
                limit=1,
            )

            if existing_offer:
                raise ValidationError(
                    "This application already has an active offer."
                )

    def _update_application_stage(self):
        for offer in self:
            application = offer.application_id
            if not application:
                continue

            if offer.status == "accepted":
                hired_stage = self.env["recruitment.stage"].search(
                    [("name", "=ilike", "Hired")],
                    limit=1,
                )
                if hired_stage:
                    application.stage_id = hired_stage
            elif offer.status == "rejected":
                application.write({'active': False})

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._update_application_stage()
        return records

    def write(self, vals):
        res = super().write(vals)
        self._update_application_stage()
        return res