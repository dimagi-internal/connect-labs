"""The single delivery point leaves Tender; 0028 carried it into delivery_points.

Its own migration, not 0028's last step, because Postgres refuses an ALTER in
the same transaction as the rows 0028 rewrites.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("supply_chain", "0028_tender_delivery"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="tender",
            name="delivery_point",
        ),
    ]
