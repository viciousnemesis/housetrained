"""Dashboard numbers, charts and the Next Walk prediction, computed from the pet tables."""
import calendar
import datetime
import math
from dataclasses import dataclass, field
from statistics import median

from django.db.models import Q, Sum
from django.utils import formats, timezone
from django.utils.translation import gettext as _, gettext_lazy, ngettext

from . import units
from .clock import show_time, uses_24h
from .models import Fill, Meal, PottyBreak, Sleep, Walk

WINDOW_DAYS = 14  # history behind the charts and predictions


def _prediction_settings():
    """All Next Walk settings from the Site Settings page in one query (reading them one by one costs a query each)."""
    from types import SimpleNamespace

    from dbsettings.models import Setting

    group = PottyBreak.prediction_settings
    stored = dict(Setting.objects.filter(module_name="pets.models", class_name="PottyBreak")
                  .values_list("attribute_name", "value"))
    values = {}
    for name, value in group._settings:
        try:
            values[name] = value.to_python(stored[name]) if stored.get(name) not in (None, "") else value.default
        except (TypeError, ValueError):
            values[name] = value.default
        if values[name] is None:
            values[name] = value.default
    return SimpleNamespace(**values)
DAY = 24 * 60  # minutes


@dataclass
class Rules:
    """The Next Walk prediction rules. Defaults here; Site Settings can change them (Rules.load()).
    See docs/housetrained/predictions.md."""

    max_gap: datetime.timedelta = datetime.timedelta(hours=12)  # longer gaps are missed entries, not holds
    meal_window: datetime.timedelta = datetime.timedelta(hours=3)  # a potty within this counts as after a meal
    stale_after: datetime.timedelta = datetime.timedelta(minutes=30)  # missed triggers give way after this
    min_night: datetime.timedelta = datetime.timedelta(hours=4)  # a day's longest potty-free stretch, to be a night
    default_bed: int = 23 * 60  # minutes of day, until enough nights are logged
    default_wake: int = 6 * 60 + 30
    bedtime_slack: int = 45  # minutes before the usual bedtime potty that already count as bedtime
    night_half_life_days: int = 3  # a night's weight in the bedtime/wake estimate halves this often
    min_history_days: int = 3  # below this, ignore learned gaps and use the age rule
    age_rule_until_days: int = 7  # below this, the age rule rather than the 3-day average is the fallback
    max_hold_hours: int = 8
    default_walk: datetime.timedelta = datetime.timedelta(minutes=15)
    merge_slack: datetime.timedelta = datetime.timedelta(minutes=30)  # needs this close to a walk go on it
    learn_from_accidents: bool = True  # move predictions earlier when accidents keep coming before them
    accident_max_shift: datetime.timedelta = datetime.timedelta(minutes=90)
    meal_delay: dict = field(default_factory=lambda: {
        "pee": datetime.timedelta(minutes=20), "poop": datetime.timedelta(minutes=30)})

    @classmethod
    def load(cls):
        s = _prediction_settings()
        minutes, hours = (lambda n: datetime.timedelta(minutes=n)), (lambda n: datetime.timedelta(hours=n))
        return cls(
            max_gap=hours(s.max_gap_hours), meal_window=hours(s.meal_window_hours),
            stale_after=minutes(s.stale_after), min_night=hours(s.min_night_hours),
            default_bed=s.default_bedtime.hour * 60 + s.default_bedtime.minute,
            default_wake=s.default_wake.hour * 60 + s.default_wake.minute,
            bedtime_slack=s.bedtime_slack, night_half_life_days=max(s.night_half_life_days, 1),
            min_history_days=s.min_history_days, age_rule_until_days=s.age_rule_until_days,
            max_hold_hours=s.max_hold_hours, default_walk=minutes(s.default_walk), merge_slack=minutes(s.merge_slack),
            meal_delay={"pee": minutes(s.meal_delay_pee), "poop": minutes(s.meal_delay_poop)},
            learn_from_accidents=bool(s.learn_from_accidents), accident_max_shift=minutes(s.accident_max_shift),
        )

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


# Prediction wording: (name, plural) for each kind, and the parts of the day.
KINDS = {"pee": (gettext_lazy("Pee"), gettext_lazy("pees")), "poop": (gettext_lazy("Poop"), gettext_lazy("poops"))}
BUCKETS = {"overnight": gettext_lazy("overnight"), "morning": gettext_lazy("morning"),
           "afternoon": gettext_lazy("afternoon"), "evening": gettext_lazy("evening")}


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


