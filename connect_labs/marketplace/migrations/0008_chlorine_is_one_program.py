"""The 2025 Chlorine EOI was tagged `water`; it is the `chlorine` program.

The directory import now stores `chlorine` for either spelling
(`marketplace.programs.ALIASES`); this moves the rows already imported, so the
marketplace shows one card without waiting for the next import.
"""

from django.db import migrations


def forwards(apps, schema_editor):
    Solicitation = apps.get_model("solicitations", "Solicitation")
    Solicitation.objects.filter(delivery_type="water").update(delivery_type="chlorine")


class Migration(migrations.Migration):
    dependencies = [
        ("marketplace", "0007_orgmembership_granted_for_org_id"),
        ("solicitations", "0015_one_draft_per_respondent"),
    ]

    # Not reversed: which rows were `water` is not recorded, and `chlorine` is right for all of them.
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop, hints={"run_on_secondary": False})]
