from django.apps import AppConfig
from django.db.models.signals import post_save


def link_new_baby(sender, instance, created, raw=False, **kwargs):
    """A baby added by someone joins their household (PUP-71)."""
    from .models import BabyHousehold
    from .scope import UNSCOPED, current_scope

    household = current_scope()
    if created and not raw and household not in (None, UNSCOPED):
        BabyHousehold.objects.get_or_create(child=instance, defaults={"household": household})


def record_expectation(sender, instance, raw=False, **kwargs):
    """An accident (inside) remembers what Next Walk expected just before it; others drop it."""
    if raw:
        return
    if instance.location == "inside" or instance.expected_at or instance.expected_reason:
        from .insights import snapshot_expectation

        snapshot_expectation(instance)


class PetsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "pets"
    verbose_name = "Pets"

    def ready(self):
        from .scope import install

        post_save.connect(link_new_baby, sender="core.Child", dispatch_uid="pets.link_new_baby")
        post_save.connect(record_expectation, sender="pets.PottyBreak", dispatch_uid="pets.record_expectation")
        install()
