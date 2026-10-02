"""A payment is always against an order (its invoice may come later)."""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("supply_chain", "0042_payment_contract_backfill")]

    operations = [
        migrations.AlterField(
            model_name="payment",
            name="contract",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, related_name="payments", to="supply_chain.contract"
            ),
        ),
    ]
