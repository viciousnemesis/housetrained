"""Household roles decide what people may do with Baby Buddy's baby data too (PUP-71).

Read-only members may view; owners and members may also add, change and delete. This adds to Django's own
permissions (superusers and Baby Buddy's groups keep working).
"""
from django.contrib.auth.backends import BaseBackend

ACTIONS = {"view", "add", "change", "delete"}


class HouseholdRoleBackend(BaseBackend):
    def has_perm(self, user_obj, perm, obj=None):
        from django.conf import settings

        if not settings.BABY_HOUSEHOLDS or not user_obj.is_active or not user_obj.is_authenticated:
            return False
        app_label, _, codename = perm.partition(".")
        action = codename.split("_", 1)[0]
        if app_label != "core" or action not in ACTIONS:
            return False
        from .households import current_household, role
        from .models import Membership

        current = role(user_obj, current_household(user_obj))
        if current == Membership.VIEWER:
            return action == "view"
        return current in (Membership.OWNER, Membership.MEMBER)
