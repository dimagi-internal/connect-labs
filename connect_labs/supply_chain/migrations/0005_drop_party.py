"""Drop the second organisation registry, now its rows have moved."""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [("supply_chain", "0004_carry_parties_across")]

    operations = [
        migrations.RemoveField(
            model_name="supplypoint",
            name="managed_by_party",
        ),
        migrations.RemoveField(
            model_name="receipt",
            name="recorded_by_party",
        ),
        migrations.RemoveField(
            model_name="supplypoint",
            name="recorded_by_party",
        ),
        migrations.RemoveField(
            model_name="distribution",
            name="recorded_by_party",
        ),
        migrations.RemoveField(
            model_name="shipment",
            name="recorded_by_party",
        ),
        migrations.RemoveField(
            model_name="movement",
            name="recorded_by_party",
        ),
        migrations.RemoveField(
            model_name="contract",
            name="buyer_party",
        ),
        migrations.RemoveField(
            model_name="document",
            name="recorded_by_party",
        ),
        migrations.RemoveField(
            model_name="supplier",
            name="party",
        ),
        migrations.RemoveField(
            model_name="invoice",
            name="recorded_by_party",
        ),
        migrations.RemoveField(
            model_name="contract",
            name="recorded_by_party",
        ),
        migrations.RemoveField(
            model_name="stockcount",
            name="recorded_by_party",
        ),
        migrations.RemoveField(
            model_name="payment",
            name="recorded_by_party",
        ),
        migrations.DeleteModel(
            name="Party",
        ),
    ]
