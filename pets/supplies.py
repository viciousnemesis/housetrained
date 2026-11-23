"""The pantry (PUP-56, PUP-57, PUP-58, PUP-213): how much is left of each food and litter, the open bag or can and
the ones still in the pantry, usage per day, and when to warn that it's running low."""
import datetime

from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext as _

from . import units
from .models import Fill, Product

USAGE_DAYS = 14  # usage rate looks back this far (or to the day the first bag was opened, if later)


def display_unit(household):
    return units.unit_for(household, "bag")


def left_in(item):
    used = item.fills.aggregate(s=Sum("amount"))["s"] or 0
    return max(item.size - used, 0)


def _stock(product):
    """The bags or cans that still have something in them: open ones first (oldest first), then the pantry."""
    items = product.items.filter(finished=None).order_by("opened", "pk")
    rows = [(item, left_in(item)) for item in items]
    opened = [(i, left) for i, left in rows if i.opened and left > 0]
    pantry = [(i, left) for i, left in rows if not i.opened]
    return opened + pantry


def draw(product, day=None):
    """The bag or can a meal takes this product from: the open one with food left, else the next from the pantry,
    which is opened now. An open one that's empty is marked finished. None when there's none left."""
    day = day or timezone.localdate()
    for item in product.items.filter(finished=None).exclude(opened=None).order_by("opened", "pk"):
        if left_in(item) > 0:
            return item
        item.finished = day
        item.save(update_fields=["finished"])
    item = product.items.filter(finished=None, opened=None).order_by("pk").first()
    if item:
        item.opened = day
        item.save(update_fields=["opened"])
    return item


def in_stock(products):
    """The products that have something left, open or in the pantry."""
    return [p for p in products if _stock(p)]


def product_status(product, unit=None, now=None):
    now = now or timezone.now()
    unit = unit or display_unit(product.household)
    stock = _stock(product)
    total = sum(left for _i, left in stock)
    current = next(((i, left) for i, left in stock if i.opened), None)
    first_opened = product.items.exclude(opened=None).order_by("opened").values_list("opened", flat=True).first()
    since = now - datetime.timedelta(days=USAGE_DAYS)
    if first_opened:
        since = max(since, timezone.make_aware(datetime.datetime.combine(first_opened, datetime.time())))
    fills = Fill.objects.filter(supply_item__product=product)
    recent = fills.filter(time__gte=since).aggregate(s=Sum("amount"))["s"] or 0
    rate = recent / max((now - since).total_seconds() / 86400, 1)
    days_left = total / rate if rate else None
    show = lambda grams: units.fmt(units.from_stored(grams, unit, units.MASS), unit)
    low = len(stock) <= product.low_at if product.low_by == Product.PACKAGES else total <= product.low_at
    return {
        "product": product,
        "item": current[0] if current else (stock[0][0] if stock else None),
        "left": show(current[1]) if current else None,
        "size": show(current[0].size) if current else None,
        "pct_left": round(current[1] / current[0].size * 100) if current and current[0].size else 0,
        "pantry": sum(1 for i, _l in stock if not i.opened),
        "packages": len(stock),
        "total_left": show(total),
        "per_day": units.show(rate, "mass", product.household) if rate else None,
        "days_left": round(days_left) if days_left is not None else None,
        "runs_out": (timezone.localdate() + datetime.timedelta(days=days_left)) if days_left is not None else None,
        "low": low,
        "unknown_fills": fills.filter(amount=None).count(),
    }


def left_text(status):
    """"2 packages left (450 g)", or "None left"."""
    from django.utils.translation import ngettext

    if not status["packages"]:
        return str(_("None left."))
    return ngettext("%(n)s package left (%(left)s).", "%(n)s packages left (%(left)s).", status["packages"]) % {
        "n": status["packages"], "left": status["total_left"]}


def item_status(item, unit):
    """One bag or can, for Setup's list: what's left of it, and whether it's open, in the pantry or finished."""
    left = left_in(item)
    return {
        "item": item,
        "left": units.fmt(units.from_stored(left, unit, units.MASS), unit),
        "size": units.fmt(units.from_stored(item.size, unit, units.MASS), unit),
        "pct_left": round(left / item.size * 100) if item.size else 0,
        "pets": ", ".join(p.name for p in item.pets.all()),
    }


def _tracked(household):
    """Products that have had a bag or can: the ones the pantry keeps an eye on."""
    return Product.objects.filter(household=household, items__isnull=False).distinct().select_related("household")


def household_supplies(household):
    unit = display_unit(household)
    return [product_status(p, unit) for p in _tracked(household)]


def pet_supplies(pet):
    unit = display_unit(pet.household)
    return [product_status(p, unit) for p in _tracked(pet.household).filter(items__pets=pet).distinct()]
