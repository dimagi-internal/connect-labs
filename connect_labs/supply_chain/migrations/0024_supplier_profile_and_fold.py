"""A supplier is a company, part one: every supplier gets its organisation.

Every supplier row gets an organisation -- its own `org` if bound, else the one
holding its Connect id, else the one its name's slug already belongs to, else a
new "local for good" organisation with no Connect id. Its company facts fill the
blanks of that organisation's supplier profile. Two rows in one program that
turn out to be one company are folded into one, and everything that pointed at
the dropped row is repointed rather than deleted.

The columns leave `Supplier` in 0025. They are two migrations, not one,
because Postgres refuses to ALTER a table with pending deferred-constraint
events in the same transaction as the rows this one rewrites.
"""

import hashlib

import django.db.models.deletion
from django.db import migrations, models
from django.db.migrations.exceptions import IrreversibleError

from connect_labs.marketplace.identity import SLUG_MAX, slug_for

PROFILE_FIELDS = ("type", "city", "contacts", "qualifications")
POINTING_AT_SUPPLIER = ("Quote", "Outreach", "Award", "Contract", "Document")


def _carry_identity(org, row):
    """Copy what only the old row knew onto the organisation, where it is blank."""
    changed = []
    if row.country and not org.country:
        org.country = row.country
        changed.append("country")
    if row.connect_organization_id and org.connect_organization_id is None:
        taken = type(org).objects.filter(connect_organization_id=row.connect_organization_id).exclude(pk=org.pk)
        if not taken.exists():
            org.connect_organization_id = row.connect_organization_id
            changed.append("connect_organization_id")
    if changed:
        org.save(update_fields=changed)
    return org


def _org_from_links(apps, row):
    """The organisation this supplier's update links were issued to, if exactly one.

    Before this migration a link issued to an organisation covering an order
    whose supplier had no organisation let that organisation act as the
    supplier. Now the supplier IS an organisation, so the link keeps working
    only if the supplier is bound to the organisation it was issued to. Links
    issued to the order's buyer, or to whoever runs its delivery store, are a
    partner's, not the supplier's, and do not count.
    """
    UpdateLink = apps.get_model("supply_chain", "UpdateLink")
    Contract = apps.get_model("supply_chain", "Contract")
    candidates = set()
    for contract in Contract.objects.filter(supplier_id=row.pk).select_related("delivery_supply_point"):
        receiving = {contract.buyer_org_id}
        if contract.delivery_supply_point is not None:
            receiving.add(contract.delivery_supply_point.managed_by_org_id)
        for org_id in UpdateLink.objects.filter(contracts=contract).values_list("org_id", flat=True):
            if org_id not in receiving:
                candidates.add(org_id)
    if len(candidates) == 1:
        return apps.get_model("labs", "LabsOrg").objects.get(pk=candidates.pop())
    return None


def _org_for(apps, LabsOrg, row):
    if row.org_id:
        return _carry_identity(row.org, row)
    by_link = _org_from_links(apps, row)
    if by_link is not None:
        return _carry_identity(by_link, row)
    if row.connect_organization_id:
        linked = LabsOrg.objects.filter(connect_organization_id=row.connect_organization_id).first()
        if linked is not None:
            return linked
    name = (row.name or "").strip() or f"Supplier {row.pk}"
    # The same resolution as `identity.find_or_mint_supplier_org`: one
    # organisation with exactly this name is this company.
    named = list(LabsOrg.objects.filter(name__iexact=name)[:2])
    if len(named) == 1:
        org = named[0]
        if row.country and not org.country:
            org.country = row.country
            org.save(update_fields=["country"])
        return org
    slug = slug_for(name)
    org = LabsOrg.objects.filter(slug=slug).first()
    if org is not None and org.name.strip().lower() != name.lower():
        digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:6]
        slug = f"{slug[: SLUG_MAX - 7]}-{digest}"
        org = LabsOrg.objects.filter(slug=slug).first()
    if org is None:
        connect_id = row.connect_organization_id
        if connect_id and LabsOrg.objects.filter(connect_organization_id=connect_id).exists():
            connect_id = None
        org = LabsOrg.objects.create(slug=slug, name=name, country=row.country or "", connect_organization_id=connect_id)
    elif row.connect_organization_id and org.connect_organization_id is None:
        org.connect_organization_id = row.connect_organization_id
        org.save(update_fields=["connect_organization_id"])
    if row.country and not org.country:
        org.country = row.country
        org.save(update_fields=["country"])
    return org


