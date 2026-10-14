"""Revoke update links that were issued to an organisation which now runs their programme.

`update_link_issue` refuses such a link, and `runner.declare` revokes them from
now on, but links issued before #2458 still work: whoever holds one writes as
the programme with no sign-in (design §8, M3). Not reversible -- a revoked link
stays revoked.
"""

from django.db import migrations
from django.utils import timezone


def revoke(apps, schema_editor):
    ProgrammeRunner = apps.get_model("supply_chain", "ProgrammeRunner")
    UpdateLink = apps.get_model("supply_chain", "UpdateLink")
    now = timezone.now()
    for program_id, org_id in ProgrammeRunner.objects.values_list("program_id", "org_id"):
        UpdateLink.objects.filter(program_id=program_id, org_id=org_id, revoked_at__isnull=True).update(
            revoked_at=now
        )


class Migration(migrations.Migration):
    dependencies = [("supply_chain", "0056_declare_dimagi_programmes")]

    operations = [migrations.RunPython(revoke, migrations.RunPython.noop, hints={"run_on_secondary": False})]
