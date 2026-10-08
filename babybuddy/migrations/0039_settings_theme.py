from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("babybuddy", "0038_settings_clock"),
    ]

    operations = [
        migrations.AddField(
            model_name="settings",
            name="theme",
            field=models.CharField(
                blank=True,
                choices=[("auto", "Automatic (follow the device)"), ("light", "Light"), ("dark", "Dark")],
                default="auto",
                max_length=5,
                verbose_name="Theme",
            ),
        ),
    ]
