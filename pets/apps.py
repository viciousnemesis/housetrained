from django.apps import AppConfig
from django.db.models.signals import post_save


def link_new_baby(sender, instance, created, raw=False, **kwargs):
    """A baby added by someone joins their household (PUP-71)."""
    from .models import BabyHousehold
    from .scope import UNSCOPED, current_scope

    household = current_scope()
    if created and not raw and household not in (None, UNSCOPED):
        BabyHousehold.objects.get_or_create(child=instance, defaults={"household": household})


class PetsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "pets"
    verbose_name = "Pets"

    def ready(self):
        from .scope import install

        post_save.connect(link_new_baby, sender="core.Child", dispatch_uid="pets.link_new_baby")
        install()
