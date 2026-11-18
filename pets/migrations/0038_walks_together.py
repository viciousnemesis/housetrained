from django.db import migrations, models


def dogs_walk_together(apps, schema_editor):
    apps.get_model("pets", "Pet").objects.filter(type="dog").update(walks_together=True)


class Migration(migrations.Migration):
    dependencies = [
        ("pets", "0037_puke"),
    ]

    operations = [
        migrations.AddField(
            model_name="pet",
            name="walks_together",
            field=models.BooleanField(
                default=False,
                help_text="Logging this pet's walk lists the other pets that walk together. Off: never any other pets.",
                verbose_name="walks with other pets",
            ),
        ),
        migrations.RunPython(dogs_walk_together, migrations.RunPython.noop),
    ]
