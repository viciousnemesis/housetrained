from os import getenv
from time import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.middleware import RemoteUserMiddleware
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import access_expired


def activate_user_timezone(user):
    """The user's own time zone, or the server's: never one left on this thread by an earlier request."""
    zone = user.settings.timezone if hasattr(user, "settings") else ""
    try:
        timezone.activate(zone) if zone else timezone.deactivate()
    except ValueError:
        timezone.deactivate()


class UserTimezoneMiddleware:
    """
    Sets the timezone based on a user specific setting. This middleware must run after
    `django.contrib.auth.middleware.AuthenticationMiddleware` because it uses the
    request.user object. API token sign-ins happen later, in the view, which sets it again.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        activate_user_timezone(request.user)
        try:
            return self.get_response(request)
        finally:
            timezone.deactivate()


class RollingSessionMiddleware:
    """
    Periodically resets the session expiry for existing sessions.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.session.keys():
            session_refresh = request.session.get("session_refresh")
            if session_refresh:
                try:
                    delta = int(time()) - session_refresh
                except (ValueError, TypeError):
                    delta = settings.ROLLING_SESSION_REFRESH + 1
                if delta > settings.ROLLING_SESSION_REFRESH:
                    request.session["session_refresh"] = int(time())
                    request.session.set_expiry(settings.SESSION_COOKIE_AGE)
            else:
                request.session["session_refresh"] = int(time())
        return self.get_response(request)


class CustomRemoteUser(RemoteUserMiddleware):
    """
    Middleware used for remote authentication when `REVERSE_PROXY_AUTH` is True.
    """

    header = getenv("PROXY_HEADER", "HTTP_REMOTE_USER")

    def process_request(self, request):
        # Exclude API paths using token authentication.
        if request.path.startswith("/api/"):
            return None
        return super().process_request(request)


class AccessExpiry:
    """
    Signs out a user whose access has expired. This runs as a view hook so
    that it also applies to users authenticated by `CustomRemoteUser`, which
    is added after this middleware. API token authentication is checked by
    `api.authentication.TokenAuthentication`.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and access_expired(user):
            logout(request)
            messages.error(request, _("Your access has expired."))
        return None
