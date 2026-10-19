"""Once copied data from an earlier install into the pet tables (PUP-61). Nothing to do now (PUP-145)."""
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("pets", "0001_initial"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = []
