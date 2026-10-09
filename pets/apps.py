from django.apps import AppConfig
from django.db.models.signals import post_save


def link_new_baby(sender, instance, created, raw=False, **kwargs):
    """A baby added by someone joins their household (PUP-71)."""
    from .models import BabyHousehold
    from .scope import UNSCOPED, current_scope

    household = current_scope()
    if created and not raw and household not in (None, UNSCOPED):
        BabyHousehold.objects.get_or_create(child=instance, defaults={"household": household})


def end_retry(sender, instance, raw=False, created=False, **kwargs):
    """Any new potty break ends a "try again" countdown."""
    if not raw and created and instance.pet.retry_at:
        type(instance.pet).objects.filter(pk=instance.pet_id).update(retry_at=None)


class PetsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "pets"
    verbose_name = "Pets"

    def ready(self):
        from .scope import install

        post_save.connect(link_new_baby, sender="core.Child", dispatch_uid="pets.link_new_baby")
        post_save.connect(end_retry, sender="pets.PottyBreak", dispatch_uid="pets.end_retry")
        install()
