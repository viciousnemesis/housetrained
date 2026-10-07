import calendar
import datetime
import math
from statistics import median

from django import template
from django.utils import timezone

from core import models

register = template.Library()

WINDOW_DAYS = 14
MAX_GAP = datetime.timedelta(hours=12)
MEAL_WINDOW = datetime.timedelta(hours=3)
STALE_AFTER = datetime.timedelta(minutes=30)
DAY = 24 * 60  # minutes
MIN_NIGHT = datetime.timedelta(hours=4)  # a day's longest potty-free stretch must be this long to count as night
DEFAULT_NIGHT = (23 * 60, 6 * 60 + 30)  # bedtime, wake-up (minutes of day) until enough nights are logged
BEDTIME_SLACK = 45  # minutes before the usual bedtime potty that already count as bedtime
NIGHT_HALF_LIFE_DAYS = 3  # a night's weight in the bedtime/wake estimate halves every 3 days
MIN_HISTORY_DAYS = 3  # below this, ignore learned gaps and use the age rule
AGE_RULE_UNTIL_DAYS = 7  # below this, fall back to the age rule rather than the 3-day average
MAX_HOLD_HOURS = 8
DEFAULT_WALK = datetime.timedelta(minutes=15)
MERGE_SLACK = datetime.timedelta(minutes=30)  # a need due this soon after a walk ends is handled on that walk
DEFAULT_MEAL_DELAY = {"pee": datetime.timedelta(minutes=20), "poop": datetime.timedelta(minutes=30)}

# Day x hour chart geometry (SVG units).
LABEL_W, PLOT_W, ROW_H, AXIS_H = 46, 254, 14, 16


def fmt_time(dt):
    return timezone.localtime(dt).strftime("%I:%M %p").lstrip("0")


