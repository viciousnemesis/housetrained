"""Next walk: a simple schedule you set and can see, plus a coach that suggests changes (PUP-93).

Each pet has three numbers: pee every (e.g. 2h 30m), poop every (e.g. 5h) and walk after a meal (e.g. 15 min).
The household has quiet hours (bedtime to wake-up) and its time to get outside. From those and what's logged:

- Next pee: last pee + pee every. Sooner after eating or drinking (+ walk after a meal) or waking from a nap.
- Next poop: last poop + poop every. Sooner after food, unless she pooped in the hour before eating.
- Back from being out with nothing: try again 15 minutes later.
- Quiet hours: after the bedtime pee, the next one is at wake-up; anything due overnight goes on the bedtime walk.
- Head out = the earliest of those minus the time to get outside. Needs within 30 minutes go on the same walk.

The coach looks at accidents: two in a week mean the interval is too long (it suggests a shorter one, from how
soon after the last one they came); a week without one means it's time to try 15 minutes longer. Suggestions only:
nothing changes until someone taps Apply. See docs/housetrained/schedule.md.
"""
import datetime
from statistics import median

from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import Meal, PottyBreak, Walk

MINUTE = datetime.timedelta(minutes=1)
RETRY = datetime.timedelta(minutes=15)  # out and nothing happened: try again this long after getting back
BEDTIME_WINDOW = datetime.timedelta(hours=1)  # a pee this close before bedtime is the bedtime one
TOGETHER = datetime.timedelta(minutes=30)  # needs this close together are expected on the same walk
POOP_THEN_FOOD = datetime.timedelta(hours=1)  # pooped this soon before eating: that meal brings no poop
COACH_WEEK = datetime.timedelta(days=7)
COACH_SNOOZE = datetime.timedelta(days=2)
LONGEST = {"pee": 8 * 60, "poop": 12 * 60}  # minutes
STEP = {"pee": 15, "poop": 30}  # the coach lengthens by this much


def fmt_time(dt):
    from .clock import show_time

    return show_time(dt)


def fmt_minutes(minutes):
    hours, mins = divmod(int(minutes), 60)
    return f"{hours}h {mins:02d}m" if hours and mins else f"{hours}h" if hours else f"{mins}m"


def fmt_span(td):
    return fmt_minutes(round(td.total_seconds() / 60))


