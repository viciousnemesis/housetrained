"""One-line summaries of entries, shared by the recent-activity list, the per-type lists and the timeline."""
import datetime

from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from . import units
from .models import Fill, Meal, Measurement, Note, PottyBreak, Sleep, Walk


def _when(value):
    if isinstance(value, datetime.datetime):
        return value
    return timezone.make_aware(datetime.datetime.combine(value, datetime.time()))  # measurements have a date only


def _potty(b):
    what = " + ".join(w for w, flag in ((_("Pee"), b.pee), (_("Poop"), b.poop)) if flag)
    return f"{what}, {b.get_location_display().lower()}"


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
    if m.food:
        parts.append(_("Ate") + (f" {units.show(m.amount, 'mass', m.pet.household)}" if m.amount else ""))
    if m.water:
        parts.append(_("Drank") + (f" {units.show(m.water_amount, 'volume', m.pet.household)}" if m.water_amount else ""))
    return ", ".join(parts) + (f" · {m.notes[:40]}" if m.notes else "")


def _measurement(m):
    kind = "weight" if m.kind == Measurement.WEIGHT else "length"
    return f"{m.get_kind_display()} {units.show(m.value, kind, m.pet.household, digits=2)}"


def _note(n):
    return n.text[:80]


# kind: (time field, summary, household queryset, select/prefetch for pets)
KINDS = {
    "potty": ("time", _potty, lambda h: PottyBreak.objects.filter(pet__household=h).select_related("pet")),
    "walk": ("start", _walk, lambda h: Walk.objects.filter(household=h).select_related("walker", "household").prefetch_related("pets")),
    "sleep": ("start", _sleep, lambda h: Sleep.objects.filter(pet__household=h).select_related("pet")),
    "fill": ("time", _fill, lambda h: Fill.objects.filter(container__household=h).select_related("container__household").prefetch_related("container__pets")),
    "meal": ("time", _meal, lambda h: Meal.objects.filter(pet__household=h).select_related("pet__household")),
    "measurement": ("date", _measurement, lambda h: Measurement.objects.filter(pet__household=h).select_related("pet__household")),
    "note": ("time", _note, lambda h: Note.objects.filter(household=h).select_related("pet")),
}
LIST_TITLES = {
    "potty": _("Potty breaks"), "walk": _("Walks"), "sleep": _("Sleep"), "fill": _("Bowl fills"),
    "meal": _("Meals"), "measurement": _("Measurements"), "note": _("Notes"),
}


def pet_names(kind, entry):
    if kind == "walk":
        return ", ".join(p.name for p in entry.pets.all())
    if kind == "fill":
        return ", ".join(p.name for p in entry.container.pets.all())
    pet = getattr(entry, "pet", None)
    return pet.name if pet else _("Household")


def row(kind, entry):
    field, summary, _qs = KINDS[kind]
    return {"time": _when(getattr(entry, field)), "kind": kind, "summary": summary(entry),
            "pets": pet_names(kind, entry), "edit_url": reverse("pets:edit", args=[kind, entry.pk])}


def for_pet(kind, queryset, pet):
    if kind == "walk":
        return queryset.filter(pets=pet)
    if kind == "fill":
        return queryset.filter(container__pets=pet)
    return queryset.filter(pet=pet)


def recent_activity(pet, limit=20):
    rows = []
    for kind, (field, _summary, qs) in KINDS.items():
        rows += [row(kind, e) for e in for_pet(kind, qs(pet.household), pet).order_by(f"-{field}")[:limit]]
    return sorted(rows, key=lambda r: r["time"], reverse=True)[:limit]


def day_timeline(household, date):
    start = timezone.make_aware(datetime.datetime.combine(date, datetime.time()))
    end = start + datetime.timedelta(days=1)
    rows = []
    for kind, (field, _summary, qs) in KINDS.items():
        if field == "date":
            entries = qs(household).filter(date=date)
        else:
            entries = qs(household).filter(**{f"{field}__gte": start, f"{field}__lt": end})
        rows += [row(kind, e) for e in entries]
    return sorted(rows, key=lambda r: r["time"])
