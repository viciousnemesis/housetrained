"""The pantry (PUP-56, PUP-57, PUP-58, PUP-210): each food or litter, its open bag or can and the ones still in the
pantry, how much is left, usage per day, and when to warn that it's running low.

A bag or can is only ever finished or opened because someone said so (Finished this can / Opened a new can on the
meal or litter entry): the amounts are estimates, so they never close one on their own."""
import datetime

from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from . import units
from .models import Fill, Product

USAGE_DAYS = 14  # usage rate looks back this far (or to the day the first bag was opened, if later)
CAN_GUESS = 0.9  # a can is guessed finished when a meal takes at least this share of what's left
SMALL = {"kg": "g", "lb": "oz"}


def unit_of(product):
    """The unit the food was added in, else the household's bag unit."""
    return product.unit or units.unit_for(product.household, "bag")


def show(product, grams):
    """An amount in the food's own unit, in g or oz when it's under 1 kg or lb, as exact as it was entered."""
    unit = unit_of(product)
    value = units.from_stored(grams, unit, units.MASS)
    if unit in SMALL and abs(value) < 1:
        unit = SMALL[unit]
        value = units.from_stored(grams, unit, units.MASS)
    text = f"{value:.{3 if unit in SMALL else 1}f}".rstrip("0").rstrip(".")
    return f"{text} {unit}"


def noun(product, n=1):
    """Can or bag: what one package of this food is called."""
    if product.category == Product.WET_FOOD:
        return ngettext("can", "cans", n)
    return ngettext("bag", "bags", n)


def left_in(item):
    """What's left by the numbers: its size less what was taken from it. Below zero when more went out than the
    label says; it stays open all the same until someone says it's finished."""
    used = sum(amount - (spill or 0) for amount, spill in item.fills.exclude(amount=None).values_list("amount", "spill"))
    return item.size - used


def open_item(product):
    return product.items.filter(finished=None).exclude(opened=None).order_by("opened", "pk").first()


def pantry(product):
    return product.items.filter(finished=None, opened=None).order_by("pk")


def in_stock(products):
    """The products with a bag or can open or in the pantry."""
    return [p for p in products if p.items.filter(finished=None).exists()]


def release(fill):
    """Undo what this fill did to its bags or cans (before it's changed or deleted): the one it finished is open
    again, the one it opened goes back to the pantry if nothing else took from it, and later fills that moved to
    the new one move back."""
    new = fill.supply_item if fill.opened_new else None
    old = fill.finished_item if fill.finished_item_id and fill.finished_item_id != fill.supply_item_id else None
    if fill.finished_item_id:
        fill.finished_item.finished = None
        fill.finished_item.save(update_fields=["finished"])
    if new:
        if old:
            Fill.objects.filter(supply_item=new, time__gt=fill.time).exclude(pk=fill.pk).update(supply_item=old)
        if not new.fills.exclude(pk=fill.pk).exists():
            new.opened = None
            new.save(update_fields=["opened"])
        fill.supply_item = old
    fill.finished_item, fill.spill, fill.opened_new = None, None, False


def take(fill, product, finished=False, opened=False):
    """Point a fill (not saved yet) at the bag or can it came from. Finished: the one it came from is finished
    with it. Opened: the open one is finished (anything left in it is ignored) and the next from the pantry is
    opened for this fill; with Finished too, the fill takes what was left of the old one and the rest from the new
    one. Call release() first when changing a fill."""
    day = timezone.localtime(fill.time).date()
    before = fill.supply_item
    current = before if before and before.product_id == product.pk and before.finished is None else open_item(product)
    fill.finished_item, fill.spill, fill.opened_new = None, None, False
    new = pantry(product).first() if opened or current is None else None
    if new is None:
        fill.supply_item = current
        if finished and current:
            fill.finished_item = current
            current.finished = day
            current.save(update_fields=["finished"])
        return fill
    if current:
        if finished:
            fill.spill = min(max(left_in(current), 0), fill.amount or 0)
        fill.finished_item = current
        current.finished = day
        current.save(update_fields=["finished"])
    new.opened = day
    new.save(update_fields=["opened"])
    fill.supply_item, fill.opened_new = new, True
    if current and fill.pk:  # an older entry: what came after it now comes from the new one
        Fill.objects.filter(supply_item=current, time__gt=fill.time).exclude(pk=fill.pk).update(supply_item=new)
    return fill


