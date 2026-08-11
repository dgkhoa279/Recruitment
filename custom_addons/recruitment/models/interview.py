from odoo import models, fields, api
from odoo.exceptions import ValidationError


class RecruitmentInterview(models.Model):
    _name = "recruitment.interview"
    _description = "Recruitment Interview"
    _order = "scheduled_date desc"

    _sql_constraints = [
        (
            "name_unique",
            "UNIQUE(name)",
            "Interview name must be unique.",
        ),
        (
            "rating_valid",
            "CHECK(rating IS NULL OR rating IN ('1', '2', '3', '4', '5'))",
            "Rating must be between 1 and 5.",
        ),
    ]

    name = fields.Char(
        string="Interview",
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

    scheduled_date = fields.Datetime(
        string="Scheduled Date",
        required=True,
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
        string="Location",
    )

    interviewer = fields.Char(
        string="Interviewer",
    )

    status = fields.Selection(
        [
            ("scheduled", "Scheduled"),
            ("completed", "Completed"),
            ("cancelled", "Cancelled"),
        ],
        string="Status",
        default="scheduled",
        required=True,
    )

    rating = fields.Selection(
        [
            ("1", "1 - Very Poor"),
            ("2", "2 - Poor"),
            ("3", "3 - Average"),
            ("4", "Good"),
            ("5", "Excellent"),
        ],
        string="Rating",
    )

    result = fields.Selection(
        [
            ("pass", "Pass"),
            ("fail", "Fail"),
            ("pending", "Pending"),
        ],
        string="Result",
        default="pending",
    )

    feedback = fields.Text(
        string="Feedback",
    )

    note = fields.Text(
        string="Note",
    )

    # =========================================================
    # CONSTRAINTS
    # =========================================================

    @api.constrains("scheduled_date")
    def _check_scheduled_date(self):
        """
        Interview date cannot be in the past.
        """
        now = fields.Datetime.now()

        for record in self:
            if record.scheduled_date and record.scheduled_date < now:
                raise ValidationError(
                    "Interview date cannot be in the past."
                )

    @api.constrains("status", "rating", "result")
    def _check_completed_interview(self):
        """
        A completed interview must have:
        - rating
        - result
        """
        for record in self:
            if record.status == "completed":

                if not record.rating:
                    raise ValidationError(
                        "A completed interview must have a rating."
                    )

                if not record.result or record.result == "pending":
                    raise ValidationError(
                        "A completed interview must have a final result."
                    )

    @api.constrains("result", "rating")
    def _check_result_rating(self):
        """
        Business rule:
        - Pass requires rating >= 3
        - Fail requires rating <= 2
        """
        for record in self:

            if not record.rating:
                continue

            rating = int(record.rating)

            if record.result == "pass" and rating < 3:
                raise ValidationError(
                    "An interview cannot be marked as Pass "
                    "with a rating below 3."
                )

            if record.result == "fail" and rating > 2:
                raise ValidationError(
                    "An interview cannot be marked as Fail "
                    "with a rating above 2."
                )

    @api.constrains("interview_type", "location")
    def _check_location(self):
        """
        Offline interviews must have a location.
        """
        for record in self:

            if (
                record.interview_type == "offline"
                and not record.location
            ):
                raise ValidationError(
                    "Offline interviews must have a location."
                )

    @api.constrains("interview_type", "location")
    def _check_online_location(self):
        """
        Online interviews must have a meeting URL/location.
        """
        for record in self:

            if (
                record.interview_type == "online"
                and not record.location
            ):
                raise ValidationError(
                    "Online interviews must have a meeting URL."
                )

    @api.constrains("status", "result")
    def _check_cancelled_interview(self):
        """
        Cancelled interviews cannot have Pass/Fail result.
        """
        for record in self:

            if (
                record.status == "cancelled"
                and record.result in ("pass", "fail")
            ):
                raise ValidationError(
                    "A cancelled interview cannot have "
                    "a Pass or Fail result."
                )

    def _update_application_stage(self):
        for interview in self:
            application = interview.application_id

            if not application:
                continue

            interviews = application.interview_ids

            # Nếu có bất kỳ Interview nào Fail -> Chuyển stage sang Rejected
            if any(item.result == "fail" for item in interviews):
                rejected_stage = self.env["recruitment.stage"].search(
                    [("name", "=ilike", "Rejected")],
                    limit=1,
                )

                if rejected_stage:
                    application.stage_id = rejected_stage

                continue

            # Nếu vẫn còn Interview Pending
            if any(item.result == "pending" for item in interviews):
                continue

            # Tất cả Interview đều Pass -> Chuyển stage sang Offer
            if interviews and all(
                item.result == "pass" for item in interviews
            ):
                offer_stage = self.env["recruitment.stage"].search(
                    [("name", "=ilike", "Offer")],
                    limit=1,
                )

                if offer_stage:
                    application.stage_id = offer_stage

    @api.onchange("result")
    def _onchange_result(self):
        self._update_application_stage()

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._update_application_stage()
        return records

    def write(self, vals):
        res = super().write(vals)
        self._update_application_stage()
        return res