from django.apps import AppConfig
from django.db.models.signals import m2m_changed, post_save


def end_retry(sender, instance, raw=False, created=False, **kwargs):
    """Any new potty break ends a "try again" countdown. An accident remembers when the schedule said to go out."""
    if raw:
        return
    if created and instance.pet.retry_at:
        type(instance.pet).objects.filter(pk=instance.pet_id).update(retry_at=None)
    if instance.accident or instance.due_at:
        from .schedule import remember_due

        remember_due(instance)


def walk_changed(sender, instance, raw=False, **kwargs):
    """A walk saved, or its pets changed: re-check which potty breaks happened on it."""
    if raw or kwargs.get("reverse") or kwargs.get("action", "post_add") not in ("post_add", "post_remove", "post_clear"):
        return
    from .forms import relink_walk

    relink_walk(instance)


class PetsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "pets"
    verbose_name = "Pets"

    def ready(self):
        post_save.connect(end_retry, sender="pets.PottyBreak", dispatch_uid="pets.end_retry")
        post_save.connect(walk_changed, sender="pets.Walk", dispatch_uid="pets.walk_changed")
        from .models import Walk

        m2m_changed.connect(walk_changed, sender=Walk.pets.through, dispatch_uid="pets.walk_pets_changed")
