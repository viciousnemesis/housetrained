from django import template
from django.utils.translation import gettext as _

from .. import insights, units
from ..clock import show_datetime, show_time
from ..households import current_household
from ..models import Sleep, Walk

register = template.Library()


def _card(name, builder):
    """Register `card_<name>` as an inclusion tag rendering pets/cards/<name>.html for a pet."""

    def tag(context, pet):
        return {"request": context["request"], "help_bubbles": context.get("help_bubbles"),
                "pets_can_edit": context.get("pets_can_edit"), "pet": pet, **builder(pet)}

    tag.__name__ = f"card_{name}"
    register.inclusion_tag(f"pets/cards/{name}.html", takes_context=True)(tag)


for _name, _builder in (
    ("status", insights.status_card),
    ("today", insights.today_card),
    ("potty_bars", insights.potty_bars_card),
    ("accidents", insights.accidents_chart),
    ("measurements", insights.measurements_card),
    ("notes", insights.notes_card),
    ("training", insights.training_card),
):
    _card(_name, _builder)


@register.inclusion_tag("pets/cards/supplies.html", takes_context=True)
def card_supplies(context, pet=None, low_only=False):
    """Open bags with estimates: for one pet, or for the whole household. low_only: just the ones running low."""
    from ..supplies import household_supplies, pet_supplies
    household = current_household(context["request"].user)
    supplies = pet_supplies(pet) if pet else household_supplies(household)
    if low_only:
        supplies = [s for s in supplies if s["low"]]
    return {"request": context["request"], "supplies": supplies, "low_only": low_only}


@register.simple_tag(takes_context=True)
def amount(context, value, kind):
    """A stored amount in the household's units: {% amount container.amount_per_bowl "mass" %}."""
    return units.show(value, kind, current_household(context["request"].user))


@register.simple_tag(takes_context=True)
def help_bubble(context, text):
    """An (i) button that explains something, unless the user switched help bubbles off in their settings."""
    from django.utils.html import format_html

    if not context.get("help_bubbles"):
        return ""
    return format_html(
        '<button type="button" class="btn btn-link btn-sm p-0 ms-1 align-baseline text-reset opacity-75 help-bubble" '
        'data-bs-toggle="popover" data-bs-trigger="focus" data-bs-placement="top" data-bs-content="{}" '
        'data-bs-custom-class="help-popover" '
        'aria-label="{}">ⓘ</button>',
        text, _("Help"),
    )


@register.filter
def clock(dt):
    """A time in the user's 12- or 24-hour format."""
    return show_time(dt)


@register.filter
def clock_dt(dt):
    """Month, day and time in the user's 12- or 24-hour format."""
    return show_datetime(dt)


def _local_date(value):
    import datetime

    from django.utils import timezone

    return timezone.localdate(value) if isinstance(value, datetime.datetime) else value


@register.filter
def day_key(value):
    """The local day of a time (or a date), for grouping a list by day."""
    return _local_date(value).isoformat() if value else ""


@register.filter
def day_heading(value):
    """A day heading in a list (PUP-178): "Today", "Yesterday", then "Fri Oct 23"."""
    from django.utils import timezone

    date = _local_date(value)
    days = (timezone.localdate() - date).days
    if days == 0:
        return _("Today")
    if days == 1:
        return _("Yesterday")
    return insights.history_label(date)


@register.filter
def minutes(value):
    """150 -> "2h 30m"."""
    from ..schedule import fmt_minutes

    return fmt_minutes(value) if value is not None else ""


@register.filter
def get_item(mapping, key):
    return mapping.get(key, "")


@register.filter
def pet_age(birth):
    return insights.pet_age(birth)


@register.simple_tag(takes_context=True)
def pet_actions(context, pet=None):
    """What the top row needs: which pets it logs for, their potty spots, and what's running."""
    from ..models import PottyBreak

    user = context["request"].user
    household = current_household(user)
    pets = [pet] if pet else list(household.pets.filter(active=True))
    allowed = []
    for p in pets:
        for spot in p.allowed_potty_locations():
            if spot not in allowed:
                allowed.append(spot)
    labels = dict(PottyBreak.LOCATIONS)
    walks = Walk.objects.filter(household=household, end=None)
    sleeps = Sleep.objects.filter(pet__household=household, end=None)
    if pet:
        walks, sleeps = walks.filter(pets=pet), sleeps.filter(pet=pet)
    return {
        "pets": pets, "many": len(pets) > 1,
        "locations": [(spot, labels.get(spot, spot)) for spot in allowed],
        "usual": pets[0].allowed_potty_locations()[0] if pets else None,
        "walk": walks.first(), "sleep": sleeps.first(),
    }


@register.simple_tag
def health_alerts(pet):
    return insights.health_alerts(pet)


@register.simple_tag(takes_context=True)
def retry_reminders(context):
    """Pets taken out with nothing to show: try again soon."""
    import datetime

    from django.utils import timezone

    user = context["request"].user
    if not user.is_authenticated:
        return []
    household = current_household(user)
    recent = timezone.now() - datetime.timedelta(minutes=45)
    return list(household.pets.filter(active=True, retry_at__gte=recent).order_by("retry_at"))


@register.simple_tag(takes_context=True)
def running_activities(context):
    """Walks and sleeps without an end time; sleeps started together for several pets show as one."""
    user = context["request"].user
    if not user.is_authenticated:
        return []
    household = current_household(user)
    running = []
    from ..schedule import next_walk

    for walk in Walk.objects.filter(household=household, end=None).prefetch_related("pets"):
        pets = list(walk.pets.all())
        # What to expect on this walk (PUP-133): "pee + poop", or per pet when several are out.
        expects = [(p.name, next_walk(p, now=walk.start)["expect"]) for p in pets]  # as of leaving (PUP-168)
        expects = [(name, e) for name, e in expects if e]
        expect = expects[0][1] if len(pets) == 1 and expects else "; ".join(f"{n}: {e}" for n, e in expects)
        running.append({"kind": "walk", "label": "Walking", "id": walk.pk, "start": walk.start,
                        "names": ", ".join(p.name for p in pets), "pets": [p.slug for p in pets], "expect": expect})
    groups = {}
    for sleep in Sleep.objects.filter(pet__household=household, end=None).select_related("pet").order_by("start", "pet__name"):
        groups.setdefault(sleep.start, []).append(sleep)
    for start, sleeps in groups.items():
        running.append({"kind": "sleep", "label": "Sleeping", "id": sleeps[0].pk, "start": start,
                        "names": ", ".join(s.pet.name for s in sleeps)})
    return sorted(running, key=lambda r: r["start"])
