"""Record the organisation each membership was granted for.

Every membership that exists now is taken as granted for the row it sits on --
except those `synthetic/0011` deals with, which `synthetic/0010` wrote without
going through `OrgMembership.save()`. A membership a past `org_merge` moved
cannot be told apart here; that only matters for labs-only organisations, none
of which existed before `synthetic/0010`.
"""

from django.db import migrations, models
from django.db.models import F


def backfill(apps, schema_editor):
    OrgMembership = apps.get_model("marketplace", "OrgMembership")
    OrgMembership.objects.filter(granted_for_org_id__isnull=True).update(granted_for_org_id=F("org_id"))


class Migration(migrations.Migration):
    dependencies = [
        ("marketplace", "0006_org_operating_areas"),
    ]

    operations = [
        migrations.AddField(
            model_name="orgmembership",
            name="granted_for_org_id",
            field=models.IntegerField(blank=True, editable=False, null=True),
        ),
        # Labs' own data, on the primary database only (multidb/db_router.py).
        migrations.RunPython(backfill, migrations.RunPython.noop, hints={"run_on_secondary": False}),
    ]
