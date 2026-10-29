"""One-line summaries of entries, shared by the recent-activity list, and the per-type lists."""
import datetime

from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from . import units
from .models import Fill, Meal, Measurement, Note, PottyBreak, Puke, Sleep, TrainingLog, Walk


def _when(value):
    if isinstance(value, datetime.datetime):
        return value
    return timezone.make_aware(datetime.datetime.combine(value, datetime.time()))  # measurements have a date only


def _potty(b):
    what = " + ".join(w for w, flag in ((_("Pee"), b.pee), (_("Poop"), b.poop)) if flag) or _("Nothing")
    text = f"{what}, {b.get_location_display().lower()}"
    if b.spot_id:
        text += f" ({b.spot.name})"
    flags = [b.get_pee_amount_display(), b.get_pee_colour_display(), b.get_poop_amount_display(),
             b.get_poop_consistency_display(), b.get_poop_colour_display()]
    unusual = [f.lower() for f, raw in zip(flags, (b.pee_amount, b.pee_colour, b.poop_amount, b.poop_consistency,
                                                    b.poop_colour)) if raw]
    return text + (" · " + ", ".join(unusual) if unusual else "") + _vs_schedule(b)


def _vs_schedule(b):
    """For an accident: how it compared with when the schedule said to go out ("25m before due")."""
    if not b.accident or not b.due_at:
        return ""
    from .schedule import fmt_minutes

    mins = round((b.due_at - b.time).total_seconds() / 60)
    if abs(mins) < 5:
        return " · " + _("as due")
    return " · " + (_("%(time)s before due") if mins > 0 else _("%(time)s overdue")) % {"time": fmt_minutes(abs(mins))}


def _walk(w):
    length = f"{int((w.end - w.start).total_seconds() // 60)} min" if w.end else _("in progress")
    walker = (w.walker.first_name or w.walker.username) if w.walker else w.walker_name
    distance = f", {units.show(w.distance, 'distance', w.household, digits=2)}" if w.distance else ""
    return _("Walk") + f", {length}{distance}" + (f", {walker}" if walker else "")


def _sleep(s):
    length = f", {int((s.end - s.start).total_seconds() // 60)} min" if s.end else f", {_('in progress')}"
    return s.get_kind_display() + length


def _fill(f):
    if f.amount is not None:
        amount = units.show(f.amount, "volume" if f.contents == Fill.WATER else "mass", f.container.household)
    elif f.bowls is not None:
        amount = _("%(n)s bowl(s), amount unknown") % {"n": f"{f.bowls:g}"}
    else:
        amount = _("amount unknown")
    return f"{f.get_contents_display()} into {f.container.name}, {amount}"


def _meal(m):
    parts = []
    portions = dict(Meal.PORTIONS)
    for had, verb, portion, amount, kind in ((m.food, _("Ate"), m.portion, m.amount, "mass"),
                                              (m.water, _("Drank"), m.water_portion, m.water_amount, "volume")):
        if not had:
            continue
        text = verb
        if portion:
            text += " " + (_("all of the bowl") if portion == 1 else _("%(part)s bowl") % {"part": portions[portion]})
        if amount:
            shown = units.show(amount, kind, m.pet.household)
            text += f" ({shown})" if portion else f" {shown}"
        parts.append(text)
    return ", ".join(parts) + (f" · {m.notes[:40]}" if m.notes else "")


def _puke(p):
    text = _("Puke, %(where)s") % {"where": p.get_location_display().lower()}
    if p.spot_id:
        text += f" ({p.spot.name})"
    return text + (f" · {p.get_contents_display().lower()}" if p.contents != Puke.FOOD else "")


def _measurement(m):
    kind = "weight" if m.kind == Measurement.WEIGHT else "length"
    return f"{m.get_kind_display()} {units.show(m.value, kind, m.pet.household, digits=2)}"


def _note(n):
    return n.text[:80]


def _training(t):
    return f"{t.command.name}: {t.score} {t.get_score_display()}"


# kind: (time field, summary, household queryset, select/prefetch for pets)
KINDS = {
    "potty": ("time", _potty, lambda h: PottyBreak.everything.filter(pet__household=h).select_related("pet", "spot")),
    "walk": ("start", _walk, lambda h: Walk.objects.filter(household=h).select_related("walker", "household").prefetch_related("pets")),
    "sleep": ("start", _sleep, lambda h: Sleep.objects.filter(pet__household=h).select_related("pet")),
    "fill": ("time", _fill, lambda h: Fill.objects.filter(container__household=h).select_related("container__household").prefetch_related("container__pets")),
    "meal": ("time", _meal, lambda h: Meal.objects.filter(pet__household=h).select_related("pet__household")),
    "puke": ("time", _puke, lambda h: Puke.objects.filter(pet__household=h).select_related("pet", "spot")),
    "measurement": ("date", _measurement, lambda h: Measurement.objects.filter(pet__household=h).select_related("pet__household")),
    "note": ("time", _note, lambda h: Note.objects.filter(household=h).select_related("pet")),
    "training": ("time", _training, lambda h: TrainingLog.objects.filter(command__pet__household=h).select_related("command__pet")),
}
LIST_TITLES = {
    "potty": _("Potty breaks"), "walk": _("Walks"), "sleep": _("Sleep"), "fill": _("Bowl fills"),
    "meal": _("Meals"), "puke": _("Puke"), "measurement": _("Measurements"), "note": _("Notes"), "training": _("Training"),
}


