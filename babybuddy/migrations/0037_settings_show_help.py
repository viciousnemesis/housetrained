from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("babybuddy", "0036_settings_access_expires"),
    ]

    operations = [
        migrations.AddField(
            model_name="settings",
            name="show_help",
            field=models.BooleanField(
                default=True,
                help_text="Small (i) buttons next to fields and buttons that explain them.",
                verbose_name="Show help bubbles",
            ),
        ),
    ]
