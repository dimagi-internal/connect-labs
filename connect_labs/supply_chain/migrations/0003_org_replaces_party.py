"""Add the organisation links, without touching a row yet.

Schema only. The data copy is 0004 and the drop is 0005, in three
migrations rather than one because Postgres refuses to build an index on a
table whose rows were modified in the same transaction -- "cannot CREATE
INDEX ... because it has pending trigger events". Found by running this
against a populated database rather than an empty test one.
"""

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("labs", "0023_labsorg"),
        ("supply_chain", "0002_document_link_targets"),
    ]

    operations = [
        migrations.AddField(
            model_name="contract",
            name="buyer_org",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="supply_contracts",
                to="labs.labsorg",
            ),
        ),
        migrations.AddField(
            model_name="contract",
            name="recorded_by_org",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="labs.labsorg"
            ),
        ),
        migrations.AddField(
            model_name="distribution",
            name="recorded_by_org",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="labs.labsorg"
            ),
        ),
        migrations.AddField(
            model_name="document",
            name="recorded_by_org",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="labs.labsorg"
            ),
        ),
        migrations.AddField(
            model_name="invoice",
            name="recorded_by_org",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="labs.labsorg"
            ),
        ),
        migrations.AddField(
            model_name="movement",
            name="recorded_by_org",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="labs.labsorg"
            ),
        ),
        migrations.AddField(
            model_name="payment",
            name="recorded_by_org",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="labs.labsorg"
            ),
        ),
        migrations.AddField(
            model_name="receipt",
            name="recorded_by_org",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="labs.labsorg"
            ),
        ),
        migrations.AddField(
            model_name="shipment",
            name="recorded_by_org",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="labs.labsorg"
            ),
        ),
        migrations.AddField(
            model_name="stockcount",
            name="recorded_by_org",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="labs.labsorg"
            ),
        ),
        migrations.AddField(
            model_name="supplier",
            name="org",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="supplier_profiles",
                to="labs.labsorg",
            ),
        ),
        migrations.AddField(
            model_name="supplypoint",
            name="managed_by_org",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="supply_points",
                to="labs.labsorg",
            ),
        ),
        migrations.AddField(
            model_name="supplypoint",
            name="recorded_by_org",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="labs.labsorg"
            ),
        ),
    ]
