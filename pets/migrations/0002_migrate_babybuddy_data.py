"""Once copied existing Baby Buddy data into the pet tables (PUP-61). Baby Buddy's tables are gone (PUP-145), so on
a new install there is nothing to copy; installs that ran it keep what it copied."""
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("pets", "0001_initial"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = []
