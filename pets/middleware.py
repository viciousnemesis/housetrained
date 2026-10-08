from django.http import JsonResponse


class BabyApiGate:
    """Baby Buddy's own API (/api/children/, /api/changes/, ...) only exists in baby mode (PUP-31).
    Housetrained's API at /api/v1/ is always there."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path_info
        if path.startswith("/api/") and not path.startswith("/api/v1/"):
            from .context_processors import baby_mode_on

            if not baby_mode_on():
                return JsonResponse(
                    {"detail": "Baby Buddy's API is only available in baby mode. The pet API is at /api/v1/."},
                    status=404,
                )
        return self.get_response(request)
