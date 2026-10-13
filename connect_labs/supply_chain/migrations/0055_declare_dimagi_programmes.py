"""Declare Dimagi the running organisation of every programme that holds supply data.

Before `ProgrammeRunner` existed, Dimagi was the programme everywhere by
construction (`identity.DIMAGI_ORG_SLUG`). Every programme with supply rows
today was therefore run by Dimagi, and declaring that keeps them exactly as
they were -- by record rather than by a default, so the default can later go.

Every model in this app with a `program_id` column is read, so a table added
later is not silently left out. Reversing deletes only the rows this created.
"""

from django.db import migrations

NOTE = "Declared by migration 0055: held supply data before running organisations existed (#2458)."


def declare(apps, schema_editor):
    LabsOrg = apps.get_model("labs", "LabsOrg")
    ProgrammeRunner = apps.get_model("supply_chain", "ProgrammeRunner")

    program_ids = set()
    for model in apps.get_app_config("supply_chain").get_models():
        if model is ProgrammeRunner:
            continue
        columns = {f.name for f in model._meta.get_fields() if hasattr(f, "column")}
        if "program_id" in columns:
            program_ids.update(
                pid for pid in model.objects.values_list("program_id", flat=True).distinct() if pid is not None
            )
        if "scope_key" in columns:
            # Reference data (catalogue, suppliers) is scoped "prog:<id>": a
            # programme with a catalogue and no tender yet is still a programme.
            for key in model.objects.values_list("scope_key", flat=True).distinct():
                prefix, _, rest = (key or "").partition(":")
                if prefix == "prog" and rest.isdigit():
                    program_ids.add(int(rest))
    if not program_ids:
        return

    dimagi, _ = LabsOrg.objects.get_or_create(slug="dimagi", defaults={"name": "Dimagi", "short_name": "Dimagi"})
    taken = set(ProgrammeRunner.objects.values_list("program_id", flat=True))
    ProgrammeRunner.objects.bulk_create(
        [ProgrammeRunner(program_id=pid, org=dimagi, note=NOTE) for pid in sorted(program_ids - taken)]
    )


def undeclare(apps, schema_editor):
    apps.get_model("supply_chain", "ProgrammeRunner").objects.filter(note=NOTE).delete()


class Migration(migrations.Migration):
    dependencies = [("supply_chain", "0054_programme_runner")]

    operations = [migrations.RunPython(declare, undeclare, hints={"run_on_secondary": False})]
