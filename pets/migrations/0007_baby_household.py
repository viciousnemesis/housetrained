"""Once linked Baby Buddy's children to households (PUP-71). Baby Buddy is gone (PUP-145); 0029 drops the table on
installs that have it."""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('pets', '0006_prediction_settings'),
    ]

    operations = []
