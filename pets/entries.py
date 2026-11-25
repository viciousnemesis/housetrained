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
    """One food or the water from Log meal (PUP-216), e.g. "Fromm Wet Food: 67 g, ate ¾" or "Water: 80 ml"; only
    what was entered."""
    household = m.pet.household
    water = m.kind == Meal.WATER
    fill = m.water_fill if water else m.fill
    product = _product(m)
    what = _("Water") if water else str(product) if product else _("Food")
    kind = "volume" if water else "mass"
    bits = []
    if fill and fill.amount is not None:
        bits.append(units.show(fill.amount, kind, household))
    elif fill and fill.bowls is not None:
        bits.append(_("%(n)s bowl(s)") % {"n": f"{fill.bowls:g}"})
    portion, amount = (m.water_portion, m.water_amount) if water else (m.portion, m.amount)
    verb = _("drank") if water else _("ate")
    if portion:
        bits.append(f"{verb} {_('all') if portion == 1 else dict(Meal.PORTIONS)[portion]}")
    elif amount and not fill:
        bits.append(f"{verb} {units.show(amount, kind, household)}")  # older entries: an exact amount
    text = what + (": " + ", ".join(bits) if bits else "")
    return text + (f" · {m.notes[:40]}" if m.notes else "")


def _product(m):
    """The food a food entry came from: its bag or can's, else the one it was logged with."""
    if m.kind == Meal.WATER:
        return None
    if m.fill_id and m.fill.supply_item_id:
        return m.fill.supply_item.product
    return m.product


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
    # Fills logged with a meal are part of that meal's row (PUP-207); litter has its own (cats).
    "fill": ("time", _fill, lambda h: Fill.objects.filter(container__household=h, meals=None, water_meals=None).exclude(
        contents=Fill.LITTER).select_related("container__household").prefetch_related("container__pets")),
    "litter": ("time", _fill, lambda h: Fill.objects.filter(container__household=h, contents=Fill.LITTER).select_related(
        "container__household").prefetch_related("container__pets")),
    "meal": ("time", _meal, lambda h: Meal.objects.filter(pet__household=h).select_related(
        "pet__household", "fill__supply_item__product", "water_fill", "product")),
    "puke": ("time", _puke, lambda h: Puke.objects.filter(pet__household=h).select_related("pet", "spot")),
    "measurement": ("date", _measurement, lambda h: Measurement.objects.filter(pet__household=h).select_related("pet__household")),
    "note": ("time", _note, lambda h: Note.objects.filter(household=h).select_related("pet")),
    "training": ("time", _training, lambda h: TrainingLog.objects.filter(command__pet__household=h).select_related("command__pet")),
}
LIST_TITLES = {
    "potty": _("Potty breaks"), "walk": _("Walks"), "sleep": _("Sleep"), "fill": _("Bowl fills"),
    "litter": _("Litter"), "meal": _("Meals"), "puke": _("Puke"), "measurement": _("Measurements"), "note": _("Notes"), "training": _("Training"),
}


def pet_names(kind, entry):
    if kind == "walk":
        return ", ".join(p.name for p in entry.pets.all())
    if kind in ("fill", "litter"):
        return ", ".join(p.name for p in entry.container.pets.all())
    if kind == "training":
        return entry.command.pet.name
    pet = getattr(entry, "pet", None)
    return pet.name if pet else _("Household")


def row(kind, entry):
    field, summary, _qs = KINDS[kind]
    return {"time": _when(getattr(entry, field)), "kind": kind, "summary": summary(entry),
            "pets": pet_names(kind, entry), "edit_url": reverse("pets:edit", args=[kind, entry.pk]),
            "date_only": field == "date",  # measurements have a day, not a time
            "end": entry.end if kind == "walk" else None}  # walks show start-end (PUP-189)


# Activity colours (PUP-100, PUP-216), the same on the charts, looked up by kind each time a row is shown: outdoor
# potties and walks green; accidents and puke reds (pee light, poop dark); food orange (dry deeper, wet lighter);
# water blue; sleep purple; measurements teal. Shown as a faint tint with a coloured edge.
COLOURS = {"pee": "143,209,138", "poop": "46,125,50", "accident_pee": "240,110,110", "accident_poop": "167,36,49",
           "walk": "76,175,80", "dry_food": "160,205,245", "wet_food": "205,230,250", "water": "74,144,226",
           "sleep": "142,108,200", "puke": "205,70,60", "nothing": "150,150,150", "measurement": "23,162,184"}
HEX = {name: "#%02x%02x%02x" % tuple(int(c) for c in rgb.split(",")) for name, rgb in COLOURS.items()}