def restock(product, count, pets=None):
    """Put `count` more bags or cans of the food in the pantry, shared by the same pets as the last one."""
    from .models import Pet, SupplyItem

    if pets is None:
        last = product.items.order_by("-pk").first()
        pets = list(last.pets.all()) if last else list(Pet.objects.filter(household=product.household, active=True))
    for _n in range(count):
        SupplyItem.objects.create(product=product, size=product.package_size or 0).pets.set(pets)


def picker_info(product):
    """What the Finished / Opened buttons and the form's warnings need about a food, for the page script."""
    item = open_item(product)
    left = left_in(item) if item else None
    return {
        "left_text": show(product, max(left, 0)) if item else None,
        "noun": str(noun(product)), "nouns": str(noun(product, 2)), "can": product.category == Product.WET_FOOD,
        "left": left, "no_guess": bool(item and item.no_guess),
        "pantry": pantry(product).count(), "low_by": product.low_by, "low_at": product.low_at,
        "size": product.package_size,
    }


def product_status(product, now=None):
    now = now or timezone.now()
    item = open_item(product)
    left = left_in(item) if item else None
    in_pantry = list(pantry(product))
    total = max(left or 0, 0) + sum(i.size for i in in_pantry)
    first_opened = product.items.exclude(opened=None).order_by("opened").values_list("opened", flat=True).first()
    since = now - datetime.timedelta(days=USAGE_DAYS)
    if first_opened:
        since = max(since, timezone.make_aware(datetime.datetime.combine(first_opened, datetime.time())))
    recent = Fill.objects.filter(supply_item__product=product, time__gte=since).aggregate(s=Sum("amount"))["s"] or 0
    rate = recent / max((now - since).total_seconds() / 86400, 1)
    days_left = total / rate if rate else None
    packages = (1 if item else 0) + len(in_pantry)
    low = packages <= product.low_at if product.low_by == Product.PACKAGES else total <= product.low_at
    return {
        "product": product,
        "item": item,
        "left": show(product, max(left, 0)) if item else None,
        "empty": item is not None and left <= 0,
        "size": show(product, item.size) if item else None,
        "pct_left": round(max(left, 0) / item.size * 100) if item and item.size else 0,
        "pantry": len(in_pantry),
        "packages": packages,
        "noun": noun(product), "nouns": noun(product, 2),
        "total_left": show(product, total),
        "per_day": show(product, rate) if rate else None,
        "days_left": round(days_left) if days_left is not None else None,
        "runs_out": (timezone.localdate() + datetime.timedelta(days=days_left)) if days_left is not None else None,
        "low": low,
    }


def left_text(status):
    """"2 cans left (450 g).", or "None left."."""
    if not status["packages"]:
        return str(_("None left."))
    return _("%(n)s %(what)s left (%(left)s).") % {
        "n": status["packages"], "what": noun(status["product"], status["packages"]), "left": status["total_left"]}


def item_status(item):
    """One bag or can on its food's page: what's left of it, and whether it's open, in the pantry or finished."""
    left = max(left_in(item), 0)
    return {
        "item": item,
        "left": show(item.product, left),
        "size": show(item.product, item.size),
        "pct_left": round(left / item.size * 100) if item.size else 0,
    }


def tracked(household):
    """Foods and litter that have had a bag or can."""
    return Product.objects.filter(household=household, items__isnull=False).distinct().select_related("household")


def household_supplies(household):
    return [product_status(p) for p in tracked(household)]


def pet_supplies(pet):
    return [product_status(p) for p in tracked(pet.household).filter(items__pets=pet).distinct()]
