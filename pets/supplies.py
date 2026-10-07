"""Bag estimates: amount left, usage per day, days left and low-supply reminders (PUP-56, PUP-57, PUP-58)."""
import datetime

from django.db.models import Sum
from django.utils import timezone

from . import units
from .models import SupplyItem

USAGE_DAYS = 14  # usage rate looks back this far (or to the day the bag was opened, if later)


def display_unit(household):
    return "lb" if household.units == "imperial" else "kg"


def small_unit(unit):
    return "oz" if unit == "lb" else "g"


def status(item, unit, now=None):
    now = now or timezone.now()
    used = item.fills.aggregate(s=Sum("amount"))["s"] or 0
    left = max(item.size - used, 0)
    since = now - datetime.timedelta(days=USAGE_DAYS)
    if item.opened:
        since = max(since, timezone.make_aware(datetime.datetime.combine(item.opened, datetime.time())))
    recent = item.fills.filter(time__gte=since).aggregate(s=Sum("amount"))["s"] or 0
    days = max((now - since).total_seconds() / 86400, 1)
    rate = recent / days
    days_left = left / rate if rate else None
    unknown = item.fills.filter(amount=None).count()
    return {
        "item": item,
        "left": units.fmt(units.from_stored(left, unit, units.MASS), unit),
        "size": units.fmt(units.from_stored(item.size, unit, units.MASS), unit),
        "pct_left": round(left / item.size * 100) if item.size else 0,
        "per_day": units.fmt(units.from_stored(rate, small_unit(unit), units.MASS), small_unit(unit)) if rate else None,
        "days_left": round(days_left) if days_left is not None else None,
        "runs_out": (timezone.localdate() + datetime.timedelta(days=days_left)) if days_left is not None else None,
        "low": days_left is not None and days_left <= item.product.reorder_days,
        "unknown_fills": unknown,
        "pets": ", ".join(p.name for p in item.pets.all()),
    }


def open_items(household):
    return SupplyItem.objects.filter(product__household=household, finished=None).select_related("product").prefetch_related("pets")


def household_supplies(household):
    unit = display_unit(household)
    return [status(item, unit) for item in open_items(household)]


def pet_supplies(pet):
    unit = display_unit(pet.household)
    return [status(item, unit) for item in open_items(pet.household).filter(pets=pet)]
