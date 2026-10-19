"""Baby Buddy's dashboard options and the Language setting are gone (PUP-145)."""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("babybuddy", "0039_settings_theme"),
    ]

    operations = [
        migrations.RemoveField(model_name="settings", name="dashboard_hide_age"),
        migrations.RemoveField(model_name="settings", name="dashboard_hide_empty"),
        migrations.RemoveField(model_name="settings", name="language"),
    ]
