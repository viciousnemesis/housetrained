"""Reminders (PUP-58): walk due and buy more, sent as browser push notifications.

`check()` looks at every household with someone signed up and sends what's due. Each reminder has a key and is
claimed in ReminderLog before sending, so it goes out once even when several server processes check at the
same time. The web server runs `check()` every few minutes in a background thread (started by
ReminderMiddleware); `manage.py housetrained_reminders` runs it once, for cron.
"""
import datetime
import logging
import threading
import time

from django.conf import settings
from django.db import IntegrityError, close_old_connections, transaction
from django.utils import timezone, translation
from django.utils.translation import gettext as _

from .models import Household, NotificationPreference, PushSubscription, ReminderLog

log = logging.getLogger(__name__)
INTERVAL = 300  # seconds between checks
WALK_LATE_LIMIT = datetime.timedelta(hours=2)  # a walk due longer ago than this isn't announced any more


def _claim(key):
    try:
        with transaction.atomic():
            ReminderLog.objects.create(key=key)
        return True
    except IntegrityError:
        return False


def _people(household, preference):
    """Household members with a device signed up who want this kind of reminder."""
    users = household.members.filter(push_subscriptions__isnull=False, is_active=True).distinct()
    wanted = []
    for user in users:
        prefs = NotificationPreference.objects.filter(user=user).first()
        if prefs is None or getattr(prefs, preference):
            wanted.append(user)
    return wanted


def _in_user_zone(user):
    zone = getattr(getattr(user, "settings", None), "timezone", None)
    return timezone.override(zone) if zone else timezone.override(timezone.get_current_timezone())


def walk_reminders(household, now):
    from . import push
    from .clock import show_time
    from .insights import next_walk_card

    people = _people(household, "walk_due")
    if not people:
        return 0
    sent = 0
    for pet in household.pets.filter(active=True):
        card = next_walk_card(pet, now=now)
        if not card["predictions"]:
            continue
        due = card["head_out"]  # when to leave, which is a little before the pet needs to go
        if not (now - WALK_LATE_LIMIT <= due <= now):
            continue
        # One reminder per predicted walk: the key changes once a potty break moves the prediction on.
        if not _claim(f"walk:{pet.pk}:{due:%Y%m%d%H%M}"):
            continue
        for user in people:
            with _in_user_zone(user):
                body = _("%(what)s expected ~%(time)s") % {
                    "what": (card["expect"] or _("potty")).capitalize(),
                    "time": show_time(card["predictions"][0]["time"])}
            title = _("%(pet)s: time for a walk") % {"pet": pet.name}
            sent += push.send(user, title, body, url=f"/pets/{pet.slug}/", tag=f"walk-{pet.pk}")
    return sent


def supply_reminders(household, now):
    from . import push
    from .supplies import household_supplies

    people = _people(household, "low_supply")
    if not people:
        return 0
    sent = 0
    for status in household_supplies(household):
        if not status["low"]:
            continue
        item = status["item"]
        if not _claim(f"supply:{item.pk}:{timezone.localdate(now):%Y%m%d}"):
            continue
        body = _("About %(days)s days left (%(left)s).") % {"days": status["days_left"], "left": status["left"]}
        title = _("Buy more %(product)s") % {"product": item.product.name}
        for user in people:
            sent += push.send(user, title, body, url="/pets/", tag=f"supply-{item.pk}")
    return sent


def retry_reminders(household, now):
    """Taken out, nothing happened: a nudge when it's time to try again."""
    from . import push

    people = _people(household, "walk_due")
    if not people:
        return 0
    sent = 0
    for pet in household.pets.filter(active=True, retry_at__lte=now, retry_at__gte=now - WALK_LATE_LIMIT):
        if not _claim(f"retry:{pet.pk}:{pet.retry_at:%Y%m%d%H%M}"):
            continue
        title = _("%(pet)s: try again") % {"pet": pet.name}
        for user in people:
            sent += push.send(user, title, _("Time for another potty trip."), url=f"/pets/{pet.slug}/",
                              tag=f"walk-{pet.pk}")
    return sent


