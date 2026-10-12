"""Drop `SupplyWorkflowView`: pinned supply tabs live in Settings (`supply` namespace).

scope_config/0002 copied every row into Settings and left the rows unread for a
release; nothing reads them now. It runs before this one (the dependency below),
so a fresh database still carries any pins through. Reversing recreates the empty
table, not the rows -- the tabs themselves are in Settings either way.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("supply_chain", "0052_forecast_cases"),
        ("scope_config", "0002_supply_pins"),
    ]

    operations = [migrations.DeleteModel(name="SupplyWorkflowView")]
