"""Undo for quick logs (PUP-151). The banner after a log offers Undo for 10 seconds. What it would take back is kept
in the person's own session, so only they can undo it, and only the entries just made."""
import time
import uuid

from django.apps import apps
from django.contrib import messages

SECONDS = 10  # how long the Undo button shows
GRACE = 5  # a tap at the last moment still counts on a slow connection


def offer(request, text, entries, back, level=messages.SUCCESS, banner=True):
    """Show `text` with an Undo button that takes `entries` back, then returns to `back`. banner=False when the page
    shows it itself (logged without leaving the page). Returns the undo token."""
    token = uuid.uuid4().hex[:12]
    request.session["undo"] = {"token": token, "at": time.time(), "back": back,
                               "items": [[e._meta.label, e.pk] for e in entries]}
    if banner:
        messages.add_message(request, level, text, extra_tags=f"undo-{token}")
    return token


def take(request, token, household):
    """Delete what the banner `token` offered to undo, if it's still in time. Returns where to go back to, or None."""
    data = request.session.get("undo")
    if not data or data.get("token") != token or time.time() - data["at"] > SECONDS + GRACE:
        return None
    del request.session["undo"]
    from .views import entry_household

    for label, pk in data["items"]:
        model = apps.get_model(label)
        entry = model._base_manager.filter(pk=pk).first()  # _base_manager: "nothing" breaks are hidden by default
        if entry is None or entry_household(entry) != household:
            continue
        if label == "pets.PottyBreak" and not entry.pee and not entry.poop:
            type(entry.pet).objects.filter(pk=entry.pet_id).update(retry_at=None)  # no "try again" for it either
        entry.delete()
    return data["back"]
