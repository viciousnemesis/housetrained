# -*- coding: utf-8 -*-
from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import PasswordChangeForm, UserCreationForm
from django.contrib.auth.models import Group
from django.utils.translation import gettext_lazy as _

from .models import Settings
from .widgets import DateTimeInput


class BabyBuddyUserForm(forms.ModelForm):
    is_read_only = forms.BooleanField(
        required=False,
        label=_("Read only"),
        help_text=_("Restricts user to viewing data only."),
    )
    is_caregiver = forms.BooleanField(
        required=False,
        label=_("Caregiver"),
        help_text=_(
            "Allows adding and editing care entries (feedings, diaper changes, "
            "sleep, timers, medication, temperature, weight, notes and tummy "
            "time) for every child. Cannot delete entries or reach pumping, "
            "height, BMI, head circumference, user management or settings."
        ),
    )
    access_expires = forms.DateTimeField(
        required=False,
        label=_("Access expires"),
        help_text=_(
            "Optional. After this time the user can no longer sign in or use "
            "the API."
        ),
        widget=DateTimeInput(),
    )

    class Meta:
        model = get_user_model()
        fields = [
            "username",
            "first_name",
            "last_name",
            "email",
            "is_staff",
            "is_read_only",
            "is_caregiver",
            "is_active",
            "access_expires",
        ]

    def __init__(self, *args, **kwargs):
        user = kwargs["instance"]
        if user:
            kwargs["initial"].update(
                {
                    "is_read_only": user.groups.filter(
                        name=settings.BABY_BUDDY["READ_ONLY_GROUP_NAME"]
                    ).exists(),
                    "is_caregiver": user.groups.filter(
                        name=settings.BABY_BUDDY["CAREGIVER_GROUP_NAME"]
                    ).exists(),
                    "access_expires": user.settings.access_expires,
                }
            )
        super(BabyBuddyUserForm, self).__init__(*args, **kwargs)
        if settings.BABY_HOUSEHOLDS:
            # Housetrained: household roles (Setup > Members) decide what people can do; being a site admin is an
            # explicit choice instead of the default.
            self.fields.pop("is_read_only")
            self.fields.pop("is_caregiver")
            self.fields["is_staff"].label = _("Site admin")
            self.fields["is_staff"].help_text = _(
                "Can create accounts, add people to households and change site settings. Leave off for "
                "everyone else; what they can do in their household is set under Setup > Members."
            )

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("is_read_only") and cleaned_data.get("is_caregiver"):
            self.add_error(
                "is_caregiver",
                _("A user cannot be both read only and caregiver."),
            )
        if cleaned_data.get("is_staff") and cleaned_data.get("is_caregiver"):
            self.add_error(
                "is_caregiver",
                _("A user cannot be both staff and caregiver."),
            )
        return cleaned_data

    def save(self, commit=True):
        user = super(BabyBuddyUserForm, self).save(commit=False)
        is_read_only = self.cleaned_data.get("is_read_only", False)
        is_caregiver = self.cleaned_data.get("is_caregiver", False)
        if settings.BABY_HOUSEHOLDS:
            user.is_superuser = bool(self.cleaned_data.get("is_staff"))
        elif is_read_only or is_caregiver:
            user.is_superuser = False
        else:
            user.is_superuser = True
        if commit:
            user.save()
            user.settings.access_expires = self.cleaned_data.get("access_expires")
            user.settings.save(update_fields=["access_expires"])
        readonly_group = Group.objects.get(
            name=settings.BABY_BUDDY["READ_ONLY_GROUP_NAME"]
        )
        caregiver_group = Group.objects.get(
            name=settings.BABY_BUDDY["CAREGIVER_GROUP_NAME"]
        )
        if is_read_only:
            user.groups.add(readonly_group.id)
        else:
            user.groups.remove(readonly_group.id)
        if is_caregiver:
            user.groups.add(caregiver_group.id)
        else:
            user.groups.remove(caregiver_group.id)
        return user


class UserAddForm(BabyBuddyUserForm, UserCreationForm):
    pass


class UserUpdateForm(BabyBuddyUserForm):
    pass


class UserForm(forms.ModelForm):
    class Meta:
        model = get_user_model()
        fields = ["first_name", "last_name", "email"]


class UserPasswordForm(PasswordChangeForm):
    class Meta:
        fields = ["old_password", "new_password1", "new_password2"]


class UserSettingsForm(forms.ModelForm):
    class Meta:
        model = Settings
        fields = [
            "dashboard_refresh_rate",
            "dashboard_hide_empty",
            "dashboard_hide_age",
            "language",
            "timezone",
            "pagination_count",
            "theme",
            "clock",
            "show_help",
        ]

    def __init__(self, *args, **kwargs):
        from . import time_zones

        super().__init__(*args, **kwargs)
        # A dropdown by UTC offset, west to east (PUP-141).
        self.fields["timezone"].choices = time_zones.choices(self.instance.timezone)
        self.fields["timezone"].widget = time_zones.TimeZoneSelect(choices=self.fields["timezone"].choices)
