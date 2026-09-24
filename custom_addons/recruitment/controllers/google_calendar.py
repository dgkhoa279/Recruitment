from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from odoo import http
from odoo.addons.google_calendar.controllers.main import GoogleCalendarController


GOOGLE_MEET_CREATE_SCOPE = (
    "https://www.googleapis.com/auth/meetings.space.created"
)


class RecruitmentGoogleCalendarController(GoogleCalendarController):
    """Request the Meet scope together with Odoo's normal Calendar scope."""

    @http.route()
    def google_calendar_sync_data(self, model, **kw):
        result = super().google_calendar_sync_data(model, **kw)
        if result.get("status") != "need_auth" or not result.get("url"):
            return result

        parsed = urlsplit(result["url"])
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        scopes = query.get("scope", "").split()
        if GOOGLE_MEET_CREATE_SCOPE not in scopes:
            scopes.append(GOOGLE_MEET_CREATE_SCOPE)
            query["scope"] = " ".join(scopes)
            result["url"] = urlunsplit(
                (
                    parsed.scheme,
                    parsed.netloc,
                    parsed.path,
                    urlencode(query),
                    parsed.fragment,
                )
            )
        return result
