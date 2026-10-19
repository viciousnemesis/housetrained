"""Once added a table that 0029 drops again (PUP-71, PUP-145). Nothing to do now."""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('pets', '0006_prediction_settings'),
    ]

    operations = []
