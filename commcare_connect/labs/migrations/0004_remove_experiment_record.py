# Generated manually
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("labs", "0003_change_organization_id_to_charfield"),
    ]

    operations = [
        migrations.DeleteModel(
            name="ExperimentRecord",
        ),
    ]
