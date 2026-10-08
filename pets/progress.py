"""Progress (PUP-73): is house-training getting better? Streaks and week-by-week numbers."""
import datetime

from django.utils import timezone
from django.utils.translation import gettext as _, ngettext

from .insights import fmt_duration
from .models import PottyBreak

WEEKS = 8


def _days(td):
    days = td.days
    if days >= 1:
        return ngettext("%(n)d day", "%(n)d days", days) % {"n": days}
    return fmt_duration(td)


def streak(pet, now=None):
    """Time since the last accident (or since logging began), and the best accident-free stretch so far."""
    now = now or timezone.now()
    breaks = list(PottyBreak.objects.filter(pet=pet, time__lte=now).order_by("time").values_list("time", "location"))
    if not breaks:
        return None
    accidents = [t for t, location in breaks if location == PottyBreak.INSIDE]
    start = breaks[0][0]
    current = now - (accidents[-1] if accidents else start)
    marks = [start] + accidents + [now]
    best = max(b - a for a, b in zip(marks, marks[1:]))
    return {
        "current": current, "current_text": _days(current), "best": best, "best_text": _days(best),
        "is_best": current >= best, "accidents_ever": len(accidents),
        "title": _("%(time)s accident-free") % {"time": _days(current)} if current >= datetime.timedelta(hours=1)
        else _("Accident just now"),
    }


def weeks(pet, now=None, count=WEEKS):
    """The last `count` weeks, newest first: 7-day periods ending today."""
    now = now or timezone.now()
    today = timezone.localdate(now)
    rows = []
    for back in range(count):
        end_day = today - datetime.timedelta(days=7 * back)
        start_day = end_day - datetime.timedelta(days=6)
        start = timezone.make_aware(datetime.datetime.combine(start_day, datetime.time()))
        end = timezone.make_aware(datetime.datetime.combine(end_day + datetime.timedelta(days=1), datetime.time()))
        breaks = list(PottyBreak.objects.filter(pet=pet, time__gte=start, time__lt=min(end, now)).order_by("time"))
        accidents = [b for b in breaks if b.location == PottyBreak.INSIDE]
        rows.append({
            "start": start_day, "end": end_day, "breaks": len(breaks), "accidents": len(accidents),
            "right_place": round(100 * (len(breaks) - len(accidents)) / len(breaks)) if breaks else None,
        })
    return rows


def summary(pet, now=None):
    """One line for the weekly notification: this week against the week before."""
    this, last = weeks(pet, now, count=2)
    if not this["breaks"]:
        return None
    text = ngettext("%(n)d accident this week", "%(n)d accidents this week", this["accidents"]) % {"n": this["accidents"]}
    if last["breaks"]:
        text += " " + _("(%(n)d the week before)") % {"n": last["accidents"]}
    return text + " · " + _("%(pct)d%% in the right place") % {"pct": this["right_place"]}


BAR_W, GAP, PLOT_H, LABEL_H = 34, 10, 100, 16


def page(pet):
    rows = weeks(pet)
    bars = []
    for i, r in enumerate(r for r in reversed(rows) if r["right_place"] is not None):
        h = PLOT_H * r["right_place"] / 100
        bars.append({"x": i * (BAR_W + GAP), "y": PLOT_H - h, "h": h, "w": BAR_W, "pct": r["right_place"],
                     "mid": i * (BAR_W + GAP) + BAR_W / 2, "label": r["end"]})
    # Always room for WEEKS bars, so one week of data doesn't become one giant bar.
    chart = {"bars": bars, "width": WEEKS * (BAR_W + GAP) - GAP, "plot_h": PLOT_H,
             "height": PLOT_H + LABEL_H, "label_y": PLOT_H + LABEL_H - 3}
    return {"streak": streak(pet), "weeks": rows, "chart": chart if bars else None}
