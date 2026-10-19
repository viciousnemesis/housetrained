"""Time zones for a dropdown (PUP-141): sorted west to east by their offset right now, so UTC-12 is at the top, UTC in
the middle and UTC+14 at the bottom, with the offset in front of each name."""
import datetime
import zoneinfo

from django.conf import settings
from django.forms import widgets

AREAS = ("Africa/", "America/", "Antarctica/", "Asia/", "Atlantic/", "Australia/", "Europe/", "Indian/", "Pacific/")
FALLBACK = "America/New_York"  # no better guess: Eastern


def offset_label(offset):
    minutes = int(offset.total_seconds() // 60)
    hours, minutes = divmod(abs(minutes), 60)
    return f"UTC{'-' if offset < datetime.timedelta() else '+'}{hours:02d}:{minutes:02d}"


def choices(current=None):
    now = datetime.datetime.now(datetime.timezone.utc)
    names = {name for name in zoneinfo.available_timezones() if name.startswith(AREAS)} | {"UTC"}
    if current:
        names.add(current)  # an older pick outside the list stays selectable
    rows = []
    for name in names:
        try:
            rows.append((now.astimezone(zoneinfo.ZoneInfo(name)).utcoffset(), name))
        except (zoneinfo.ZoneInfoNotFoundError, ValueError):
            continue
    rows.sort()
    return [(name, f"{offset_label(offset)}  {name.replace('_', ' ')}") for offset, name in rows]


def default():
    """The server's zone, unless it's plain UTC (a container default), then Eastern."""
    return settings.TIME_ZONE if settings.TIME_ZONE and settings.TIME_ZONE != "UTC" else FALLBACK


class TimeZoneSelect(widgets.Select):
    """A time zone dropdown. With guess=True (nothing picked yet), the page swaps in the device's own zone; that's
    worked out in the browser, nothing is sent anywhere."""

    template_name = "housetrained/widgets/time_zone_select.html"

    def __init__(self, attrs=None, choices=(), guess=False):
        super().__init__(attrs, choices)
        self.guess = guess

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context["widget"]["guess"] = self.guess
        return context
