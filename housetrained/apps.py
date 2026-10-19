# -*- coding: utf-8 -*-
from django.apps import AppConfig

from housetrained import VERSION


class HousetrainedConfig(AppConfig):
    """Accounts, settings and the site frame."""

    name = "housetrained"
    verbose_name = "Housetrained"
    version = VERSION
    version_string = VERSION
