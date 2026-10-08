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


def _minutes(default, description, help_text):
    return dbsettings.PositiveIntegerValue(default=default, description=description, help_text=help_text)


class PredictionSettings(dbsettings.Group):
    """Next Walk prediction rules. The defaults suit most dogs; see docs/housetrained/predictions.md."""

    max_hold_hours = dbsettings.PositiveIntegerValue(
        default=8, description=_("Longest a pet is expected to hold it (hours)"),
        help_text=_("Caps the age rule of thumb (age in months + 1 hour). Adult dogs: about 8."),
    )
    min_history_days = dbsettings.PositiveIntegerValue(
        default=3, description=_("Days of history before learning from it"),
        help_text=_("Before this, predictions use the age rule instead of the pet's own potty gaps."),
    )
    age_rule_until_days = dbsettings.PositiveIntegerValue(
        default=7, description=_("Days before the 3-day average replaces the age rule"),
        help_text=_("Fallback when there aren't enough similar gaps at this time of day."),
    )
    max_gap_hours = dbsettings.PositiveIntegerValue(
        default=12, description=_("Ignore gaps longer than (hours)"),
        help_text=_("Longer gaps are usually missed entries, not real holds."),
    )
    meal_window_hours = dbsettings.PositiveIntegerValue(
        default=3, description=_("A potty counts as after a meal within (hours)"),
        help_text=_("Used to learn how long after eating this pet goes."),
    )
    meal_delay_pee = _minutes(20, _("Pee after a meal, until learned (minutes)"),
                              _("Used until three meals were followed by a pee."))
    meal_delay_poop = _minutes(30, _("Poop after a meal, until learned (minutes)"),
                               _("Used until three meals were followed by a poop."))
    stale_after = _minutes(30, _("Drop a missed trigger after (minutes)"),
                           _("A meal or wake-up that passed this long ago without a potty gives way to the next estimate."))
    merge_slack = _minutes(30, _("Needs grouping buffer (minutes)"),
                           _("A pee or poop due this long after a walk would normally end is expected on that walk."))
    default_walk = _minutes(15, _("Walk length, until learned (minutes)"), _("Used until walks are logged."))
    min_night_hours = dbsettings.PositiveIntegerValue(
        default=4, description=_("Shortest night (hours)"),
        help_text=_("A day's longest potty-free stretch must be at least this long to count as the night."),
    )
    night_half_life_days = dbsettings.PositiveIntegerValue(
        default=3, description=_("How fast old nights fade (days)"),
        help_text=_("A night's weight in the bedtime and wake-up estimate halves every this many days."),
    )
    bedtime_slack = _minutes(45, _("Bedtime potty window (minutes)"),
                             _("A potty this long before the usual bedtime already counts as the bedtime one."))
    default_bedtime = dbsettings.TimeValue(default=datetime.time(23, 0), description=_("Bedtime, until learned"))
    default_wake = dbsettings.TimeValue(default=datetime.time(6, 30), description=_("Wake-up, until learned"))
