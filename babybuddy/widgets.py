import datetime

from django.forms import widgets


class DateTimeBaseInput(widgets.DateTimeBaseInput):
    def format_value(self, value):
        if isinstance(value, datetime.datetime):
            value = value.isoformat()
        return value


class DateTimeInput(widgets.MultiWidget):
    """Housetrained (PUP-134): a date field and a time field side by side, so changing the time doesn't mean
    picking the date again. Posts name_date and name_time; a single combined value under the plain name (API,
    imports, tests) still works."""

    template_name = "babybuddy/widgets/split_datetime.html"

    def __init__(self, attrs=None):
        super().__init__({"date": widgets.DateInput(attrs={"type": "date"}),
                          "time": widgets.TimeInput(attrs={"type": "time", "step": 60})}, attrs)

    def decompress(self, value):
        if isinstance(value, str):
            try:
                value = datetime.datetime.fromisoformat(value)
            except ValueError:
                return [None, None]
        if isinstance(value, datetime.datetime):
            return [value.date().isoformat(), value.strftime("%H:%M")]
        return [None, None]

    def value_from_datadict(self, data, files, name):
        if name in data:
            return data.get(name)
        date, time = data.get(f"{name}_date"), data.get(f"{name}_time")
        if not date and not time:
            return None
        if not date:  # a time on its own is today
            from django.utils import timezone
            date = timezone.localdate().isoformat()
        return f"{date} {time or ''}".strip()

    def value_omitted_from_data(self, data, files, name):
        return name not in data and f"{name}_date" not in data and f"{name}_time" not in data


class DateInput(DateTimeBaseInput):
    input_type = "date"


class TimeInput(DateTimeBaseInput):
    input_type = "time"
