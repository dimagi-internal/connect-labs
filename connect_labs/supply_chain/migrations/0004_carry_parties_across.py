"""Carry every party across to the one organisation registry.

Separate from the schema change before it and the drop after it, so the
copy is committed before anything is destroyed.
"""

from django.db import migrations


def carry_parties_across(apps, schema_editor):
    """Every Party becomes a LabsOrg, and every link follows it.

    Parties were programme-scoped, so the same organisation could exist as
    several rows in several programmes. They collapse on slug -- which is
    the correction: one organisation, one row.

    `kind` and `roles` are deliberately not carried. What an organisation is
    to a purchase is a fact about the purchase (`Contract.buyer_of_record`,
    which already holds it), not about the organisation.
    """
    Party = apps.get_model("supply_chain", "Party")
    LabsOrg = apps.get_model("labs", "LabsOrg")

    by_party_id = {}
    for party in Party.objects.all().order_by("pk"):
        org, _ = LabsOrg.objects.get_or_create(
            slug=party.slug,
            defaults={
                "name": party.name,
                "country": party.country or "",
                "connect_organization_id": party.connect_organization_id,
                "notes": party.notes or "",
            },
        )
        # A later duplicate of the same slug may carry the Connect id the
        # first did not; take it rather than lose the link.
        if org.connect_organization_id is None and party.connect_organization_id is not None:
            org.connect_organization_id = party.connect_organization_id
            org.save(update_fields=["connect_organization_id"])
        by_party_id[party.pk] = org

    if not by_party_id:
        return

    sourced = (
        "Contract", "Shipment", "Receipt", "Invoice", "Payment", "Document",
        "Movement", "StockCount", "Distribution", "SupplyPoint",
    )
    for name in sourced:
        model = apps.get_model("supply_chain", name)
        for row in model.objects.exclude(recorded_by_party_id=None).iterator():
            row.recorded_by_org = by_party_id.get(row.recorded_by_party_id)
            row.save(update_fields=["recorded_by_org"])

    Contract = apps.get_model("supply_chain", "Contract")
    for row in Contract.objects.exclude(buyer_party_id=None).iterator():
        row.buyer_org = by_party_id.get(row.buyer_party_id)
        row.save(update_fields=["buyer_org"])

    Supplier = apps.get_model("supply_chain", "Supplier")
    for row in Supplier.objects.exclude(party_id=None).iterator():
        row.org = by_party_id.get(row.party_id)
        row.save(update_fields=["org"])

    SupplyPoint = apps.get_model("supply_chain", "SupplyPoint")
    for row in SupplyPoint.objects.exclude(managed_by_party_id=None).iterator():
        row.managed_by_org = by_party_id.get(row.managed_by_party_id)
        row.save(update_fields=["managed_by_org"])


def unmigrate(apps, schema_editor):
    """Deliberately a no-op rather than a reversal.

    The forward step collapses parties onto one organisation per slug, so it
    loses which programme each duplicate belonged to. Reversing would have to
    invent that, and a migration that guesses is worse than one that refuses.
    The rows it created stay; the next forward run finds them by slug.
    """


class Migration(migrations.Migration):

    dependencies = [("supply_chain", "0003_org_replaces_party")]

    operations = [
        # `run_on_secondary` is required by connect_labs.multidb: False
        # because supply's tables are the labs database's own.
        migrations.RunPython(carry_parties_across, unmigrate, hints={"run_on_secondary": False}),
    ]
