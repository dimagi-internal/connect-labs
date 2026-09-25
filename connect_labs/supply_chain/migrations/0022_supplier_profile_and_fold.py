"""A supplier is a company, part one: every supplier gets its organisation.

Every supplier row gets an organisation -- its own `org` if bound, else the one
holding its Connect id, else the one its name's slug already belongs to, else a
new "local for good" organisation with no Connect id. Its company facts fill the
blanks of that organisation's supplier profile. Two rows in one program that
turn out to be one company are folded into one, and everything that pointed at
the dropped row is repointed rather than deleted.

The columns leave `Supplier` in 0023. They are two migrations, not one,
because Postgres refuses to ALTER a table with pending deferred-constraint
events in the same transaction as the rows this one rewrites.
"""

import hashlib

import django.db.models.deletion
from django.db import migrations, models

from connect_labs.marketplace.identity import SLUG_MAX, slug_for

PROFILE_FIELDS = ("type", "city", "contacts", "qualifications")
POINTING_AT_SUPPLIER = ("Quote", "Outreach", "Award", "Contract", "Document")


def _org_for(LabsOrg, row):
    if row.org_id:
        return row.org
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
        org = _org_for(LabsOrg, row)
        profile, _ = SupplierProfile.objects.get_or_create(org=org)
        _fill(profile, row)

        survivor = survivors.get((row.scope_key, org.pk))
        if survivor is None:
            if row.org_id != org.pk:
                row.org = org
                row.save(update_fields=["org"])
            survivors[(row.scope_key, org.pk)] = row
            continue
        for model in pointing:
            model.objects.filter(supplier_id=row.pk).update(supplier_id=survivor.pk)
        if row.notes and row.notes not in survivor.notes:
            survivor.notes = "\n\n".join(n for n in (survivor.notes, row.notes) if n)
            survivor.save(update_fields=["notes"])
        row.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("labs", "0024_workflowschedule_interval_hours"),
        ("supply_chain", "0021_portfolio"),
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
        migrations.RunPython(fold, migrations.RunPython.noop, hints={"run_on_secondary": False}),
    ]