def age_rule(birth_date, today=None):
    """Minutes a puppy can usually hold it while awake: about its age in months plus one hour, in 15-minute steps,
    between 1 and 8 hours. Without a birth date: 4 hours."""
    if not birth_date:
        return 4 * 60
    months = ((today or timezone.localdate()) - birth_date).days / 30.44
    return int(min(max((months + 1) * 60, 60), LONGEST["pee"]) // 15 * 15)


class QuietHours:
    """The household's night: bedtime to wake-up, as set on the Household settings page."""

    def __init__(self, household):
        self.bed, self.wake = household.bedtime, household.wake_time

    def contains(self, dt):
        t = timezone.localtime(dt).time()
        return (t >= self.bed or t < self.wake) if self.bed > self.wake else self.bed <= t < self.wake

    def _next(self, dt, at):
        local = timezone.localtime(dt)
        when = timezone.make_aware(datetime.datetime.combine(local.date(), at))
        return when if when > local else timezone.make_aware(
            datetime.datetime.combine(local.date() + datetime.timedelta(days=1), at))

    def next_wake(self, dt):
        return self._next(dt, self.wake)

    def next_bed(self, dt):
        return self._next(dt, self.bed)

    def is_bedtime(self, dt):
        """The last pee of the day: in the quiet hours or in the hour before them."""
        return self.contains(dt) or self.next_bed(dt) - dt <= BEDTIME_WINDOW


def next_walk(pet, now=None):
    """When to head out next and what to expect, as of `now` (default: this moment), from what was logged before."""
    now = now or timezone.now()
    household = pet.household
    quiet = QuietHours(household)
    to_outside = datetime.timedelta(minutes=household.minutes_to_outside)
    after_meal = datetime.timedelta(minutes=pet.after_meal)
    cleared = pet.triggers_cleared_at if pet.triggers_cleared_at and pet.triggers_cleared_at <= now else None
    since = now - datetime.timedelta(days=3)
    # Accidents found later have no reliable time, so they don't count.
    breaks = list(PottyBreak.objects.filter(pet=pet, time__gte=since, time__lt=now, found_later=False).order_by("time"))
    meals = list(Meal.objects.filter(pet=pet, time__gte=since, time__lt=now).filter(Q(food=True) | Q(water=True))
                 .order_by("time").values_list("time", "food", "created"))
    naps = list(pet.sleeps.filter(end__gte=since, end__lt=now).order_by("end").values_list("end", "created"))
    nothing = list(PottyBreak.everything.filter(pet=pet, pee=False, poop=False, time__gte=since, time__lt=now)
                   .values_list("time", flat=True))
    outings = list(Walk.objects.filter(pets=pet, start__gte=since, end__lt=now).values_list("start", "end"))

    def ready(cause, logged, wake=False):
        """The earliest a need from a meal or nap can be: you head out no sooner than it was logged (one logged
        afterwards is due from then), and not while she's still asleep."""
        at = logged if logged and logged <= now else cause
        return max(at, cause) + to_outside

    def out_since(t, kind):
        """When she was last out since `t` without doing `kind`, if she was: a walk, Nothing on the Potty panel, or
        an outdoor potty break that was the other kind."""
        tries = [n for n in nothing if n > t] + [end for start, end in outings if start > t]
        tries += [b.time for b in breaks if b.time > t and b.location != PottyBreak.INSIDE and not getattr(b, kind)]
        return max(tries) if tries else None

    def overnight(due, last, reason):
        """After the bedtime one, the next is at wake-up; anything else due in the quiet hours goes on the bedtime
        walk if that's still ahead."""
        if quiet.is_bedtime(last):
            wake = quiet.next_wake(last)
            return wake, _("bedtime at %(time)s; next when up, %(wake)s") % {
                "time": fmt_time(last), "wake": fmt_time(wake)}
        if quiet.contains(due) and not quiet.contains(now):
            bedtime = quiet.next_bed(last)
            if bedtime <= due:
                return bedtime, _("the bedtime walk, %(time)s") % {"time": fmt_time(bedtime)}
        return due, reason

    needs = []
    pees = [b.time for b in breaks if b.pee]
    if pees:
        last = pees[-1]
        every = datetime.timedelta(minutes=pet.pee_minutes)
        due, reason = overnight(last + every, last, _("last pee %(time)s + every %(every)s") % {
            "time": fmt_time(last), "every": fmt_span(every)})
        source = None
        start = max(last, cleared) if cleared else last
        for t, _food, logged in meals:
            if t > start and max(t + after_meal, ready(t, logged)) < due:
                due, source = max(t + after_meal, ready(t, logged)), "meal"
                reason = _("ate or drank at %(time)s; out %(after)s later") % {
                    "time": fmt_time(t), "after": fmt_span(after_meal)}
        for t, logged in naps:
            if t > start and ready(t, logged, wake=True) < due:
                due, source = ready(t, logged, wake=True), "wake"
                reason = _("woke up at %(time)s") % {"time": fmt_time(t)}
        back = out_since(last, "pee")
        if back and back + RETRY + to_outside > due:
            due, source = back + RETRY + to_outside, None
            reason = _("nothing when out at %(time)s; try again %(again)s") % {
                "time": fmt_time(back), "again": fmt_time(back + RETRY)}
        needs.append({"key": "pee", "kind": _("Pee"), "time": due, "reason": reason, "source": source, "last": last})

    poops = [b.time for b in breaks if b.poop]
    if poops:
        last = poops[-1]
        every = datetime.timedelta(minutes=pet.poop_minutes)
        due, reason = overnight(last + every, last, _("last poop %(time)s + every %(every)s") % {
            "time": fmt_time(last), "every": fmt_span(every)})
        source = None
        start = max(last + POOP_THEN_FOOD, cleared) if cleared else last + POOP_THEN_FOOD
        fed = [(t, logged) for t, food, logged in meals if food and t > start]
        if fed and max(fed[0][0] + after_meal, ready(*fed[0])) < due:
            due, source = max(fed[0][0] + after_meal, ready(*fed[0])), "meal"
            reason = _("ate at %(time)s; out %(after)s later") % {"time": fmt_time(fed[0][0]), "after": fmt_span(after_meal)}
        # Out since it was due and no poop: it doesn't keep its own walk, it's expected on the next one.
        back = out_since(due - TOGETHER, "poop")
        if back and back > due - TOGETHER:
            pee = needs[0]["time"] if needs else back + RETRY + to_outside
            due, source = max(pee, due), None
            reason = _("none when out at %(time)s; expected on the next walk") % {"time": fmt_time(back)}
        needs.append({"key": "poop", "kind": _("Poop"), "time": due, "reason": reason, "source": source, "last": last})

    needs.sort(key=lambda n: (n["time"], n["key"] != "pee"))
    head_out = due = expect = None
    if needs:
        walk_at = needs[0]["time"]
        for n in needs:
            n["this_walk"] = n["time"] <= walk_at + TOGETHER
        head_out = walk_at - to_outside  # she needs to go at walk_at; be out the door before that
        expect = " + ".join(str(n["kind"]).lower() for n in sorted(needs, key=lambda n: n["key"] != "pee")
                            if n["this_walk"])
        mins = int((head_out - now).total_seconds() // 60)
        due = _("now") if abs(mins) < 5 else (
            _("in %(time)s") % {"time": fmt_minutes(mins)} if mins > 0 else
            _("%(time)s overdue") % {"time": fmt_minutes(-mins)})
    return {"needs": needs, "head_out": head_out, "due": due, "expect": expect,
            "source": needs[0]["source"] if needs else None,
            "to_outside": fmt_span(to_outside) if to_outside else None}


def coach(pet, now=None):
    """One suggestion at most: shorter after accidents, longer after a clean week. Returns None or
    {kind, current, suggested, text}."""
    now = now or timezone.now()
    if pet.coach_snoozed_until and pet.coach_snoozed_until > now:
        return None
    week = now - COACH_WEEK
    changed = pet.schedule_changed_at
    since = max(week, changed) if changed else week
    breaks = list(PottyBreak.objects.filter(pet=pet, time__gte=week - datetime.timedelta(days=1), time__lt=now)
                  .order_by("time"))
    current = {"pee": pet.pee_minutes, "poop": pet.poop_minutes}
    labels = {"pee": _("pee"), "poop": _("poop")}

    for kind in ("pee", "poop"):
        of_kind = [b for b in breaks if getattr(b, kind)]
        soon = [b.time - a.time for a, b in zip(of_kind, of_kind[1:])
                if b.location == PottyBreak.INSIDE and not b.found_later and b.time >= since]
        if len(soon) >= 2:
            typical = median(soon)
            suggested = int((typical.total_seconds() / 60 - 15) // 15 * 15)
            suggested = max(min(suggested, current[kind] - 15), 30)
            if suggested < current[kind]:
                return {"kind": kind, "current": current[kind], "suggested": suggested, "text": _(
                    "%(n)d %(kind)s accidents this week came ~%(soon)s after the last %(kind)s. "
                    "Try every %(every)s?") % {"n": len(soon), "kind": labels[kind], "soon": fmt_span(typical),
                                                "every": fmt_minutes(suggested)}}

    first = breaks[0].time if breaks else None
    settled = (changed or first) and (changed or first) <= week
    for kind in ("pee", "poop"):
        recent = [b for b in breaks if getattr(b, kind) and b.time >= week]
        accidents = [b for b in recent if b.location == PottyBreak.INSIDE]
        if settled and not accidents and len(recent) >= 7 and current[kind] < LONGEST[kind]:
            suggested = min(current[kind] + STEP[kind], LONGEST[kind])
            return {"kind": kind, "current": current[kind], "suggested": suggested, "text": _(
                "A week without a %(kind)s accident at every %(every)s. Try %(longer)s?") % {
                "kind": labels[kind], "every": fmt_minutes(current[kind]), "longer": fmt_minutes(suggested)}}
    return None
