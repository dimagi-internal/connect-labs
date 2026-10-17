from django.db import migrations

DIMAGI_ORG_SLUG = "dimagi"  # supply_chain.runner.DIMAGI_ORG_SLUG, frozen for this migration


def own_directory_rounds(apps, schema_editor):
    """Dimagi ran every directory round, so it owns them (`marketplace.eoi.upsert_rounds`
    sets this on every import from here on)."""
    LabsOrg = apps.get_model("labs", "LabsOrg")
    Solicitation = apps.get_model("solicitations", "Solicitation")
    if not Solicitation.objects.filter(source="directory", owner_org__isnull=True).exists():
        return
    dimagi, _ = LabsOrg.objects.get_or_create(slug=DIMAGI_ORG_SLUG, defaults={"name": "Dimagi", "short_name": "Dimagi"})
    Solicitation.objects.filter(source="directory", owner_org__isnull=True).update(owner_org=dimagi)


def disown_directory_rounds(apps, schema_editor):
    LabsOrg = apps.get_model("labs", "LabsOrg")
    Solicitation = apps.get_model("solicitations", "Solicitation")
    dimagi = LabsOrg.objects.filter(slug=DIMAGI_ORG_SLUG).first()
    if dimagi is not None:
        Solicitation.objects.filter(source="directory", owner_org=dimagi).update(owner_org=None)


class Migration(migrations.Migration):

    dependencies = [
        ("labs", "0030_drop_retired_oes_supply_tables"),
        ("solicitations", "0015_one_draft_per_respondent"),
    ]

    operations = [
        migrations.RunPython(own_directory_rounds, disown_directory_rounds, hints={"run_on_secondary": False}),
    ]
