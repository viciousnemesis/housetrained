"""The household a request works in, and what each member may do there.

The UI is single-household for now: a user's first household, created on first use. Access follows the household
role, not Django model permissions: owners and members log entries and change setup, read-only members only look,
and only owners change household settings and members. Superusers count as owners everywhere.
"""
from .models import Household, Membership


def current_household(user):
    membership = user.memberships.select_related("household").order_by("pk").first()
    if membership:
        return membership.household
    settings_ = getattr(user, "settings", None)
    household = Household.objects.create(name="Home", time_zone=getattr(settings_, "timezone", "") or "")
    Membership.objects.create(household=household, user=user, role=Membership.OWNER)
    return household


def adopt_time_zone(household, user):
    """Someone joining a household starts in its time zone, unless they already picked their own."""
    from django.utils import timezone

    settings_ = getattr(user, "settings", None)
    if settings_ and household.time_zone and settings_.timezone == timezone.get_default_timezone_name():
        settings_.timezone = household.time_zone
        settings_.save(update_fields=["timezone"])


def role(user, household):
    if user.is_superuser:
        return Membership.OWNER
    membership = household.memberships.filter(user=user).first()
    return membership.role if membership else None


def can_edit(user, household):
    return role(user, household) in (Membership.OWNER, Membership.MEMBER)


def is_owner(user, household):
    return role(user, household) == Membership.OWNER


def add_member(household, user, role_name):
    """Add a user to a household. A household made automatically on their first visit goes away if nothing was
    ever put in it, so they land in the shared one."""
    for membership in user.memberships.exclude(household=household).select_related("household"):
        other = membership.household
        empty = not any(related.exists() for related in (
            other.pets, other.containers, other.products, other.walks, other.notes))
        if other.memberships.count() == 1 and empty:
            other.delete()
    Membership.objects.update_or_create(household=household, user=user, defaults={"role": role_name})
    adopt_time_zone(household, user)
