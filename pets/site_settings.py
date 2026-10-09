import datetime

from django.forms.fields import BooleanField
from django.forms.widgets import CheckboxInput
from django.utils.translation import gettext_lazy as _

import dbsettings


class BabyModeValue(dbsettings.BooleanValue):
    field = BooleanField


class PlatformSettings(dbsettings.Group):
    baby_mode = BabyModeValue(
        required=False,
        default=False,
        description=_("Baby mode"),
        help_text=_(
            "Show Baby Buddy's baby tracking (feedings, diaper changes, tummy time, pumping and more) alongside the "
            "pets. The BABY_MODE environment variable turns it on regardless."
        ),
        widget=CheckboxInput,
    )
    push_notifications = BabyModeValue(
        required=False,
        default=False,
        description=_("Allow push notifications"),
        help_text=_(
            "Off: nothing ever leaves this server. On: each person can turn on reminders for their own devices "
            "(user menu > Notifications). Browsers deliver them through their maker's push service (Apple, Google "
            "or Mozilla): the messages are encrypted, but that service sees that one was sent to a device, and when."
        ),
        widget=CheckboxInput,
    )


class _Blankable:
    """A setting that may be left blank, meaning "use the default" (see insights._prediction_settings)."""

    def get_db_prep_save(self, value, *args, **kwargs):
        return "" if value in (None, "") else super().get_db_prep_save(value, *args, **kwargs)

    def to_python(self, value):
        return None if value in (None, "") else super().to_python(value)

    def to_editor(self, value):
        return "" if value in (None, "") else super().to_editor(value)


class OptionalNumber(_Blankable, dbsettings.PositiveIntegerValue):
    pass


class OptionalTime(_Blankable, dbsettings.TimeValue):
    pass


def _number(default, description, help_text):
    return OptionalNumber(default=default, description=description, help_text=help_text, required=False)


class PredictionSettings(dbsettings.Group):
    """Next Walk prediction rules. The defaults suit most dogs; see docs/housetrained/predictions.md."""

    max_hold_hours = _number(8, _("Longest hold"), _(
        "Hours. The most a pet is expected to hold it. Caps the age rule of thumb (age in months + 1 hour). "
        "Adult dogs: about 8."))
    min_history_days = _number(3, _("History needed to learn"), _(
        "Days of logs before predictions use the pet's own potty gaps instead of the age rule."))
    age_rule_until_days = _number(7, _("Age rule period"), _(
        "Days of logs during which the age rule is the fallback. After that, the average gap over the past "
        "3 days is used instead."))
    max_gap_hours = _number(12, _("Longest real gap"), _(
        "Hours. Longer gaps between potty breaks are ignored: they're usually missed entries, not real holds."))
    meal_window_hours = _number(3, _("After-meal window"), _(
        "Hours. A potty break this soon after a meal counts as caused by it, to learn how long after eating "
        "this pet goes."))
    meal_delay_pee = _number(20, _("Pee after a meal"), _(
        "Minutes from a meal to the next pee, used until three meals were followed by a pee."))
    meal_delay_poop = _number(30, _("Poop after a meal"), _(
        "Minutes from a meal to the next poop, used until three meals were followed by a poop."))
    retry_after = _number(15, _("Try again after nothing"), _(
        "Minutes. Out on a walk (or Nothing on the Potty panel) and nothing happened: the next walk is due this "
        "long after getting back."))
    merge_slack = _number(30, _("Needs grouping buffer"), _(
        "Minutes. A pee or poop due this long after a walk would normally end is expected on that walk."))
    learn_from_accidents = BabyModeValue(
        required=False, default=True, widget=CheckboxInput,
        description=_("Learn from accidents"),
        help_text=_("When at least two recent accidents came before the predicted walk, predict that much earlier."),
    )
    accident_max_shift = _number(90, _("Largest accident adjustment"), _(
        "Minutes. The most Next Walk is moved earlier because of accidents."))
    default_walk = _number(15, _("Usual walk length"), _(
        "Minutes a walk takes, used until walks are logged."))
    min_night_hours = _number(4, _("Shortest night"), _(
        "Hours. A day's longest potty-free stretch must be at least this long to count as the night."))
    night_half_life_days = _number(3, _("Night fade"), _(
        "Days. Every this many days, an older night counts half as much in the bedtime and wake-up estimate, "
        "so a changing routine is picked up."))
    bedtime_slack = _number(45, _("Bedtime potty window"), _(
        "Minutes. A potty break this long before the usual bedtime already counts as the bedtime one."))
    default_bedtime = OptionalTime(default=datetime.time(23, 0), description=_("Starting bedtime"),
                                           help_text=_("Used until two nights are logged."), required=False)
    default_wake = OptionalTime(default=datetime.time(6, 30), description=_("Starting wake-up"),
                                        help_text=_("Used until two nights are logged."), required=False)