def fmt_duration(td):
    minutes = int(td.total_seconds() // 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m" if hours else f"{minutes}m"


def _window_start(days=WINDOW_DAYS):
    today = timezone.localdate()
    start = datetime.datetime.combine(today - datetime.timedelta(days=days - 1), datetime.time())
    return timezone.make_aware(start)


def _potty(child, since):
    return list(models.DiaperChange.objects.filter(child=child, time__gte=since).order_by("time"))


def _avg_gap(changes):
    gaps = [b.time - a.time for a, b in zip(changes, changes[1:])]
    return sum(gaps, datetime.timedelta()) / len(gaps) if gaps else None


def _card(**kwargs):
    return {"empty": False, "hide_empty": False, **kwargs}


# --- Age ---------------------------------------------------------------------------
def _plural(n, unit):
    return f"{n} {unit}{'' if n == 1 else 's'}"


@register.filter
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
        return "1 year" + (f", {_plural(rest, 'month')}" if rest else "")
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


# --- Walk/sleep timers ----------------------------------------------------------
@register.simple_tag
def active_timer(name, child=None):
    """A running walk/sleep timer, for one pet or for any pet."""
    timers = models.Timer.objects.filter(name=name)
    return (timers.filter(child=child) if child else timers).first()


@register.simple_tag
def activity_groups():
    """Running walks/sleeps; timers started together for several pets show as one."""
    groups = {}
    for t in models.Timer.objects.filter(name__in=("Walk", "Sleep")).select_related("child").order_by(
            "start", "child__first_name"):
        groups.setdefault((t.name, t.start), []).append(t)
    return [{"name": name, "start": start, "first": timers[0],
             "names": ", ".join(t.child.first_name for t in timers if t.child)}
            for (name, start), timers in groups.items()]


# --- Walks --------------------------------------------------------------------
@register.inclusion_tag("cards/pet_walks.html")
def card_pet_walks(child):
    walks = list(models.TummyTime.objects.filter(child=child).order_by("-start"))
    outside = list(models.DiaperChange.objects.filter(child=child, color="outside"))
    for walk in walks:
        during = [c for c in outside if walk.start <= c.time <= walk.end]
        walk.pee = any(c.wet for c in during)
        walk.poop = any(c.solid for c in during)
        walk.length = fmt_duration(walk.end - walk.start)
    return _card(type="tummytime", walks=walks)


# --- Statistics ---------------------------------------------------------------
@register.inclusion_tag("cards/pet_statistics.html")
def card_pet_statistics(child):
    now = timezone.now()
    today = _window_start(1)
    walk_time = sum(
        (w.end - w.start for w in models.TummyTime.objects.filter(child=child, start__gte=today)),
        datetime.timedelta(),
    )
    recent = _potty(child, now - datetime.timedelta(days=3))
    window = _potty(child, _window_start())
    gap3, gap14 = _avg_gap(recent), _avg_gap(window)
    if window:
        days = (timezone.localdate() - timezone.localtime(window[0].time).date()).days + 1
        pees = f"{sum(c.wet for c in window) / days:.1f}"
        poops = f"{sum(c.solid for c in window) / days:.1f}"
    else:
        pees = poops = "–"
    stats = [
        ("Age", pet_age(child.birth_date)),
        ("Today's walk time", fmt_duration(walk_time)),
        ("Potty break frequency (past 3 days)", fmt_duration(gap3) if gap3 else "–"),
        ("Potty break frequency (past 2 weeks)", fmt_duration(gap14) if gap14 else "–"),
        ("Average pees per day (past 2 weeks)", pees),
        ("Average poops per day (past 2 weeks)", poops),
    ]
    return _card(type="statistics", stats=stats)


# --- Day x hour charts --------------------------------------------------------
def _x(dt):
    local = timezone.localtime(dt)
    return LABEL_W + (local.hour + local.minute / 60) / 24 * PLOT_W


def _chart(days=WINDOW_DAYS):
    today = timezone.localdate()
    rows = []
    for i in range(days):
        date = today - datetime.timedelta(days=i)
        rows.append({"date": date, "y": i * ROW_H, "mid": i * ROW_H + ROW_H / 2,
                     "label": f"{date:%a} {date.day}"})
    by_date = {r["date"]: r for r in rows}
    height = days * ROW_H
    ticks = [{"x": LABEL_W + h / 24 * PLOT_W, "label": ("12a", "6a", "12p", "6p", "12a")[h // 6]} for h in range(0, 25, 6)]
    return {"rows": rows, "by_date": by_date, "height": height, "total_h": height + AXIS_H,
            "width": LABEL_W + PLOT_W, "plot_x": LABEL_W, "label_x": LABEL_W - 6, "plot_w": PLOT_W, "ticks": ticks}


def _place(chart, dt):
    row = chart["by_date"].get(timezone.localdate(dt))
    return (round(_x(dt), 1), row["mid"]) if row else None


@register.inclusion_tag("cards/pet_feedings.html")
def card_pet_feedings(child):
    chart = _chart()
    dots = []
    for f in models.Feeding.objects.filter(child=child, start__gte=_window_start()):
        pos = _place(chart, f.start)
        if pos:
            r = 2.5 + min((f.amount or 0) / 30, 2.5)
            label = fmt_time(f.start) + (f" · {f.amount:g}g" if f.amount else "")
            dots.append({"x": pos[0], "y": pos[1], "r": r, "label": label})
    return _card(type="feeding", chart=chart, dots=dots)


@register.inclusion_tag("cards/pet_accidents.html")
def card_pet_accidents(child):
    chart = _chart()
    since = _window_start()
    dots, meals, sleeps = [], [], []
    hours = [0] * 24
    for c in models.DiaperChange.objects.filter(child=child, color="inside", time__gte=since):
        pos = _place(chart, c.time)
        if not pos:
            continue
        hours[timezone.localtime(c.time).hour] += 1
        kind = "Pee + poop" if c.wet and c.solid else ("Poop" if c.solid else "Pee")
        color = "#a72431" if c.solid else "#f5d33f"
        dots.append({"x": pos[0], "y": pos[1], "color": color,
                     "label": f"{kind} inside {fmt_time(c.time)}"})
    for f in models.Feeding.objects.filter(child=child, start__gte=since):
        pos = _place(chart, f.start)
        if pos:
            meals.append({"x": pos[0] - 3, "y": pos[1] - 3})
    for s in models.Sleep.objects.filter(child=child, end__gte=since):
        start, end = timezone.localtime(s.start), timezone.localtime(s.end)
        day = start.date()
        while day <= end.date():
            row = chart["by_date"].get(day)
            if row:
                a = max(start, timezone.make_aware(datetime.datetime.combine(day, datetime.time())))
                b = min(end, timezone.make_aware(datetime.datetime.combine(day, datetime.time.max)))
                sleeps.append({"x": round(_x(a), 1), "y": row["y"], "w": round(_x(b) - _x(a), 1)})
            day += datetime.timedelta(days=1)
    peak = max(hours) or 1
    # One column per hour, each exactly as wide as its two-digit label.
    slot = PLOT_W / 24
    col_w = round(slot - 1.5, 2)
    hist = [{"x": round(LABEL_W + h * slot + 0.75, 2), "y": 26 - n / peak * 26, "h": n / peak * 26,
             "n": n, "label": f"{h:02d}", "even": h % 2 == 0}
            for h, n in enumerate(hours)]
    return _card(type="diaperchange", chart=chart, dots=dots, meals=meals, sleeps=sleeps,
                 hist=hist, hist_w=col_w, row_h=ROW_H)


# --- Next potty ----------------------------------------------------------------
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


def _nights(times):
    """Each day's longest potty-free stretch, as (last potty before, first potty after)."""
    longest = {}
    for a, b in zip(times, times[1:]):
        day = timezone.localdate(b)
        if b - a >= MIN_NIGHT and (day not in longest or b - a > longest[day][1] - longest[day][0]):
            longest[day] = (a, b)
    return list(longest.values())


class Night:
    """The household's learned sleep window: usual bedtime potty and first potty after waking."""

    def __init__(self, times, now):
        self.nights = _nights(times)
        if len(self.nights) >= 2:
            # Recent nights count more, so a drifting routine pulls the estimate along.
            weights = [0.5 ** ((now - b).total_seconds() / 86400 / NIGHT_HALF_LIFE_DAYS) for _, b in self.nights]
            self.bed = _circular_median([_minute(a) for a, _ in self.nights], weights)
            self.wake = _circular_median([_minute(b) for _, b in self.nights], weights)
        else:
            self.bed, self.wake = DEFAULT_NIGHT
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
        at = local.replace(hour=int(minute_of_day) // 60, minute=int(minute_of_day) % 60, second=0, microsecond=0)
        return at if at > local else at + datetime.timedelta(days=1)

    def next_wake(self, dt):
        return self._next(dt, self.wake)

    def next_bed(self, dt):
        return self._next(dt, self.bed)

    def first_after_wake(self, events):
        """Usual delay from waking to the first of these events."""
        delays = []
        for _, woke in self.nights:
            after = [e for e in events if woke <= e < woke + MAX_GAP]
            if after:
                delays.append(after[0] - woke)
        return median(delays) if len(delays) >= 2 else datetime.timedelta()


def _predict(kind, events, feedings, sleeps, fallback, now, night, learned=True):
    """Predict the next pee or poop. Returns (time, reason) or None."""
    if not events:
        return None
    last = events[-1]
    candidates = []

    gaps = [(b - a, night.bucket(a)) for a, b in zip(events, events[1:]) if b - a <= MAX_GAP]
    bucket = night.bucket(last)
    same = [g for g, name in gaps if name == bucket]
    if learned and len(same) >= 3:
        gap = median(same)
        candidates.append((last + gap, f"{bucket} {kind}s usually {fmt_duration(gap)} apart"))
    elif fallback:
        gap, reason = fallback
        candidates.append((last + gap, reason))

    meals_since = [f for f in feedings if f > last]
    if meals_since:
        delays = []
        for f in feedings:
            after = [e for e in events if f < e <= f + MEAL_WINDOW]
            if after:
                delays.append(after[0] - f)
        delay = median(delays) if len(delays) >= 3 else DEFAULT_MEAL_DELAY[kind]
        meal = meals_since[-1]
        candidates.append((meal + delay, f"ate at {fmt_time(meal)}, "
                                         f"{kind}s ~{fmt_duration(delay)} after meals"))

    woke = [s for s in sleeps if last < s <= now]
    if woke:
        candidates.append((woke[-1], f"woke up at {fmt_time(woke[-1])}"))

    # A trigger that passed long ago without a potty break gives way to the next estimate.
    fresh = [c for c in candidates if c[0] >= now - STALE_AFTER] or candidates
    return min(fresh, key=lambda c: c[0]) if fresh else None


@register.inclusion_tag("cards/pet_next_potty.html")
def card_pet_next_potty(child):
    now = timezone.now()
    since = _window_start()
    changes = _potty(child, since)
    feedings = [f.start for f in models.Feeding.objects.filter(child=child, start__gte=since).order_by("start")]
    sleeps = [s.end for s in models.Sleep.objects.filter(child=child, end__gte=since).order_by("end")]
    # With little history, lean on the age rule of thumb: a puppy can hold it ~(age in months + 1) hours.
    history = (now - changes[0].time).days if changes else 0
    months = (timezone.localdate() - child.birth_date).days / 30.44
    age_gap = datetime.timedelta(hours=min(MAX_HOLD_HOURS, months + 1))
    avg_gap = _avg_gap(_potty(child, now - datetime.timedelta(days=3)))
    if history < AGE_RULE_UNTIL_DAYS or not avg_gap:
        age = f"{months * 30.44 / 7:.0f}-week-old" if months < 4 else f"{months:.0f}-month-old"
        fallback = (age_gap, f"a {age} can usually hold it ~{fmt_duration(age_gap)}")
    else:
        fallback = (avg_gap, f"average potty gap (3 days) is {fmt_duration(avg_gap)}")

    # After the usual bedtime potty break, the next one comes after waking up.
    times = [c.time for c in changes]
    night = Night([c.time for c in changes if c.color == "outside"], now)
    wake = None
    if times and night.contains(times[-1], slack=BEDTIME_SLACK) and now < night.next_wake(times[-1]) + MAX_GAP:
        wake = night.next_wake(times[-1])

    predictions = []
    for kind, attr in (("pee", "wet"), ("poop", "solid")):
        events = [c.time for c in changes if getattr(c, attr)]
        if wake and events:
            when = wake + night.first_after_wake(events)
            result = (when, f"after bedtime potty at {fmt_time(times[-1])}; usually up ~{fmt_time(wake)}, "
                            f"first {kind} ~{fmt_time(when)}")
        else:
            result = _predict(kind, events, feedings, sleeps, fallback, now, night,
                              learned=history >= MIN_HISTORY_DAYS)
            # Needs that would fall during the night: pees go on the bedtime walk if it's still ahead,
            # everything else waits for the first walk after waking up.
            if result and night.contains(result[0]) and not night.contains(now):
                bedtime = night.next_bed(now)
                if kind == "pee" and bedtime <= result[0]:
                    result = (bedtime, f"bedtime walk, usually ~{fmt_time(bedtime)}")
                else:
                    result = (night.next_wake(result[0]), result[1] + ", so after waking up")
        if result:
            when, reason = result
            predictions.append({"kind": kind.capitalize(), "time": when, "reason": reason,
                                "last": events[-1]})
    predictions.sort(key=lambda p: p["time"])

    # Pees and poops happen on walks: anything due before the next walk would end belongs to it.
    walks = list(models.TummyTime.objects.filter(child=child, start__gte=since).order_by("start"))
    lengths = [w.end - w.start for w in walks if w.end > w.start]
    walk_len = median(lengths) if lengths else DEFAULT_WALK
    gaps = [b.start - a.start for a, b in zip(walks, walks[1:])
            if not night.contains(a.start) and b.start - a.start <= MAX_GAP]
    walk_gap = median(gaps) if gaps else None

    due = expect = None
    if predictions:
        walk_at = predictions[0]["time"]
        for p in predictions:
            p["this_walk"] = p["time"] <= walk_at + walk_len + MERGE_SLACK
        expect = " + ".join(p["kind"].lower() for p in predictions if p["this_walk"])
        delta = walk_at - now
        mins = int(delta.total_seconds() // 60)
        due = "now" if abs(mins) < 5 else (f"in {fmt_duration(delta)}" if mins > 0 else f"{fmt_duration(-delta)} overdue")
    return _card(type="diaperchange", predictions=predictions, due=due, expect=expect,
                 walk_len=fmt_duration(walk_len), walk_gap=fmt_duration(walk_gap) if walk_gap else None)
