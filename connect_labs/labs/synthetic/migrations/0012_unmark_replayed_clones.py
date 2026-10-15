"""Stop counting as generated any clone that may hold real cases' numbers.

From 2026-10-14 to 2026-10-15 the case model replayed each real case's numbers (its
weighings, birth weight, visit days) into a clone, and the clone was marked
generated, so callers kept from real visit data could read it. Such a clone cannot
be told from its row, so every clone (``cloned_from`` set) written since the replay
first ran loses the mark; regenerating it with the current code marks it again.
Others regenerated in that window lose it too, which only costs a regeneration.

Irreversible: the mark is not restored.
"""

import datetime as dt

from django.db import migrations

REPLAY_FIRST_RAN = dt.datetime(2026, 10, 14, tzinfo=dt.UTC)


def forward(apps, schema_editor):
    SyntheticOpportunity = apps.get_model("synthetic", "SyntheticOpportunity")
    SyntheticOpportunity.objects.filter(
        cloned_from_opportunity_id__isnull=False,
        generated_folder_id__isnull=False,
        updated_at__gte=REPLAY_FIRST_RAN,
    ).update(generated_folder_id=None)


class Migration(migrations.Migration):
    dependencies = [("synthetic", "0011_flag_labs_only_orgs")]

    operations = [migrations.RunPython(forward, migrations.RunPython.noop, hints={"run_on_secondary": False})]
