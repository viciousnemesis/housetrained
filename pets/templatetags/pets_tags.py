from django import template

from .. import insights, units
from ..households import current_household
from ..models import Sleep, Walk

register = template.Library()


def _card(name, builder):
    """Register `card_<name>` as an inclusion tag rendering pets/cards/<name>.html for a pet."""

    def tag(context, pet):
        return {"request": context["request"], "pet": pet, **builder(pet)}

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


@register.filter
def pet_age(birth):
    return insights.pet_age(birth)


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
