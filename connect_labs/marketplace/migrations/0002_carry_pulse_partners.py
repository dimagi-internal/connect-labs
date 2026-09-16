"""Carry pulse's partner rows into the organisation registry.

Runs before pulse drops its tables. Every join date on the network growth curve
is a directory fact Connect has never held and cannot regenerate, so dropping
the table without this would silently flatten that chart.
"""

from django.db import migrations


def forwards(apps, schema_editor):
    from connect_labs.marketplace.carry import carry_partners

    PulsePartner = apps.get_model("pulse", "PulsePartner")
    PulsePartnerAlias = apps.get_model("pulse", "PulsePartnerAlias")

    rows = list(
        PulsePartner.objects.values(
            "name",
            "short",
            "joined_at",
            "joined_basis",
            "country_iso3",
            "lat",
            "lon",
            "location_precision",
            "location_label",
        )
    )
    aliases = [
        {"slug": slug, "partner_name": partner_name, "why": why}
        for slug, partner_name, why in PulsePartnerAlias.objects.values_list("slug", "partner__name", "why")
    ]
    carry_partners(rows, aliases)


def backwards(apps, schema_editor):
    """Intentionally a no-op: the registry keeps what it was given.

    Reversing this migration should not delete organisations — by the time
    anyone reverses it, the registry is also being fed by marketplace_import
    and cannot tell which rows came from where.
    """


class Migration(migrations.Migration):
    dependencies = [
        ("marketplace", "0001_initial"),
        ("pulse", "0015_partner_joined_at"),
    ]

    # This repo's multi-DB router requires every RunPython to say which
    # database it belongs to. The registry is primary-only.
    operations = [migrations.RunPython(forwards, backwards, hints={"run_on_secondary": False})]
