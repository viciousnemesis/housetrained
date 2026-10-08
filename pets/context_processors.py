from django.conf import settings


def baby_mode_on():
    """On when the BABY_MODE environment variable or the site setting says so."""
    from .models import Pet

    return settings.BABY_MODE or Pet.site_settings.baby_mode


def baby_mode(request):
    """Baby Buddy's own menus and pages show only when baby mode is switched on (PUP-49). Also whether the user may
    log and change things in their household (read-only members may not)."""
    from django.utils.functional import SimpleLazyObject

    from .households import can_edit, current_household

    def editable():
        user = request.user
        return user.is_authenticated and can_edit(user, current_household(user))

    def help_on():
        user = request.user
        if not user.is_authenticated:
            return True
        settings_ = getattr(user, "settings", None)
        return settings_ is None or settings_.show_help

    from . import __version__

    return {"housetrained_version": __version__, "housetrained_source_url": settings.HOUSETRAINED_SOURCE_URL,
            "baby_mode": SimpleLazyObject(baby_mode_on), "help_bubbles": SimpleLazyObject(help_on), "pets_can_edit": SimpleLazyObject(editable)}
