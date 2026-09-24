"""Item.components_per: which unit a kit's components describe.

Existing kits get the level `records.infer_components_per` reads off them --
pack when a component quantity is a whole multiple of a pack size greater
than one (a test kit's 50 tablets for 50 tests), base otherwise (every co-pack
written before this). Items with no components stay at the default, base.
"""

from django.db import migrations, models


def infer_levels(apps, schema_editor):
    from connect_labs.supply_chain.records import infer_components_per

    Item = apps.get_model("supply_chain", "Item")
    for item in Item.objects.exclude(components=[]).select_related("commodity"):
        level = infer_components_per(
            item.components,
            pack_unit=item.pack_unit or (item.commodity.pack_unit if item.commodity_id else ""),
            base_per_pack=item.base_per_pack,
        )
        if level != item.components_per:
            Item.objects.filter(pk=item.pk).update(components_per=level)


class Migration(migrations.Migration):

    dependencies = [
        ("supply_chain", "0016_approval_rests_on_document"),
    ]

    operations = [
        migrations.AddField(
            model_name="item",
            name="components_per",
            field=models.CharField(
                choices=[("base", "base"), ("pack", "pack")], default="base", max_length=8
            ),
        ),
        # `run_on_secondary` is required by connect_labs.multidb: False, as in 0004.
        migrations.RunPython(infer_levels, migrations.RunPython.noop, hints={"run_on_secondary": False}),
    ]
