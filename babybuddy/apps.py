# -*- coding: utf-8 -*-
from django.apps import AppConfig

from babybuddy import VERSION


class BabyBuddyConfig(AppConfig):
    """Accounts, settings and the site frame (the package keeps Baby Buddy's name)."""

    name = "babybuddy"
    verbose_name = "Housetrained"
    version = VERSION
    version_string = VERSION