def food_fills(pet, since):
    return Fill.objects.filter(container__pets=pet, contents=Fill.FOOD, time__gte=since)


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
def walks_card(pet):
    # The card shows the past two weeks; the Walks log has the rest.
    walks = list(pet.walks.filter(start__gte=window_start()).select_related("walker")
                 .prefetch_related("potty_breaks").order_by("-start"))
    for walk in walks:
        breaks = [b for b in walk.potty_breaks.all() if b.pet_id == pet.pk]
        walk.pee = any(b.pee for b in breaks)
        walk.poop = any(b.poop for b in breaks)
        walk.length = fmt_duration(walk.end - walk.start) if walk.end else _("in progress")
        walk.walker_label = (walk.walker.first_name or walk.walker.username) if walk.walker else walk.walker_name
    return {"walks": walks}


def statistics_card(pet):
    now = timezone.now()
    today = window_start(1)
    walk_time = sum(
        ((w.end or now) - w.start for w in pet.walks.filter(start__gte=today)), datetime.timedelta()
    )
    recent, window = potty(pet, now - datetime.timedelta(days=3)), potty(pet, window_start())
    gap3, gap14 = avg_gap(recent), avg_gap(window)
    if window:
        days = (timezone.localdate() - timezone.localtime(window[0].time).date()).days + 1
        pees, poops = f"{sum(b.pee for b in window) / days:.1f}", f"{sum(b.poop for b in window) / days:.1f}"
    else:
        pees = poops = "–"
    food = food_fills(pet, today).aggregate(s=Sum("amount"))["s"]
    water = Fill.objects.filter(container__pets=pet, contents=Fill.WATER, time__gte=today).aggregate(s=Sum("amount"))["s"]
    meals = Meal.objects.filter(pet=pet, time__gte=today)
    eaten = meals.aggregate(s=Sum("amount"))["s"]
    drunk = meals.aggregate(s=Sum("water_amount"))["s"]
    stats = [
        (_("Age"), pet_age(pet.birth_date) or "–"),
        (_("Today's walk time"), fmt_duration(walk_time)),
        (_("Meals today"), meals.filter(food=True).count() or "–"),
        (_("Food eaten today"), units.show(eaten, "mass", pet.household) if eaten else "–"),
        (_("Food put out today"), units.show(food, "mass", pet.household) if food else "–"),
        (_("Water drunk today"), units.show(drunk, "volume", pet.household) if drunk else "–"),
        (_("Water served today"), units.show(water, "volume", pet.household) if water else "–"),
        (_("Potty break frequency (past 3 days)"), fmt_duration(gap3) if gap3 else "–"),
        (_("Potty break frequency (past 2 weeks)"), fmt_duration(gap14) if gap14 else "–"),
        (_("Average pees per day (past 2 weeks)"), pees),
        (_("Average poops per day (past 2 weeks)"), poops),
    ]
    return {"stats": stats}


def last_potty_card(pet):
    return {"last": pet.potty_breaks.order_by("-time").first()}


def last_fed_card(pet):
    """The last meal (what the pet ate), not the last bowl fill."""
    last = Meal.objects.filter(pet=pet, food=True).order_by("-time").first()
    amount = units.show(last.amount, "mass", pet.household) if last and last.amount else None
    return {"last": last, "amount": amount}


def sleep_card(pet):
    today = window_start(1)
    naps = pet.sleeps.filter(kind=Sleep.NAP, start__gte=today).exclude(end=None)
    nap_time = sum((s.end - s.start for s in naps), datetime.timedelta())
    return {
        "last": pet.sleeps.order_by("-start").first(),
        "naps_today": naps.count(),
        "nap_time": fmt_duration(nap_time) if naps else None,
    }


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
    from .progress import streak

    return {"days": days, "legend": legend, "empty": not breaks, "show_walks": bool(walks), "streak": streak(pet)}


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
            "hist_w": round(slot - 1.5, 2), "row_h": ROW_H, **accident_review(pet), "hide_empty": True,
            "empty": not dots and not PottyBreak.objects.filter(pet=pet, location=PottyBreak.INSIDE,
                                                                 time__gte=since).exists()}


