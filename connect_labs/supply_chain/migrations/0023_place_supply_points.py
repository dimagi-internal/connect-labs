"""Give every existing supply point a latitude and longitude.

Stand-ins from the managing organisation's head office (see
stock/services/placement.py); coordinates already on a point are kept and
marked recorded. Historical models are passed in, so a later change to either
table cannot break this on replay. Reversible as a no-op: the coordinates are
data, and the columns they sit in predate this migration.
"""

from django.db import migrations


def place(apps, schema_editor):
    from connect_labs.supply_chain.stock.services.placement import place_all

    place_all(
        SupplyPoint=apps.get_model("supply_chain", "SupplyPoint"),
        OrgProfile=apps.get_model("marketplace", "OrgProfile"),
    )


class Migration(migrations.Migration):
    dependencies = [
        ("supply_chain", "0022_supply_point_location_source"),
        ("marketplace", "0004_remove_orgprofile_flws_managed"),
    ]

    operations = [migrations.RunPython(place, migrations.RunPython.noop, hints={"run_on_secondary": False})]
