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