def meal_colour(meal):
    from .models import Product

    if meal.kind == Meal.WATER:
        return "water"
    product = _product(meal)
    return "wet_food" if product and product.category == Product.WET_FOOD else "dry_food"


def _tags(kind, entry):
    """What the activity filters match: pee, poop, accident, nothing, walk, food, water, litter, sleep, fill, note…"""
    if kind == "potty":
        tags = [t for t, flag in (("pee", entry.pee), ("poop", entry.poop)) if flag] or ["nothing"]
        return tags + (["accident"] if entry.accident else [])
    if kind == "meal":
        return [entry.kind]
    return [kind] if kind in ("walk", "sleep", "fill", "measurement", "puke", "litter", "note") else ["other"]


def _colour(kind, entry):
    if kind == "potty":
        if not entry.pee and not entry.poop:
            return COLOURS["nothing"]
        if entry.accident:
            return COLOURS["accident_poop" if entry.poop else "accident_pee"]
        return COLOURS["poop" if entry.poop else "pee"]
    if kind == "meal":
        return COLOURS[meal_colour(entry)]
    return COLOURS.get(kind)


def for_pet(kind, queryset, pet):
    if kind == "walk":
        return queryset.filter(pets=pet)
    if kind in ("fill", "litter"):
        return queryset.filter(container__pets=pet)
    if kind == "training":
        return queryset.filter(command__pet=pet)
    return queryset.filter(pet=pet)


ACTIVITY_PAGE = 40
# The activity filters (PUP-217), named the same on the pet page and Activities. Each looks through these kinds;
# potties count on their own and on the walks they were part of.
ACTIVITY_FILTERS = [("all", _("All")), ("walk", _("Walks")), ("pee", _("Pees")), ("poop", _("Poops")),
                    ("accident", _("Accidents")), ("food", _("Food")), ("water", _("Water")), ("litter", _("Litter")),
                    ("puke", _("Puke")), ("sleep", _("Sleep")), ("measurement", _("Measurements")), ("note", _("Notes"))]
POTTY_FILTERS = {"pee": {"pee": True}, "poop": {"poop": True}, "accident": {"accident": True}}
MEAL_FILTERS = {"food": Meal.FOOD, "water": Meal.WATER}
FILTER_KINDS = {"walk": ["walk"], "food": ["meal"], "water": ["meal"], "litter": ["litter"], "sleep": ["sleep"],
                "measurement": ["measurement"], "puke": ["puke"], "note": ["note"],
                **{name: ["walk", "potty"] for name in POTTY_FILTERS}}


def activity_filters(household, pet=None):
    """The filters that can match something here: Litter only where there are cats."""
    from .models import Pet

    cats = pet.type == Pet.CAT if pet else household.pets.filter(type=Pet.CAT).exists()
    return [f for f in ACTIVITY_FILTERS if f[0] != "litter" or cats]


def activity(household, pet=None, filter="all", page=0, size=ACTIVITY_PAGE):
    """One page of activity, newest first (PUP-143): one pet's, or the whole household's (PUP-217), and whether
    there's more. Potty breaks on a walk are listed inside it (children); a potty filter shows the walks that had
    one, with just the matching potties."""
    from .schedule import remember_due

    potty_match = POTTY_FILTERS.get(filter, {})
    want = (page + 1) * size  # each kind's newest `want` are enough to know the first `want` overall
    rows = []
    for kind in FILTER_KINDS.get(filter, [k for k in KINDS if k != "training"]):  # training has its own card
        field, _summary, qs = KINDS[kind]
        entries = qs(household)
        if pet:
            entries = for_pet(kind, entries, pet)
        if kind == "potty":
            entries = entries.filter(walk=None, **potty_match)
        elif kind == "walk" and potty_match:
            match = {f"potty_breaks__{k}": v for k, v in potty_match.items()}
            entries = entries.filter(**({"potty_breaks__pet": pet} if pet else {}), **match)
        elif kind == "meal" and filter in MEAL_FILTERS:
            entries = entries.filter(kind=MEAL_FILTERS[filter])
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
            potties = PottyBreak.everything.filter(walk=e, **potty_match).select_related("pet", "spot")
            if pet:
                potties = potties.filter(pet=pet)
            r["children"] = [{**row("potty", p), "tags": _tags("potty", p), "colour": _colour("potty", p)}
                             for p in potties.order_by("-time")]  # newest first, like the feed
            r["tags"] = sorted(set(r["tags"]).union(*(c["tags"] for c in r["children"])))
        result.append(r)
    return result, more


def recent_activity(pet, limit=ACTIVITY_PAGE):
    return activity(pet.household, pet, size=limit)[0]
