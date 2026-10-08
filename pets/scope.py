"""Which household Baby Buddy's data is limited to during a request (PUP-71).

Baby Buddy's tables (children and everything logged for them) use household-scoped managers: during a web or
API request they only return the signed-in user's household's babies. Outside requests (migrations, management
commands, tests that don't go through a request) nothing is filtered.
"""
from contextvars import ContextVar

from django.db import models

_request = ContextVar("housetrained_request", default=None)
UNSCOPED = object()


def current_scope():
    """UNSCOPED outside requests, None for anonymous requests (nothing visible), else the household."""
    from django.conf import settings

    request = _request.get()
    if request is None or not settings.BABY_HOUSEHOLDS:
        return UNSCOPED
    if request.path_info.startswith("/admin/") and getattr(request.user, "is_superuser", False):
        return UNSCOPED  # site admins see everything in the database admin
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return None
    cached = getattr(request, "_housetrained_household", None)
    if cached is None or cached[0] != user.pk:
        from .households import current_household

        cached = (user.pk, current_household(user))
        request._housetrained_household = cached  # DRF signs API users in later, so this is looked up lazily
    return cached[1]


class HouseholdScopeMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = _request.set(request)
        try:
            return self.get_response(request)
        finally:
            _request.reset(token)


class ScopedManager(models.Manager):
    """Default manager for Baby Buddy tables: filter by the request's household through `household_path`."""

    def __init__(self, household_path=None, extra=None):
        # Django makes reverse-relation managers (child.note, ...) from this class without arguments; those are
        # already limited to one child, so they don't filter.
        super().__init__()
        self.household_path, self.extra = household_path, extra

    def get_queryset(self):
        queryset = super().get_queryset()
        household = current_scope()
        if household is UNSCOPED or self.household_path is None:
            return queryset
        if household is None:
            return queryset.none()
        condition = models.Q(**{self.household_path: household})
        if self.extra:
            condition |= self.extra(household)
        return queryset.filter(condition)


def rescope(queryset):
    """Limit a queryset that was built before the request (at import time) to the request's household."""
    if queryset is None or not hasattr(queryset, "model"):
        return queryset
    manager = queryset.model._default_manager
    if not isinstance(manager, ScopedManager) or current_scope() is UNSCOPED:
        return queryset
    return queryset & manager.all()


def install():
    """Baby Buddy builds some querysets once, at import time: form choices, filter choices, class-level view and
    API querysets. Re-limit them whenever they're used in a request."""
    from django.forms.models import ModelChoiceField
    from django.views.generic.detail import SingleObjectMixin
    from django.views.generic.list import MultipleObjectMixin
    from rest_framework.generics import GenericAPIView
    from rest_framework.relations import RelatedField

    if getattr(ModelChoiceField, "_housetrained_scoped", False):
        return
    getter, setter = ModelChoiceField.queryset.fget, ModelChoiceField.queryset.fset
    ModelChoiceField.queryset = property(getter, lambda self, qs: setter(self, rescope(qs)))
    ModelChoiceField._housetrained_scoped = True

    for cls in (SingleObjectMixin, MultipleObjectMixin, GenericAPIView, RelatedField):
        original = cls.get_queryset

        def get_queryset(self, *args, _original=original, **kwargs):
            return rescope(_original(self, *args, **kwargs))

        cls.get_queryset = get_queryset
