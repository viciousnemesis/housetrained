from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("babybuddy", "0037_settings_show_help"),
    ]

    operations = [
        migrations.AddField(
            model_name="settings",
            name="clock",
            field=models.CharField(
                blank=True,
                choices=[("", "Automatic (from language)"), ("12", "12-hour"), ("24", "24-hour")],
                default="",
                max_length=2,
                verbose_name="Time format",
            ),
        ),
    ]
