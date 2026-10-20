"""Health checks (PUP-159): short warnings on a pet's page when something in the potty log looks off.

Each check can be switched off, and the ones with a number can be tuned, per household (Setup > Health checks).
Their history is worked out from the logs, so the settings page can show when each one last went off.
"""
import datetime
from collections import defaultdict
from statistics import median

from django.utils import timezone
from django.utils.translation import gettext as _, gettext_lazy

from .models import PottyBreak

MAX_USUAL_DAYS = 28  # the longest "usual day" window offered
SAME_TIME = datetime.timedelta(minutes=30)  # pees closer together than this count as one time (top-ups)
# usual_days: "a usual day" is the median of this many days before (days with nothing logged left out); a puppy's
# habits change fast, so a week by default.
DEFAULTS = {"soft_count": 2, "dark_count": 2, "pee_factor": 1.5, "pee_extra": 3, "usual_days": 7}

CHECKS = [
    ("bloody_poop", gettext_lazy("Black or bloody poop"), "danger",
     gettext_lazy("Any black or red poop logged in the past day.")),
    ("pale_poop", gettext_lazy("Grey, white or yellow poop"), "warning",
     gettext_lazy("Any grey, white or yellow poop logged in the past day.")),
    ("soft_poop", gettext_lazy("Soft poop"), "warning",
     gettext_lazy("Soft poop logged this many times in the past day.")),
    ("dark_pee", gettext_lazy("Dark pee"), "warning",
     gettext_lazy("Dark pee logged this many times in the past day.")),
    ("more_pees", gettext_lazy("Peeing more than usual"), "warning",
     gettext_lazy("Pee times today against a usual day (the median of the days before). Pees less than 30 minutes "
                  "apart count as one time, so a top-up on the same walk doesn't add up.")),
]


def settings_for(household):
    """{"off": set of check keys, and the numbers}, with defaults filled in."""
    saved = household.health_checks or {}
    values = {key: saved.get(key, default) for key, default in DEFAULTS.items()}
    return {"off": set(saved.get("off", [])), **values}


def pee_times(times):
    """How many separate times: pees less than SAME_TIME after the last counted one are the same time."""
    count, last = 0, None
    for t in sorted(times):
        if last is None or t - last >= SAME_TIME:
            count, last = count + 1, t
    return count


def _trips(breaks, pees_by_day, day, now, cfg):
    """[(key, text)] for the checks that went off with the past day's `breaks`, on `day`, judged at `now`."""
    found = []
    if any(b.poop_colour in ("black", "red") for b in breaks):
        found.append(("bloody_poop", _("Black or bloody poop in the past day. That can mean bleeding: call your vet.")))
    if any(b.poop_colour in ("grey", "yellow") for b in breaks):
        found.append(("pale_poop", _("Grey, white or yellow poop in the past day. Worth mentioning to your vet if it "
                                     "happens again.")))
    soft = sum(1 for b in breaks if b.poop_consistency == "soft")
    if soft >= cfg["soft_count"]:
        found.append(("soft_poop", _("Soft poop %(n)d times in the past day. Keep an eye on water intake; call your "
                                     "vet if it lasts more than a day or comes with vomiting or tiredness.") % {"n": soft}))
    dark = sum(1 for b in breaks if b.pee_colour == "dark")
    if dark >= cfg["dark_count"]:
        found.append(("dark_pee", _("Dark pee %(n)d times in the past day. Make sure fresh water is available.")
                      % {"n": dark}))
    window = cfg["usual_days"]
    before = [pee_times(pees_by_day[day - datetime.timedelta(days=back)]) for back in range(1, window + 1)]
    logged = [n for n in before if n]
    if len(logged) >= max(3, (window + 1) // 2):  # enough days logged to know what usual is
        usual, today = median(logged), pee_times(pees_by_day[day])
        if today >= max(usual * cfg["pee_factor"], usual + cfg["pee_extra"]):
            found.append(("more_pees", _(
                "Peed %(n)d separate times today, usually about %(usual)d. Peeing much more often can be a urinary "
                "infection: worth a vet call if it continues.") % {"n": today, "usual": round(usual)}))
    return found


def _load(pet, since, until):
    breaks = list(PottyBreak.objects.filter(pet=pet, time__gte=since, time__lte=until).order_by("time"))
    pees_by_day = defaultdict(list)
    for b in breaks:
        if b.pee:
            pees_by_day[timezone.localdate(b.time)].append(b.time)
    return breaks, pees_by_day


def alerts(pet, now=None):
    """[(level, text)] for the pet page: the switched-on checks that are off right now."""
    now = now or timezone.now()
    cfg = settings_for(pet.household)
    today = timezone.localdate(now)
    breaks, pees_by_day = _load(pet, now - datetime.timedelta(days=MAX_USUAL_DAYS + 1), now)
    recent = [b for b in breaks if b.time >= now - datetime.timedelta(hours=24)]
    levels = {key: level for key, _label, level, _what in CHECKS}
    return [(levels[key], text) for key, text in _trips(recent, pees_by_day, today, now, cfg) if key not in cfg["off"]]


def history(pet, days=90, now=None):
    """{check key: [(date, text), newest first]}: when each check would have gone off over the past `days`, judged at
    the end of each day (today: now). Uses the current numbers, switched off or not."""
    now = now or timezone.now()
    cfg = settings_for(pet.household)
    today = timezone.localdate(now)
    breaks, pees_by_day = _load(pet, now - datetime.timedelta(days=days + MAX_USUAL_DAYS + 1), now)
    found = defaultdict(list)
    for back in range(days):
        day = today - datetime.timedelta(days=back)
        end = now if back == 0 else timezone.make_aware(
            datetime.datetime.combine(day + datetime.timedelta(days=1), datetime.time()))
        recent = [b for b in breaks if end - datetime.timedelta(hours=24) <= b.time < end or (back == 0 and b.time == end)]
        for key, text in _trips(recent, pees_by_day, day, end, cfg):
            found[key].append((day, text))
    return found