# --- Next walk prediction -----------------------------------------------------------------
def _minute(dt):
    t = timezone.localtime(dt)
    return t.hour * 60 + t.minute


def _after(dt, ref):
    """Minutes from time-of-day `ref` forward to `dt`'s time of day (0..DAY)."""
    return (_minute(dt) - ref) % DAY


def _circular_median(values, weights):
    """Weighted median time of day that copes with wrapping past midnight (e.g. 23:50 and 00:10)."""
    angles = [v / DAY * 2 * math.pi for v in values]
    center = math.atan2(sum(w * math.sin(a) for a, w in zip(angles, weights)),
                        sum(w * math.cos(a) for a, w in zip(angles, weights))) / (2 * math.pi) * DAY
    offsets = sorted(zip((((v - center + DAY / 2) % DAY) - DAY / 2 for v in values), weights))
    half, total = sum(weights) / 2, 0
    for offset, weight in offsets:
        total += weight
        if total >= half:
            return (center + offset) % DAY


def _nights(times, rules):
    """Each day's longest potty-free stretch, as (last potty before, first potty after)."""
    longest = {}
    for a, b in zip(times, times[1:]):
        day = timezone.localdate(b)
        if b - a >= rules.min_night and (day not in longest or b - a > longest[day][1] - longest[day][0]):
            longest[day] = (a, b)
    return list(longest.values())