def pet_names(kind, entry):
    if kind == "walk":
        return ", ".join(p.name for p in entry.pets.all())
    if kind == "fill":
        return ", ".join(p.name for p in entry.container.pets.all())
    if kind == "training":
        return entry.command.pet.name
    pet = getattr(entry, "pet", None)
    return pet.name if pet else _("Household")


def row(kind, entry):
    field, summary, _qs = KINDS[kind]
    return {"time": _when(getattr(entry, field)), "kind": kind, "summary": summary(entry),
            "pets": pet_names(kind, entry), "edit_url": reverse("pets:edit", args=[kind, entry.pk]),
            "date_only": field == "date"}  # measurements have a day, not a time


# Recent activity colours, as on the charts: outdoor potties green, accidents yellow (pee) and red (poop), meals
# blue, sleep purple, walks green. Shown as a faint tint with a coloured edge.
COLOURS = {"pee": "143,209,138", "poop": "46,125,50", "accident_pee": "245,211,63", "accident_poop": "167,36,49",
           "walk": "76,175,80", "meal": "74,144,226", "sleep": "142,108,200", "puke": "196,120,60", "nothing": "150,150,150",
           "measurement": "23,162,184"}


def _tags(kind, entry):
    """What the Recent activity filters match: pee, poop, accident, nothing, walk, meal, sleep, fill, other."""
    if kind == "potty":
        tags = [t for t, flag in (("pee", entry.pee), ("poop", entry.poop)) if flag] or ["nothing"]
        return tags + (["accident"] if entry.accident else [])
    return [kind] if kind in ("walk", "meal", "sleep", "fill", "measurement", "puke") else ["other"]


def _colour(kind, entry):
    if kind == "potty":
        if not entry.pee and not entry.poop:
            return COLOURS["nothing"]
        if entry.accident:
            return COLOURS["accident_poop" if entry.poop else "accident_pee"]
        return COLOURS["poop" if entry.poop else "pee"]
    return COLOURS.get(kind)


def for_pet(kind, queryset, pet):
    if kind == "walk":
        return queryset.filter(pets=pet)
    if kind == "fill":
        return queryset.filter(container__pets=pet)
    if kind == "training":
        return queryset.filter(command__pet=pet)
    return queryset.filter(pet=pet)


ACTIVITY_PAGE = 40
# What each All activity filter looks through: potties count on their own and on the walks they were part of.
POTTY_FILTERS = {"pee": {"pee": True}, "poop": {"poop": True}, "accident": {"accident": True}}
FILTER_KINDS = {"walk": ["walk"], "meal": ["meal"], "sleep": ["sleep"], "measurement": ["measurement"], "puke": ["puke"],
                **{name: ["walk", "potty"] for name in POTTY_FILTERS}}


def activity(pet, filter="all", page=0, size=ACTIVITY_PAGE):
    """One page of the pet's whole history, newest first (PUP-143), and whether there's more. Potty breaks on a walk
    are listed inside it (children); a potty filter shows the walks that had one, with just the matching potties."""
    from .schedule import remember_due

    potty_match = POTTY_FILTERS.get(filter, {})
    want = (page + 1) * size  # each kind's newest `want` are enough to know the first `want` overall
    rows = []
    for kind in FILTER_KINDS.get(filter, [k for k in KINDS if k != "training"]):  # training has its own card
        field, _summary, qs = KINDS[kind]
        entries = for_pet(kind, qs(pet.household), pet)
        if kind == "potty":
            entries = entries.filter(walk=None, **potty_match)
        elif kind == "walk" and potty_match:
            entries = entries.filter(potty_breaks__pet=pet, **{f"potty_breaks__{k}": v for k, v in potty_match.items()})
        for e in entries.distinct().order_by(f"-{field}", "-pk")[:want + 1]:
            if kind == "potty" and e.accident and e.due_at is None and not e.found_later:
                remember_due(e)  # logged before this was kept: work it out once
            rows.append((kind, e, row(kind, e)))
    rows.sort(key=lambda r: (r[2]["time"], r[1].pk), reverse=True)
    more = len(rows) > want
    result = []
    for kind, e, r in rows[page * size:want]:
        r.update(tags=_tags(kind, e), colour=_colour(kind, e), children=[])
        if kind == "walk":
            potties = PottyBreak.everything.filter(walk=e, pet=pet, **potty_match).select_related("pet", "spot")
            r["children"] = [{**row("potty", p), "tags": _tags("potty", p), "colour": _colour("potty", p)}
                             for p in potties.order_by("-time")]  # newest first, like the feed
            r["tags"] = sorted(set(r["tags"]).union(*(c["tags"] for c in r["children"])))
        result.append(r)
    return result, more


def recent_activity(pet, limit=ACTIVITY_PAGE):
    return activity(pet, size=limit)[0]
