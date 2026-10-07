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
