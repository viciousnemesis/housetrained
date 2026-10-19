# -*- coding: utf-8 -*-
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import PasswordChangeForm, UserCreationForm
from django.utils.translation import gettext_lazy as _

from .models import Settings
from .widgets import DateTimeInput


class AccountForm(forms.ModelForm):
    """A user account. What someone can do in their household is set under Setup > Members; being a site admin is
    an explicit choice."""

    access_expires = forms.DateTimeField(
        required=False,
        label=_("Access expires"),
        help_text=_("Optional. After this time the user can no longer sign in or use the API."),
        widget=DateTimeInput(),
    )

    class Meta:
        model = get_user_model()
        fields = ["username", "first_name", "last_name", "email", "is_staff", "is_active", "access_expires"]
        labels = {"is_staff": _("Site admin")}
        help_texts = {"is_staff": _(
            "Can create accounts, add people to households and change site settings. Leave off for everyone "
            "else; what they can do in their household is set under Setup > Members.")}

    def __init__(self, *args, **kwargs):
        user = kwargs.get("instance")
        if user:
            kwargs.setdefault("initial", {})["access_expires"] = user.settings.access_expires
        super().__init__(*args, **kwargs)

    def save(self, commit=True):
        user = super().save(commit=False)
        user.is_superuser = bool(self.cleaned_data.get("is_staff"))
        if commit:
            user.save()
            user.settings.access_expires = self.cleaned_data.get("access_expires")
            user.settings.save(update_fields=["access_expires"])
        return user


class UserAddForm(AccountForm, UserCreationForm):
    pass


class UserUpdateForm(AccountForm):
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
