"""Dashboard numbers and charts, computed from the pet tables. The Next Walk schedule is in schedule.py."""
import calendar
import datetime
from statistics import median

from django.db.models import Q, Sum
from django.utils import formats, timezone
from django.utils.translation import gettext as _, gettext_lazy, ngettext

from . import units
from .clock import show_time, uses_24h
from .models import Meal, PottyBreak, Walk

WINDOW_DAYS = 14  # history behind the charts
# Day x hour chart geometry (SVG units).
LABEL_W, PLOT_W, ROW_H, AXIS_H = 46, 254, 14, 16


# --- Formatting --------------------------------------------------------------------------
def fmt_time(dt):
    return show_time(dt)


def fmt_duration(td):
    minutes = int(td.total_seconds() // 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return _("%(hours)dh %(minutes)dm") % {"hours": hours, "minutes": minutes}
    return _("%(minutes)dm") % {"minutes": minutes}


def _plural(n, unit):
    forms = {
        "year": ngettext("%(n)d year", "%(n)d years", n),
        "month": ngettext("%(n)d month", "%(n)d months", n),
        "week": ngettext("%(n)d week", "%(n)d weeks", n),
    }
    return forms[unit] % {"n": n}


def day_label(date):
    return formats.date_format(date, "D j")


# Wording: (name, plural) for each kind.
KINDS = {"pee": (gettext_lazy("Pee"), gettext_lazy("pees")), "poop": (gettext_lazy("Poop"), gettext_lazy("poops"))}


def pet_age(birth):
    """'9 weeks (2 months, 1 week)' under 6 months, '7 months' under a year,
    '1 year, 3 months' under two years, then '2 years'."""
    if not birth:
        return ""
    if isinstance(birth, datetime.datetime):
        birth = timezone.localtime(birth).date()
    today = timezone.localdate()
    months = (today.year - birth.year) * 12 + today.month - birth.month - (today.day < birth.day)
    if months >= 24:
        return _plural(months // 12, "year")
    if months >= 12:
        rest = months - 12
        return _plural(1, "year") + (f", {_plural(rest, 'month')}" if rest else "")
    if months >= 6:
        return _plural(months, "month")
    year, month = divmod(birth.month - 1 + months, 12)
    year += birth.year
    anniversary = datetime.date(year, month + 1, min(birth.day, calendar.monthrange(year, month + 1)[1]))
    weeks = (today - anniversary).days // 7
    total = _plural((today - birth).days // 7, "week")
    if not months:
        return total
    return f"{total} ({_plural(months, 'month')}" + (f", {_plural(weeks, 'week')}" if weeks else "") + ")"


# --- Data access -------------------------------------------------------------------------
def window_start(days=WINDOW_DAYS):
    today = timezone.localdate()
    return timezone.make_aware(datetime.datetime.combine(today - datetime.timedelta(days=days - 1), datetime.time()))


def potty(pet, since, until=None):
    breaks = PottyBreak.objects.filter(pet=pet, time__gte=since)
    if until is not None:
        breaks = breaks.filter(time__lt=until)
    return list(breaks.order_by("time"))


def is_accident(b):
    return b.location == PottyBreak.INSIDE


def feeding_times(pet, since, water=False, until=None):
    """When the pet ate (or, with water=True, ate or drank): its meals only. Filling a bowl says nothing about when,
    or whether, it was eaten."""
    meals = Meal.objects.filter(pet=pet, time__gte=since)
    if until is not None:
        meals = meals.filter(time__lt=until)
    meals = meals.filter(Q(food=True) | Q(water=True)) if water else meals.filter(food=True)
    return sorted(meals.values_list("time", flat=True))


def avg_gap(breaks):
    gaps = [b.time - a.time for a, b in zip(breaks, breaks[1:])]
    return sum(gaps, datetime.timedelta()) / len(gaps) if gaps else None


# --- Cards -------------------------------------------------------------------------------
def potty_bars_card(pet):
    """Past week: pee/poop in the right place vs accidents, one stacked bar per day, scaled to the busiest day."""
    today = timezone.localdate()
    days = []
    breaks = potty(pet, window_start(7))
    # Walks per day sit beside each bar, so a day with fewer walks and more accidents shows on one row.
    walks = list(pet.walks.filter(start__gte=window_start(7)).exclude(end=None))
    for i in range(7):
        date = today - datetime.timedelta(days=i)
        day = [b for b in breaks if timezone.localtime(b.time).date() == date]
        day_walks = [w for w in walks if timezone.localtime(w.start).date() == date]
        walk_time = sum((w.end - w.start for w in day_walks), datetime.timedelta())
        days.append({
            "walks": len(day_walks),
            "walk_time": fmt_duration(walk_time) if day_walks else "",
            "label": day_label(date),
            "pee_ok": sum(b.pee and not is_accident(b) for b in day),
            "poop_ok": sum(b.poop and not is_accident(b) for b in day),
            "pee_in": sum(b.pee and is_accident(b) for b in day),
            "poop_in": sum(b.poop and is_accident(b) for b in day),
        })
    segments = ("pee_ok", "poop_ok", "pee_in", "poop_in")
    peak = max([sum(d[s] for s in segments) for d in days] + [1])
    for d in days:
        for s in segments:
            d[s + "_pct"] = d[s] / peak * 100
    dog = pet.type == pet.DOG
    legend = [("pee_ok", "#8fd18a", _("Pee outside") if dog else _("Pee")),
              ("poop_ok", "#2e7d32", _("Poop outside") if dog else _("Poop")),
              ("pee_in", "#f5d33f", _("Pee accident")), ("poop_in", "#a72431", _("Poop accident"))]
    return {"days": days, "legend": legend, "empty": not breaks, "show_walks": bool(walks)}


# --- Day x hour charts ---------------------------------------------------------------------
def _x(dt):
    local = timezone.localtime(dt)
    return LABEL_W + (local.hour + local.minute / 60) / 24 * PLOT_W


def chart(days=WINDOW_DAYS):
    today = timezone.localdate()
    rows = []
    for i in range(days):
        date = today - datetime.timedelta(days=i)
        rows.append({"date": date, "y": i * ROW_H, "mid": i * ROW_H + ROW_H / 2, "label": day_label(date)})
    height = days * ROW_H
    ticks = [{"x": LABEL_W + h / 24 * PLOT_W, "label": (("00", "06", "12", "18", "24") if uses_24h() else ("12a", "6a", "12p", "6p", "12a"))[h // 6]} for h in range(0, 25, 6)]
    return {"rows": rows, "by_date": {r["date"]: r for r in rows}, "height": height, "total_h": height + AXIS_H,
            "width": LABEL_W + PLOT_W, "plot_x": LABEL_W, "label_x": LABEL_W - 6, "plot_w": PLOT_W, "ticks": ticks}


def _place(c, dt):
    row = c["by_date"].get(timezone.localdate(dt))
    return (round(_x(dt), 1), row["mid"]) if row else None


def accidents_chart(pet):
    c, since = chart(), window_start()
    dots, meals, sleeps = [], [], []
    hours = [0] * 24
    for b in PottyBreak.objects.filter(pet=pet, location=PottyBreak.INSIDE, time__gte=since):
        pos = _place(c, b.time)
        if not pos:
            continue
        hours[timezone.localtime(b.time).hour] += 1
        kind = _("Pee + poop") if b.pee and b.poop else (_("Poop") if b.poop else _("Pee"))
        dots.append({"x": pos[0], "y": pos[1], "color": "#a72431" if b.poop else "#f5d33f",
                     "label": _("%(kind)s inside %(time)s") % {"kind": kind, "time": fmt_time(b.time)}})
    for t in feeding_times(pet, since):
        pos = _place(c, t)
        if pos:
            meals.append({"x": pos[0] - 3, "y": pos[1] - 3})
    for s in pet.sleeps.filter(Q(end__gte=since) | Q(end=None)):
        start, end = timezone.localtime(s.start), timezone.localtime(s.end or timezone.now())
        day = start.date()
        while day <= end.date():
            row = c["by_date"].get(day)
            if row:
                a = max(start, timezone.make_aware(datetime.datetime.combine(day, datetime.time())))
                b = min(end, timezone.make_aware(datetime.datetime.combine(day, datetime.time.max)))
                sleeps.append({"x": round(_x(a), 1), "y": row["y"], "w": round(_x(b) - _x(a), 1)})
            day += datetime.timedelta(days=1)
    peak = max(hours) or 1
    # One column per hour, each exactly as wide as its two-digit label.
    slot = PLOT_W / 24
    hist = [{"x": round(LABEL_W + h * slot + 0.75, 2), "y": 26 - n / peak * 26, "h": n / peak * 26,
             "n": n, "label": f"{h:02d}", "even": h % 2 == 0} for h, n in enumerate(hours)]
    return {"chart": c, "dots": dots, "meals": meals, "sleeps": sleeps, "hist": hist,
            "hist_w": round(slot - 1.5, 2), "row_h": ROW_H, "hide_empty": True,
            "empty": not dots and not PottyBreak.objects.filter(pet=pet, location=PottyBreak.INSIDE,
                                                                 time__gte=since).exists()}


# --- Health (PUP-79) -----------------------------------------------------------------------------
def health_alerts(pet, now=None):
    """Short warnings for the pet page, only when something's off: worrying poop colours, unusual details logged
    in the past day, or far more pees today than this pet's normal."""
    now = now or timezone.now()
    alerts = []
    recent = list(PottyBreak.objects.filter(pet=pet, time__gte=now - datetime.timedelta(hours=24), time__lte=now))
    if any(b.poop_colour in ("black", "red") for b in recent):
        alerts.append(("danger", _("Black or bloody poop in the past day. That can mean bleeding: call your vet.")))
    if any(b.poop_colour in ("grey", "yellow") for b in recent):
        alerts.append(("warning", _("Grey, white or yellow poop in the past day. Worth mentioning to your vet if it "
                                    "happens again.")))
    if sum(1 for b in recent if b.poop_consistency == "soft") >= 2:
        alerts.append(("warning", _("Soft poop more than once in the past day. Keep an eye on water intake; call your "
                                    "vet if it lasts more than a day or comes with vomiting or tiredness.")))
    if sum(1 for b in recent if b.pee_colour == "dark") >= 2:
        alerts.append(("warning", _("Dark pee more than once in the past day. Make sure fresh water is available.")))
    # Pees today against the usual day (median of the past 2 weeks, not counting today).
    today = window_start(1)
    pees_today = PottyBreak.objects.filter(pet=pet, pee=True, time__gte=today, time__lte=now).count()
    days = []
    for back in range(1, WINDOW_DAYS):
        start = today - datetime.timedelta(days=back)
        days.append(PottyBreak.objects.filter(pet=pet, pee=True, time__gte=start,
                                              time__lt=start + datetime.timedelta(days=1)).count())
    logged_days = [d for d in days if d]
    if len(logged_days) >= 5:
        usual = median(logged_days)
        if pees_today >= max(usual * 1.5, usual + 3):
            alerts.append(("warning", _("%(n)d pees today, usually about %(usual)d. Peeing much more often can be a "
                                        "urinary infection: worth a vet call if it continues.") % {
                "n": pees_today, "usual": round(usual)}))
    return alerts


# --- Pet page summary cards (PUP-80) ---------------------------------------------------------------
def status_card(pet, now=None):
    """Where things stand: when to head out, the accident-free stretch, the last pee, poop and meal, sleep."""
    now = now or timezone.now()
    from .schedule import coach, next_walk

    card = next_walk(pet, now=now)
    last_pee = pet.potty_breaks.filter(pee=True, time__lte=now).order_by("-time").first()
    last_poop = pet.potty_breaks.filter(poop=True, time__lte=now).order_by("-time").first()
    last_meal = Meal.objects.filter(pet=pet, food=True, time__lte=now).order_by("-time").first()
    asleep = pet.sleeps.filter(end=None).order_by("-start").first()
    from .progress import streak

    latest = [(label, entry.time, url) for label, entry, url in (
        (_("Pee"), last_pee, last_pee and f"/log/potty/{last_pee.pk}/"),
        (_("Poop"), last_poop, last_poop and f"/log/potty/{last_poop.pk}/"),
        (_("Ate"), last_meal, last_meal and f"/log/meal/{last_meal.pk}/")) if entry]
    actual = {r["key"]: r["actual"] for r in schedule_vs_actual(pet, now=now)}
    return {**card, "latest": latest, "asleep": asleep, "coach": coach(pet, now=now), "streak": streak(pet, now=now),
            "actual": actual,
            "pee_every": pet.pee_minutes, "poop_every": pet.poop_minutes}


def today_card(pet, now=None):
    """Five numbers for today: walks, pees, poops, accidents, meals (or food eaten)."""
    now = now or timezone.now()
    today = window_start(1)
    walks = list(pet.walks.filter(start__gte=today, start__lte=now))
    walk_time = sum(((w.end or now) - w.start for w in walks), datetime.timedelta())
    breaks = potty(pet, today, until=now)
    meals = Meal.objects.filter(pet=pet, food=True, time__gte=today, time__lte=now)
    eaten = meals.aggregate(s=Sum("amount"))["s"]
    pees, poops = sum(b.pee for b in breaks), sum(b.poop for b in breaks)
    accidents, count = sum(1 for b in breaks if is_accident(b)), meals.count()
    return {"tiles": [
        (ngettext("Walk", "Walks", len(walks)), str(len(walks)), fmt_duration(walk_time) if walks else ""),
        (ngettext("Pee", "Pees", pees), str(pees), ""),
        (ngettext("Poop", "Poops", poops), str(poops), ""),
        (ngettext("Accident", "Accidents", accidents), str(accidents), ""),
        (_("Eaten"), units.show(eaten, "mass", pet.household), "") if eaten else
        (ngettext("Meal", "Meals", count), str(count), ""),
    ]}


def averages(pet, now=None):
    """The longer-run numbers that used to be on the pet page, for the Progress page."""
    now = now or timezone.now()
    recent, window = potty(pet, now - datetime.timedelta(days=3), until=now), potty(pet, window_start(), until=now)
    gap3, gap14 = avg_gap(recent), avg_gap(window)
    rows = []
    if window:
        days = (timezone.localdate(now) - timezone.localtime(window[0].time).date()).days + 1
        rows += [(_("Pees per day"), f"{sum(b.pee for b in window) / days:.1f}"),
                 (_("Poops per day"), f"{sum(b.poop for b in window) / days:.1f}")]
    if gap3:
        rows.append((_("Time between potty breaks (3 days)"), fmt_duration(gap3)))
    if gap14:
        rows.append((_("Time between potty breaks (2 weeks)"), fmt_duration(gap14)))
    walks = [w.end - w.start for w in Walk.objects.filter(pets=pet, start__gte=window_start(), end__lte=now)
             if w.end > w.start]
    if walks:
        rows.append((_("Usual walk"), fmt_duration(median(walks))))
    return rows


# --- Patterns (Progress page) ---------------------------------------------------------------------
PARTS = {"morning": gettext_lazy("morning"), "afternoon": gettext_lazy("afternoon"), "evening": gettext_lazy("evening")}
GAP_MIN, GAP_MAX = datetime.timedelta(minutes=30), datetime.timedelta(hours=4)  # top-ups / naps or missed entries


def day_part(dt):
    """Morning (before noon), afternoon (noon to 5 PM) or evening."""
    hour = timezone.localtime(dt).hour
    return "morning" if hour < 12 else "afternoon" if hour < 17 else "evening"


def pee_gaps(pet, now):
    """Each gap from one pee to the next in the past two weeks (30 minutes to 4 hours, not from a bedtime pee), with
    where the second one happened: [(start, gap, accident)]."""
    from .schedule import QuietHours

    quiet = QuietHours(pet.household)
    pees = [b for b in potty(pet, window_start(), until=now) if b.pee and not b.found_later]
    return [(a.time, b.time - a.time, is_accident(b)) for a, b in zip(pees, pees[1:])
            if not quiet.is_bedtime(a.time) and GAP_MIN <= b.time - a.time <= GAP_MAX]


def schedule_vs_actual(pet, now=None, days=7):
    """Pee every and poop every next to how far apart they really were over the past week (PUP-132). Top-ups (under
    30 minutes), the overnight gap and missed entries (pee over 6 hours, poop over 12) are left out; an average
    needs 3 gaps. Returns [{key, label, set, actual, diff, shorter}]."""
    from .schedule import QuietHours, fmt_minutes, fmt_span

    now = now or timezone.now()
    quiet = QuietHours(pet.household)
    breaks = [b for b in potty(pet, now - datetime.timedelta(days=days), until=now) if not b.found_later]
    rows = []
    for key, label, every, longest in (("pee", _("Pee"), pet.pee_minutes, 6), ("poop", _("Poop"), pet.poop_minutes, 12)):
        times = [b.time for b in breaks if getattr(b, key)]
        gaps = [b - a for a, b in zip(times, times[1:])
                if not quiet.is_bedtime(a) and GAP_MIN <= b - a <= datetime.timedelta(hours=longest)]
        actual = sum(gaps, datetime.timedelta()) / len(gaps) if len(gaps) >= 3 else None
        diff = shorter = None
        if actual:
            minutes = round(actual.total_seconds() / 60) - every
            shorter = minutes < 0
            diff = (_("about the same") if abs(minutes) < 10 else
                    (_("%(time)s shorter") if shorter else _("%(time)s longer")) % {"time": fmt_minutes(abs(minutes))})
        rows.append({"key": key, "label": label, "set": fmt_minutes(every), "actual": fmt_span(actual) if actual else None,
                     "diff": diff, "shorter": shorter})
    return rows


def by_part_of_day(pet, now=None):
    """Morning, afternoon and evening side by side: the average gap between outdoor pees that worked, and the
    accidents there (pee and poop, past two weeks). Plus a headline when pee accidents cluster."""
    now = now or timezone.now()
    gaps = pee_gaps(pet, now)
    breaks = [b for b in potty(pet, window_start(), until=now) if is_accident(b)]
    rows = []
    for part in PARTS:
        worked = [g for t, g, accident in gaps if not accident and day_part(t) == part]
        rows.append({
            "part": PARTS[part], "key": part,
            "usual": fmt_duration(sum(worked, datetime.timedelta()) / len(worked)) if len(worked) >= 3 else None,
            "pee_accidents": sum(1 for b in breaks if b.pee and day_part(b.time) == part),
            "poop_accidents": sum(1 for b in breaks if b.poop and day_part(b.time) == part),
        })
    total = sum(r["pee_accidents"] for r in rows)
    ranked = sorted(rows, key=lambda r: r["pee_accidents"], reverse=True)
    headline = None
    if ranked[0]["pee_accidents"] >= 3 and ranked[0]["pee_accidents"] > ranked[1]["pee_accidents"]:
        headline = _("Most pee accidents are in the %(part)s: %(n)d of %(total)d in the past two weeks.") % {
            "part": ranked[0]["part"], "n": ranked[0]["pee_accidents"], "total": total}
    return {"rows": rows, "headline": headline}


def hold_table(pet, now=None):
    """How long after a pee the next one came, and where: rows of half-hour steps with outside and accident
    counts (past two weeks). Shows where accidents start."""
    now = now or timezone.now()
    gaps = pee_gaps(pet, now)
    if not any(accident for _t, _g, accident in gaps):
        return []
    rows = []
    for start in range(30, 240, 30):
        lo, hi = datetime.timedelta(minutes=start), datetime.timedelta(minutes=start + 30)
        here = [accident for _t, g, accident in gaps if lo <= g < hi or (hi == GAP_MAX and g == hi)]
        if here:
            rows.append({"label": f"{fmt_duration(lo)}–{fmt_duration(hi)}", "outside": here.count(False),
                         "accidents": here.count(True)})
    return rows
