from django.apps import AppConfig
from django.db.models.signals import m2m_changed, post_save


def link_new_baby(sender, instance, created, raw=False, **kwargs):
    """A baby added by someone joins their household (PUP-71)."""
    from .models import BabyHousehold
    from .scope import UNSCOPED, current_scope

    household = current_scope()
    if created and not raw and household not in (None, UNSCOPED):
        BabyHousehold.objects.get_or_create(child=instance, defaults={"household": household})


def end_retry(sender, instance, raw=False, created=False, **kwargs):
    """Any new potty break ends a "try again" countdown. An accident remembers when the schedule said to go out."""
    if raw:
        return
    if created and instance.pet.retry_at:
        type(instance.pet).objects.filter(pk=instance.pet_id).update(retry_at=None)
    if instance.location == "inside" or instance.due_at:
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
        from .scope import install

        post_save.connect(link_new_baby, sender="core.Child", dispatch_uid="pets.link_new_baby")
        post_save.connect(end_retry, sender="pets.PottyBreak", dispatch_uid="pets.end_retry")
        post_save.connect(walk_changed, sender="pets.Walk", dispatch_uid="pets.walk_changed")
        from .models import Walk

        m2m_changed.connect(walk_changed, sender=Walk.pets.through, dispatch_uid="pets.walk_pets_changed")
        install()