def household_night(household, now):
    """The dogs' learned night (earliest bedtime among them), as datetimes for today: (bedtime, wake-up)."""
    from .insights import Night, Rules, is_accident, potty, window_start

    rules = Rules.load()
    nights = []
    for pet in household.pets.filter(active=True, type="dog"):
        breaks = [b.time for b in potty(pet, window_start(), until=now) if not is_accident(b)]
        nights.append(Night(breaks, now, rules))
    if not nights:
        return None
    night = min(nights, key=lambda n: (n.bed - 12 * 60) % (24 * 60))  # earliest bedtime, counted from noon
    today = timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)
    bed = today + datetime.timedelta(minutes=int(night.bed))
    if night.bed < 12 * 60:  # bedtime after midnight belongs to tonight
        bed += datetime.timedelta(days=1)
    wake = today + datetime.timedelta(minutes=int(night.wake))
    return bed, wake


def water_reminders(household, now):
    """Pick up the water bowl a few hours before bed; put it back after waking up."""
    from . import push
    from .clock import show_time

    if not household.water_cutoff_hours:
        return 0
    people = _people(household, "water_cutoff")
    if not people:
        return 0
    times = household_night(household, now)
    if not times:
        return 0
    bed, wake = times
    cutoff = bed - datetime.timedelta(hours=household.water_cutoff_hours)
    window = datetime.timedelta(minutes=45)
    sent = 0
    day = timezone.localdate(now)
    if cutoff <= now <= cutoff + window and _claim(f"water-up:{household.pk}:{day:%Y%m%d}"):
        for user in people:
            with _in_user_zone(user):
                body = _("Bedtime is usually ~%(time)s.") % {"time": show_time(bed)}
            sent += push.send(user, _("Pick up the water bowl"), body, url="/pets/", tag="water")
    if wake <= now <= wake + window and _claim(f"water-down:{household.pk}:{day:%Y%m%d}"):
        for user in people:
            sent += push.send(user, _("Water bowl back down"), _("Good morning! Fresh water time."), url="/pets/",
                              tag="water")
    return sent


def weekly_reminders(household, now):
    """Monday from 9 am (household time): each pet's week in one line."""
    from . import push
    from .progress import summary

    local = timezone.localtime(now)
    if local.weekday() != 0 or local.hour < 9:
        return 0
    people = _people(household, "weekly_progress")
    if not people:
        return 0
    sent = 0
    year, week, _day = local.isocalendar()
    for pet in household.pets.filter(active=True):
        text = summary(pet, now)
        if not text or not _claim(f"weekly:{pet.pk}:{year}-{week}"):
            continue
        title = _("%(pet)s's week") % {"pet": pet.name}
        for user in people:
            sent += push.send(user, title, text, url=f"/pets/{pet.slug}/progress/", tag=f"weekly-{pet.pk}")
    return sent


def check(now=None):
    """Send whatever is due, for every household with someone signed up. Returns how many were delivered."""
    from . import push

    now = now or timezone.now()
    sent = 0
    if not push.allowed():
        return 0
    households = Household.objects.filter(memberships__user__push_subscriptions__isnull=False).distinct()
    with translation.override(settings.LANGUAGE_CODE):
        for household in households:
            zone = household.time_zone or settings.TIME_ZONE
            with timezone.override(zone):
                try:
                    sent += walk_reminders(household, now)
                    sent += supply_reminders(household, now)
                    sent += weekly_reminders(household, now)
                    sent += retry_reminders(household, now)
                    sent += water_reminders(household, now)
                except Exception:
                    log.exception("Reminders for %s failed", household)
    ReminderLog.objects.filter(created__lt=now - datetime.timedelta(days=30)).delete()
    return sent


_started = False
_lock = threading.Lock()


def _loop():
    while True:
        time.sleep(INTERVAL)
        try:
            close_old_connections()
            if PushSubscription.objects.exists():
                check()  # does nothing unless an admin allowed push notifications
        except Exception:
            log.exception("Reminder check failed")
        finally:
            close_old_connections()


class ReminderMiddleware:
    """Starts the reminder thread in each web server process on its first request (management commands and
    tests never handle requests, so they don't start it)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        global _started
        if not _started and settings.HOUSETRAINED_REMINDER_THREAD:
            with _lock:
                if not _started:
                    threading.Thread(target=_loop, name="housetrained-reminders", daemon=True).start()
                    _started = True
        return self.get_response(request)