def _fill(profile, row):
    changed = False
    for field in PROFILE_FIELDS:
        value = getattr(row, field)
        if value in (None, "", []):
            continue
        current = getattr(profile, field)
        if field == "contacts" and current:
            seen = {(c.get("email") or "").lower() for c in current if c.get("email")}
            extra = [c for c in value if not c.get("email") or c["email"].lower() not in seen]
            if extra:
                profile.contacts = current + extra
                changed = True
        elif current in ("", [], None):
            setattr(profile, field, value)
            changed = True
    if changed:
        profile.save()


def fold(apps, schema_editor):
    LabsOrg = apps.get_model("labs", "LabsOrg")
    Supplier = apps.get_model("supply_chain", "Supplier")
    SupplierProfile = apps.get_model("supply_chain", "SupplierProfile")
    pointing = [apps.get_model("supply_chain", name) for name in POINTING_AT_SUPPLIER]

    survivors = {}
    for row in Supplier.objects.order_by("pk"):
        org = _org_for(apps, LabsOrg, row)
        profile, _ = SupplierProfile.objects.get_or_create(org=org)
        _fill(profile, row)

        survivor = survivors.get((row.scope_key, org.pk))
        if survivor is None:
            if row.org_id != org.pk:
                row.org = org
                row.save(update_fields=["org"])
            survivors[(row.scope_key, org.pk)] = row
            continue
        # Said out loud: a fold is a judgement that two rows were one company,
        # and whoever runs the migration should be able to check it.
        print(
            f"  supplier {row.pk} ({row.name!r}) folded into {survivor.pk} "
            f"as one company in {row.scope_key}: {org.name!r} (org {org.pk})"
        )
        for model in pointing:
            model.objects.filter(supplier_id=row.pk).update(supplier_id=survivor.pk)
        if row.notes and row.notes not in survivor.notes:
            survivor.notes = "\n\n".join(n for n in (survivor.notes, row.notes) if n)
            survivor.save(update_fields=["notes"])
        row.delete()


def unfold(apps, schema_editor):
    """Refuse to roll back over real suppliers.

    The company columns are dropped in 0025 and folded rows cannot be
    unfolded, so a rollback with suppliers on file would lose them -- or
    fail half-way re-adding a NOT NULL column. Only an empty table (a fresh
    database, a test) may go back.
    """
    if apps.get_model("supply_chain", "Supplier").objects.exists():
        raise IrreversibleError(
            "0024 cannot be reversed with suppliers on file: their company facts now live on the organisation"
        )


class Migration(migrations.Migration):
    dependencies = [
        ("labs", "0024_workflowschedule_interval_hours"),
        ("supply_chain", "0023_place_supply_points"),
    ]

    operations = [
        migrations.CreateModel(
            name="SupplierProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("type", models.CharField(blank=True, default="", max_length=32)),
                ("city", models.CharField(blank=True, default="", max_length=128)),
                ("contacts", models.JSONField(blank=True, default=list)),
                ("qualifications", models.JSONField(blank=True, default=list)),
                ("website", models.URLField(blank=True, default="", max_length=500)),
                ("description", models.TextField(blank=True, default="")),
                (
                    "org",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="supplier_profile",
                        to="labs.labsorg",
                    ),
                ),
            ],
            options={"abstract": False},
        ),
        migrations.RunPython(fold, unfold, hints={"run_on_secondary": False}),
    ]
