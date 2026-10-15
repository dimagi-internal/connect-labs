# THROWAWAY: proves CI's restore-older path (an added migration applied to a cached
# template). Never merged.
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("labs", "0030_drop_retired_oes_supply_tables")]
    operations = [migrations.RunPython(migrations.RunPython.noop, migrations.RunPython.noop)]