class Night:
    """The household's learned sleep window: usual bedtime potty and first potty after waking."""

    def __init__(self, times, now, rules=None):
        self.rules = rules = rules or Rules()
        self.nights = _nights(times, rules)
        if len(self.nights) >= 2:
            # Recent nights count more, so a drifting routine pulls the estimate along.
            # Relative to the latest night (not `now`), so the estimate doesn't wobble as the clock moves.
            latest = self.nights[-1][1]
            weights = [0.5 ** ((latest - b).total_seconds() / 86400 / rules.night_half_life_days) for _bed, b in self.nights]
            self.bed = _circular_median([_minute(a) for a, _ in self.nights], weights)
            self.wake = _circular_median([_minute(b) for _bed, b in self.nights], weights)
        else:
            self.bed, self.wake = rules.default_bed, rules.default_wake
        self.length = (self.wake - self.bed) % DAY

    def contains(self, dt, slack=0):
        return _after(dt, self.bed - slack) < self.length + slack

    def bucket(self, dt):
        if self.contains(dt):
            return "overnight"
        since_wake = _after(dt, self.wake)
        return "morning" if since_wake < 360 else "afternoon" if since_wake < 720 else "evening"

    @staticmethod
    def _next(dt, minute_of_day):
        local = timezone.localtime(dt)
        minute_of_day = round(minute_of_day) % DAY
        at = local.replace(hour=minute_of_day // 60, minute=minute_of_day % 60, second=0, microsecond=0)
        return at if at > local else at + datetime.timedelta(days=1)

    def next_wake(self, dt):
        return self._next(dt, self.wake)

    def next_bed(self, dt):
        return self._next(dt, self.bed)

    def first_after_wake(self, events):
        """Usual delay from waking to the first of these events."""
        delays = []
        for _bed, woke in self.nights:
            after = [e for e in events if woke <= e < woke + self.rules.max_gap]
            if after:
                delays.append(after[0] - woke)
        return median(delays) if len(delays) >= 2 else datetime.timedelta()


def _predict(kind, events, feedings, wakes, fallback, now, night, learned=True, rules=None):
    """Predict the next pee or poop. Returns (time, reason) or None."""
    rules = rules or Rules()
    if not events:
        return None
    last = events[-1]
    candidates = []

    gaps = [(b - a, night.bucket(a)) for a, b in zip(events, events[1:]) if b - a <= rules.max_gap]
    bucket = night.bucket(last)
    same = [g for g, name in gaps if name == bucket]
    if learned and len(same) >= 3:
        gap = median(same)
        candidates.append((last + gap, _("%(part)s %(kinds)s usually %(gap)s apart") % {"part": BUCKETS[bucket], "kinds": KINDS[kind][1], "gap": fmt_duration(gap)}, False))
    elif fallback:
        gap, reason = fallback
        candidates.append((last + gap, reason, False))

    meals_since = [f for f in feedings if f > last]
    if meals_since:
        delays = []
        for f in feedings:
            after = [e for e in events if f < e <= f + rules.meal_window]
            if after:
                delays.append(after[0] - f)
        delay = median(delays) if len(delays) >= 3 else rules.meal_delay[kind]
        # Every meal or drink since is a trigger; a later one never postpones an earlier one (min below).
        for meal in meals_since:
            candidates.append((meal + delay, _("meal at %(time)s, %(kinds)s ~%(delay)s after meals") % {"time": fmt_time(meal), "kinds": KINDS[kind][1], "delay": fmt_duration(delay)}, True))

    for woke in (s for s in wakes if last < s <= now):
        candidates.append((woke, _("woke up at %(time)s") % {"time": fmt_time(woke)}, True))

    # A trigger (meal, wake-up) that passed long ago without a potty break gives way to the next estimate.
    # The pet's usual gap never does: once that's passed, the pet stays overdue until a potty break is logged.
    fresh = [c for c in candidates if not c[2] or c[0] >= now - rules.stale_after] or candidates
    return min(fresh, key=lambda c: c[0])[:2] if fresh else None


def accident_shift(pet, now, rules):
    """How much earlier to predict, learned from recent accidents that came before the (unadjusted) prediction:
    their median lead, if at least two did, capped. Returns (shift, how many accidents)."""
    if not rules.learn_from_accidents:
        return datetime.timedelta(), 0
    accidents = PottyBreak.objects.filter(
        pet=pet, location=PottyBreak.INSIDE, found_later=False,
        time__gte=now - datetime.timedelta(days=WINDOW_DAYS), time__lt=now,
    ).exclude(expected_raw_at=None)
    leads = [a.expected_raw_at - a.time for a in accidents if a.expected_raw_at > a.time]
    if len(leads) < 2:
        return datetime.timedelta(), len(leads)
    return min(median(leads), rules.accident_max_shift), len(leads)


def next_walk_card(pet, now=None, rules=None, learn=True):
    """The Next Walk prediction as of `now` (default: this moment), from data logged before `now` only."""
    now = now or timezone.now()
    rules = rules or Rules.load()
    since = timezone.make_aware(datetime.datetime.combine(
        timezone.localdate(now) - datetime.timedelta(days=WINDOW_DAYS - 1), datetime.time()))
    # Accidents found later have no reliable time, so they don't count as potty events here.
    breaks = [b for b in potty(pet, since, until=now) if not b.found_later]
    # What goes in comes out: food drives poops, food and water drive pees.
    feedings = {"pee": feeding_times(pet, since, water=True, until=now), "poop": feeding_times(pet, since, until=now)}
    wakes = sorted(pet.sleeps.filter(end__gte=since, end__lt=now).values_list("end", flat=True))
    # With little history, lean on the age rule of thumb: a puppy can hold it ~(age in months + 1) hours.
    history = (now - breaks[0].time).days if breaks else 0
    months = (timezone.localdate(now) - pet.birth_date).days / 30.44 if pet.birth_date else 12
    age_gap = datetime.timedelta(hours=min(rules.max_hold_hours, months + 1))
    # Whole days, so the average doesn't drift (and move the estimate) as old breaks age out during the day.
    since3 = timezone.make_aware(datetime.datetime.combine(timezone.localdate(now) - datetime.timedelta(days=3),
                                                           datetime.time()))
    gap3 = avg_gap(potty(pet, since3, until=now))
    if history < rules.age_rule_until_days or not gap3:
        if months < 4:
            reason = _("a %(n).0f-week-old can usually hold it ~%(gap)s") % {"n": months * 30.44 / 7, "gap": fmt_duration(age_gap)}
        else:
            reason = _("a %(n).0f-month-old can usually hold it ~%(gap)s") % {"n": months, "gap": fmt_duration(age_gap)}
        fallback = (age_gap, reason)
    else:
        fallback = (gap3, _("average potty gap (3 days) is %(gap)s") % {"gap": fmt_duration(gap3)})

    # After the usual bedtime potty break, the next one comes after waking up.
    times = [b.time for b in breaks]
    night = Night([b.time for b in breaks if not is_accident(b)], now, rules)
    wake = None
    if times and night.contains(times[-1], slack=rules.bedtime_slack) and now < night.next_wake(times[-1]) + rules.max_gap:
        wake = night.next_wake(times[-1])

    predictions = []
    for kind in ("pee", "poop"):
        events = [b.time for b in breaks if getattr(b, kind)]
        if wake and events:
            when = wake + night.first_after_wake(events)
            result = (when, _("after bedtime potty at %(bed)s; usually up ~%(wake)s, first %(kind)s ~%(when)s") % {
                "bed": fmt_time(times[-1]), "wake": fmt_time(wake), "kind": str(KINDS[kind][0]).lower(),
                "when": fmt_time(when)})
        else:
            result = _predict(kind, events, feedings[kind], wakes, fallback, now, night, learned=history >= rules.min_history_days, rules=rules)
            # Needs that would fall during the night: pees go on the bedtime walk if it's still ahead,
            # everything else waits for the first walk after waking up.
            if result and night.contains(result[0]) and not night.contains(now):
                bedtime = night.next_bed(now)
                if kind == "pee" and bedtime <= result[0]:
                    result = (bedtime, _("bedtime walk, usually ~%(time)s") % {"time": fmt_time(bedtime)})
                else:
                    result = (night.next_wake(result[0]), result[1] + _(", so after waking up"))
        if result:
            when, reason = result
            predictions.append({"kind": str(KINDS[kind][0]), "key": kind, "time": when, "raw_time": when,
                                "reason": reason, "last": events[-1]})

    # Accidents that keep coming before the prediction pull it earlier (not into the night, not before the last
    # potty break).
    shift, _count = accident_shift(pet, now, rules) if learn else (datetime.timedelta(), 0)
    if shift >= datetime.timedelta(minutes=5):
        for p in predictions:
            moved = max(p["time"] - shift, p["last"] + datetime.timedelta(minutes=10))
            if moved < p["time"] and not night.contains(moved):
                p["time"] = moved
                p["reason"] += _("; ~%(shift)s earlier: recent accidents came before the prediction") % {
                    "shift": fmt_duration(p["raw_time"] - moved)}
    predictions.sort(key=lambda p: p["time"])

    # Pees and poops happen on walks: anything due before the next walk would end belongs to it.
    walks = list(Walk.objects.filter(pets=pet, start__gte=since, start__lt=now).exclude(end=None).order_by("start"))
    lengths = [w.end - w.start for w in walks if w.end > w.start]
    walk_len = median(lengths) if lengths else rules.default_walk
    gaps = [b.start - a.start for a, b in zip(walks, walks[1:])
            if not night.contains(a.start) and b.start - a.start <= rules.max_gap]
    walk_gap = median(gaps) if gaps else None

    due = expect = head_out = None
    to_outside = datetime.timedelta(minutes=pet.household.minutes_to_outside)
    if predictions:
        walk_at = predictions[0]["time"]
        for p in predictions:
            p["this_walk"] = p["time"] <= walk_at + walk_len + rules.merge_slack
        # The pet needs to go at walk_at; you need to be out the door before that.
        head_out = walk_at - to_outside
        expect = " + ".join(p["kind"].lower() for p in predictions if p["this_walk"])
        delta = head_out - now
        mins = int(delta.total_seconds() // 60)
        if abs(mins) < 5:
            due = _("now")
        elif mins > 0:
            due = _("in %(time)s") % {"time": fmt_duration(delta)}
        else:
            due = _("%(time)s overdue") % {"time": fmt_duration(-delta)}
    return {"predictions": predictions, "due": due, "expect": expect, "head_out": head_out,
            "to_outside": fmt_duration(to_outside) if to_outside else None,
            "walk_len": fmt_duration(walk_len), "walk_gap": fmt_duration(walk_gap) if walk_gap else None}


# --- Accidents vs the prediction -------------------------------------------------------------
def snapshot_expectation(accident, rules=None):
    """Store what Next Walk expected just before this accident: shown and unadjusted, for its kind (poop if any)."""
    if accident.location != PottyBreak.INSIDE:
        values = {"expected_at": None, "expected_raw_at": None, "expected_reason": ""}
    else:
        rules = rules or Rules.load()
        card = next_walk_card(accident.pet, now=accident.time, rules=rules)
        kind = "poop" if accident.poop else "pee"
        match = next((p for p in card["predictions"] if p["key"] == kind), None) or (
            card["predictions"][0] if card["predictions"] else None)
        values = {
            "expected_at": match["time"] if match else None,
            "expected_raw_at": match["raw_time"] if match else None,
            "expected_reason": (match["reason"] if match else "")[:255],
        }
    PottyBreak.objects.filter(pk=accident.pk).update(**values)
    for key, value in values.items():
        setattr(accident, key, value)
    return accident


def accident_review(pet):
    """Recent accidents next to what the app expected, and the typical lead. Fills in missing snapshots."""
    since = window_start()
    accidents = list(PottyBreak.objects.filter(pet=pet, location=PottyBreak.INSIDE, time__gte=since).order_by("-time"))
    rules = Rules.load()
    rows, leads = [], []
    for a in accidents:
        kind = _("Pee + poop") if a.pee and a.poop else (_("Poop") if a.poop else _("Pee"))
        if a.found_later:
            rows.append({"time": a.time, "kind": kind, "relation": _("found later, time unknown"), "early": False,
                         "expected": None, "pk": a.pk})
            continue
        if a.expected_at is None and not a.expected_reason:
            snapshot_expectation(a, rules)
        if a.expected_at is None:
            continue
        lead = a.expected_at - a.time
        if lead > datetime.timedelta():
            leads.append(lead)
            relation = _("%(time)s earlier than expected") % {"time": fmt_duration(lead)}
        else:
            relation = _("%(time)s later than expected") % {"time": fmt_duration(-lead)}
        rows.append({"time": a.time, "kind": kind, "relation": relation, "early": lead > datetime.timedelta(),
                     "expected": a.expected_at, "pk": a.pk})
    summary = None
    if leads:
        summary = ngettext(
            "%(n)d of %(total)d accidents came earlier than expected, typically by %(lead)s.",
            "%(n)d of %(total)d accidents came earlier than expected, typically by %(lead)s.", len(rows),
        ) % {"n": len(leads), "total": len([r for r in rows if r["expected"]]), "lead": fmt_duration(median(leads))}
    shift, _count = accident_shift(pet, timezone.now(), rules)
    adjusted = fmt_duration(shift) if shift >= datetime.timedelta(minutes=5) else None
    return {"accident_rows": rows[:6], "accident_summary": summary, "accident_adjusted": adjusted}


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
def now_card(pet, now=None):
    """Next walk plus the latest of everything, in one card: head-out time, last pee/poop/meal, sleep."""
    now = now or timezone.now()
    card = next_walk_card(pet, now=now)
    last_pee = pet.potty_breaks.filter(pee=True, time__lte=now).order_by("-time").first()
    last_poop = pet.potty_breaks.filter(poop=True, time__lte=now).order_by("-time").first()
    last_meal = Meal.objects.filter(pet=pet, food=True, time__lte=now).order_by("-time").first()
    asleep = pet.sleeps.filter(end=None).order_by("-start").first()
    latest = []
    if last_pee:
        latest.append((_("pee"), last_pee.time, f"/log/potty/{last_pee.pk}/"))
    if last_poop:
        latest.append((_("poop"), last_poop.time, f"/log/potty/{last_poop.pk}/"))
    if last_meal:
        latest.append((_("ate"), last_meal.time, f"/log/meal/{last_meal.pk}/"))
    return {**card, "latest": latest, "asleep": asleep}


def today_card(pet, now=None):
    """Five numbers for today: walks, pees, poops, accidents, meals (or food eaten)."""
    now = now or timezone.now()
    today = window_start(1)
    walks = list(pet.walks.filter(start__gte=today, start__lte=now))
    walk_time = sum(((w.end or now) - w.start for w in walks), datetime.timedelta())
    breaks = potty(pet, today, until=now)
    meals = Meal.objects.filter(pet=pet, food=True, time__gte=today, time__lte=now)
    eaten = meals.aggregate(s=Sum("amount"))["s"]
    return {"tiles": [
        (_("Walks"), str(len(walks)), fmt_duration(walk_time) if walks else ""),
        (_("Pees"), str(sum(b.pee for b in breaks)), ""),
        (_("Poops"), str(sum(b.poop for b in breaks)), ""),
        (_("Accidents"), str(sum(1 for b in breaks if is_accident(b))), ""),
        (_("Eaten"), units.show(eaten, "mass", pet.household), "") if eaten else (_("Meals"), str(meals.count()), ""),
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
    card = next_walk_card(pet, now=now)
    rows.append((_("Usual walk"), card["walk_len"] + (", " + _("every %(gap)s") % {"gap": card["walk_gap"]}
                                                        if card["walk_gap"] else "")))
    return rows
