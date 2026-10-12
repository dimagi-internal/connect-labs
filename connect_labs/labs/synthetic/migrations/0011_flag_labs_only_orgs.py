"""Mark the rows `0010` filed labs-only opps under as labs-only organisations.

`LabsOrg.labs_only` is what decides whether a key is a labs-only organisation's
(not its prefix, not the org tree). The rows `0010` made or reused for the old
name buckets are exactly the unlinked, `labs-synthetic-` rows a labs-only opp now
points at. The memberships `0010` granted were written without
`OrgMembership.save()`, so they are stamped as granted for their row here.

Reversing clears the flag and nothing else.
"""

from django.db import migrations
from django.db.models import F

PREFIX = "labs-synthetic-"


def forward(apps, schema_editor):
    SyntheticOpportunity = apps.get_model("synthetic", "SyntheticOpportunity")
    LabsOrg = apps.get_model("labs", "LabsOrg")
    OrgMembership = apps.get_model("marketplace", "OrgMembership")

    filed = SyntheticOpportunity.objects.filter(labs_only=True, labs_org__isnull=False).values("labs_org_id")
    rows = LabsOrg.objects.filter(pk__in=filed, connect_organization_id__isnull=True, slug__startswith=PREFIX)
    rows.update(labs_only=True)
    OrgMembership.objects.filter(org__labs_only=True, granted_for_org_id__isnull=True).update(
        granted_for_org_id=F("org_id")
    )


def backward(apps, schema_editor):
    apps.get_model("labs", "LabsOrg").objects.filter(labs_only=True).update(labs_only=False)


class Migration(migrations.Migration):
    dependencies = [
        ("synthetic", "0010_syntheticopportunity_labs_org"),
        ("labs", "0026_labsorg_labs_only"),
        ("marketplace", "0007_orgmembership_granted_for_org_id"),
    ]

    # Labs' own data, on the primary database only (multidb/db_router.py).
    operations = [migrations.RunPython(forward, backward, hints={"run_on_secondary": False})]
