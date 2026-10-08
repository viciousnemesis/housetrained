"""Browser push notifications (PUP-58): the server's keys, and sending a message to a person's devices."""
import base64
import json
import logging

from django.conf import settings
from django.utils import timezone

from .models import PushKey, PushSubscription

log = logging.getLogger(__name__)


def _keys():
    key = PushKey.objects.order_by("pk").first()
    if key:
        return key
    from cryptography.hazmat.primitives import serialization
    from py_vapid import Vapid

    vapid = Vapid()
    vapid.generate_keys()
    public = vapid.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return PushKey.objects.create(
        public_key=base64.urlsafe_b64encode(public).decode().rstrip("="),
        private_key=vapid.private_pem().decode(),
    )


def public_key():
    """What browsers need to subscribe (base64url, uncompressed P-256 point)."""
    return _keys().public_key


def send(user, title, body, url="/", tag=None):
    """Send to every device the user turned notifications on for. Returns how many got it."""
    from py_vapid import Vapid
    from pywebpush import WebPushException, webpush

    keys = _keys()
    vapid = Vapid.from_pem(keys.private_key.encode())
    payload = json.dumps({"title": title, "body": body, "url": url, "tag": tag or url})
    delivered = 0
    for sub in PushSubscription.objects.filter(user=user):
        try:
            webpush(
                subscription_info={"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}},
                data=payload,
                vapid_private_key=vapid,
                vapid_claims={"sub": settings.HOUSETRAINED_PUSH_CONTACT},
                ttl=3600,
            )
        except WebPushException as error:
            status = getattr(error.response, "status_code", None)
            if status in (404, 410):
                sub.delete()  # the browser dropped this subscription
            else:
                log.warning("Push to %s failed: %s", sub, error)
            continue
        except Exception:  # a bad endpoint must not stop the other devices
            log.exception("Push to %s failed", sub)
            continue
        delivered += 1
        PushSubscription.objects.filter(pk=sub.pk).update(last_used=timezone.now())
    return delivered
