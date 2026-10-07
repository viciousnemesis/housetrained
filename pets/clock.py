"""12- or 24-hour times (PUP-9). Each user picks in their settings; "automatic" follows their language."""
from contextvars import ContextVar

from django.utils import formats, timezone

_hour24 = ContextVar("hour24", default=None)


def uses_24h():
    chosen = _hour24.get()
    if chosen is not None:
        return chosen
    time_format = formats.get_format("TIME_FORMAT")
    return "H" in time_format or "G" in time_format


def time_format():
    return "H:i" if uses_24h() else "g:i A"


def show_time(dt):
    return formats.date_format(timezone.localtime(dt), time_format()) if dt else ""


def show_datetime(dt):
    return formats.date_format(timezone.localtime(dt), "M j, " + time_format()) if dt else ""


class ClockMiddleware:
    """Remembers the user's time format for this request. Runs after the language and time zone middleware."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        settings_ = getattr(request.user, "settings", None) if request.user.is_authenticated else None
        clock = getattr(settings_, "clock", "") or ""
        token = _hour24.set({"24": True, "12": False}.get(clock))
        try:
            return self.get_response(request)
        finally:
            _hour24.reset(token)
