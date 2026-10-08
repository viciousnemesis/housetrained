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
        return {"request": context["request"], "help_bubbles": context.get("help_bubbles"), "pet": pet, **builder(pet)}

    tag.__name__ = f"card_{name}"
    register.inclusion_tag(f"pets/cards/{name}.html", takes_context=True)(tag)


for _name, _builder in (
    ("next_walk", insights.next_walk_card),
    ("statistics", insights.statistics_card),
    ("walks", insights.walks_card),
    ("last_potty", insights.last_potty_card),
    ("potty_bars", insights.potty_bars_card),
    ("accidents", insights.accidents_chart),
    ("last_fed", insights.last_fed_card),
    ("feeding_times", insights.feedings_chart),
    ("sleep", insights.sleep_card),
):
    _card(_name, _builder)


@register.inclusion_tag("pets/cards/supplies.html", takes_context=True)
def card_supplies(context, pet=None):
    """Open bags with estimates: for one pet, or for the whole household."""
    from ..supplies import household_supplies, pet_supplies
    household = current_household(context["request"].user)
    return {"request": context["request"], "supplies": pet_supplies(pet) if pet else household_supplies(household)}


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
    for walk in Walk.objects.filter(household=household, end=None).prefetch_related("pets"):
        running.append({"kind": "walk", "label": "Walking", "id": walk.pk, "start": walk.start,
                        "names": ", ".join(p.name for p in walk.pets.all())})
    groups = {}
    for sleep in Sleep.objects.filter(pet__household=household, end=None).select_related("pet").order_by("start", "pet__name"):
        groups.setdefault(sleep.start, []).append(sleep)
    for start, sleeps in groups.items():
        running.append({"kind": "sleep", "label": "Sleeping", "id": sleeps[0].pk, "start": start,
                        "names": ", ".join(s.pet.name for s in sleeps)})
    return sorted(running, key=lambda r: r["start"])
