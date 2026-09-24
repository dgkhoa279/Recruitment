from datetime import timedelta
import logging

from markupsafe import Markup, escape

from odoo import Command, models, fields, api
from odoo.exceptions import ValidationError


_logger = logging.getLogger(__name__)


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

    interview_round = fields.Selection(
        [
            ("round_1", "Vòng 1 (First Interview)"),
            ("round_2", "Vòng 2 (Second Interview)"),
        ],
        string="Vòng phỏng vấn",
        default="round_1",
        required=True,
    )

    location = fields.Char(
        string="Location",
    )

    interviewer = fields.Char(
        string="Interviewer",
    )

    interviewer_user_id = fields.Many2one(
        "res.users",
        string="Interviewer User",
        help="Odoo user who organizes the interview calendar event.",
    )

    calendar_event_id = fields.Many2one(
        "calendar.event",
        string="Calendar Event",
        readonly=True,
        copy=False,
        ondelete="set null",
        help="Calendar event synchronized with the interview schedule.",
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

    def _prepare_calendar_event_values(self, include_partners=True):
        self.ensure_one()
        organizer = (
            self.interviewer_user_id
            or self.application_id.interviewer_user_id
            or self.env.user
        )
        location = self.location or ""
        round_label = (
            "Vòng 1 (Sơ tuyển)"
            if self.interview_round == "round_1"
            else "Vòng 2 (Chuyên sâu)"
        )
        description = Markup(
            """
            <p><strong>Lịch phỏng vấn tuyển dụng</strong></p>
            <ul>
                <li><strong>Ứng viên:</strong> {candidate}</li>
                <li><strong>Vị trí:</strong> {job}</li>
                <li><strong>Vòng phỏng vấn:</strong> {round_label}</li>
                <li><strong>Người phỏng vấn:</strong> {interviewer}</li>
                <li><strong>Địa điểm / Link họp:</strong> {location}</li>
            </ul>
            """
        ).format(
            candidate=escape(self.candidate_id.name or "Ứng viên"),
            job=escape(self.job_position_id.name or "Vị trí tuyển dụng"),
            round_label=escape(round_label),
            interviewer=escape(self.interviewer or organizer.name),
            location=escape(location),
        )
        values = {
            "name": (
                f"Phỏng vấn {self.candidate_id.name} - "
                f"Vị trí {self.job_position_id.name}"
            ),
            "start": self.scheduled_date,
            "stop": self.scheduled_date + timedelta(hours=1),
            "user_id": organizer.id,
            "location": location,
            "description": description,
            "show_as": "busy",
            "active": self.status != "cancelled",
            "res_model_id": self.env["ir.model"]._get_id(self._name),
            "res_id": self.id,
        }
        if include_partners:
            values["partner_ids"] = [Command.set(organizer.partner_id.ids)]
        if location.startswith("https://meet.google.com/"):
            values["videocall_location"] = location
        return values

    def _create_calendar_event(self):
        """Create exactly one calendar event for each scheduled interview."""
        self.ensure_one()
        if self.calendar_event_id:
            self._sync_calendar_event(sync_partners=True)
            return self.calendar_event_id

        event = self.env["calendar.event"].with_context(
            no_mail_to_attendees=True,
            send_updates=False,
        ).create(self._prepare_calendar_event_values())
        # calendar.event creates attendee records and its M2M relation in the
        # same operation, but the returned record can still carry the empty
        # pre-create partner_ids cache. Refresh it for callers in this
        # transaction and for the values prepared by subsequent sync writes.
        event.invalidate_recordset(["partner_ids", "attendee_ids"])
        self.with_context(skip_interview_calendar_sync=True).write({
            "calendar_event_id": event.id,
        })
        return event

    def _sync_calendar_event(self, sync_partners=False):
        """Keep the linked Odoo/Google calendar event aligned with Interview."""
        for interview in self.filtered("calendar_event_id"):
            event = interview.calendar_event_id
            organizer = (
                interview.interviewer_user_id
                or interview.application_id.interviewer_user_id
                or interview.env.user
            )
            replace_partners = sync_partners and event.user_id != organizer
            if replace_partners:
                # The Calendar model derives attendee rows from partner_ids.
                # Remove the old organizer attendee first to avoid duplicates.
                event.attendee_ids.unlink()
            event.with_context(
                no_mail_to_attendees=True,
                send_updates=False,
            ).write(interview._prepare_calendar_event_values(
                include_partners=replace_partners,
            ))

    @api.model
    def _backfill_future_calendar_events(self):
        """Create missing events for interviews made before this integration."""
        interviews = self.search([
            ("calendar_event_id", "=", False),
            ("scheduled_date", ">=", fields.Datetime.now()),
            ("status", "=", "scheduled"),
        ])
        for interview in interviews:
            try:
                if (
                    not interview.interviewer_user_id
                    and interview.application_id.interviewer_user_id
                ):
                    interview.with_context(
                        skip_interview_calendar_sync=True,
                    ).interviewer_user_id = (
                        interview.application_id.interviewer_user_id
                    )
                interview._create_calendar_event()
            except Exception:
                # One malformed legacy interview must not block the module
                # upgrade. The error remains visible in the server log.
                _logger.exception(
                    "Could not create calendar event for interview %s",
                    interview.id,
                )
        return True

    def unlink(self):
        calendar_events = self.calendar_event_id
        result = super().unlink()
        calendar_events.exists().with_context(
            no_mail_to_attendees=True,
            send_updates=False,
        ).unlink()
        return result

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

            # Nếu có bất kỳ Interview nào Fail -> Lưu trữ hồ sơ (active = False)
            if any(item.result == "fail" for item in interviews):
                application.write({'active': False})
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

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._update_application_stage()
        for interview in records:
            interview._create_calendar_event()
        return records

    def write(self, vals):
        res = super().write(vals)
        self._update_application_stage()
        synced_fields = {
            "name",
            "scheduled_date",
            "interview_type",
            "interview_round",
            "location",
            "interviewer",
            "interviewer_user_id",
            "status",
        }
        if (
            not self.env.context.get("skip_interview_calendar_sync")
            and synced_fields.intersection(vals)
        ):
            self._sync_calendar_event(
                sync_partners="interviewer_user_id" in vals,
            )
        return res
