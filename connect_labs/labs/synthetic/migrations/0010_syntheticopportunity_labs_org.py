"""File every labs-only synthetic opp under a `LabsOrg` registry row.

A labs-only organisation used to be `labs-synthetic-<slug of org_name>`, worked out
on every request: every synthetic opp anyone gave the same organisation name was
one organisation. Each such bucket becomes one row here, and the row's slug IS
the key the bucket already had -- so Settings rows, benchmark cohorts, page
addresses and sessions that name a bucket keep naming the same organisation.
A row that already holds that slug is the bucket's row.

Who administers it: the bucket's creator, when one person made every opp in it.
A bucket several people (or no recorded person) filled is nobody's: its settings
stay with Dimagi staff until someone is made its administrator.

Opps made after this are filed by `labs.synthetic.org_tree.assign_labs_org`.
Irreversible, deliberately. The organisation rows and admin memberships it makes
cannot be told apart from ones made afterwards, and leaving them behind with
nothing filed under them would leave admins of organisations that no longer
mean anything. To undo it, take a database snapshot first.
"""

import django.db.models.deletion
from django.db import migrations, models

PREFIX = "labs-synthetic-"


def _slugify(value):
    # As labs.synthetic.org_tree.slugify stood when this was written; copied so the
    # migration does not change if that function ever does.
    return "".join(c if c.isalnum() else "-" for c in (value or "").strip().lower()).strip("-") or "labs"


def _legacy_key(org_name):
    key = f"{PREFIX}{_slugify(org_name or 'Labs Synthetic')}"
    # `labs-synthetic-unfiled-<opp id>` is each opp's own key when it has no
    # organisation (org_tree.unfiled_key); a name must never land on one.
    if key.startswith(f"{PREFIX}unfiled-"):
        key = f"{PREFIX}org-{key[len(PREFIX):]}"
    return key


def backfill(apps, schema_editor):
    SyntheticOpportunity = apps.get_model("synthetic", "SyntheticOpportunity")
    LabsOrg = apps.get_model("labs", "LabsOrg")
    OrgMembership = apps.get_model("marketplace", "OrgMembership")

    buckets = {}
    for opp in SyntheticOpportunity.objects.filter(labs_only=True, labs_org__isnull=True).order_by("pk"):
        buckets.setdefault(_legacy_key(opp.org_name), []).append(opp)

    for key, opps in buckets.items():
        org = LabsOrg.objects.filter(slug=key).first()
        if org is None:
            org = LabsOrg.objects.create(slug=key, name=opps[0].org_name or "Labs Synthetic")
        elif org.connect_organization_id is not None:
            # A real organisation already holds this slug. Filing synthetic opps under
            # it would make their viewers its holders; leave them unfiled, which reads
            # as the old key and so changes nothing for them.
            continue
        SyntheticOpportunity.objects.filter(pk__in=[o.pk for o in opps]).update(labs_org=org)

        creators = {o.created_by_id for o in opps}
        if len(creators) == 1 and None not in creators:
            OrgMembership.objects.get_or_create(org=org, user_id=creators.pop(), defaults={"role": "admin"})


class Migration(migrations.Migration):
    dependencies = [
        ("synthetic", "0009_syntheticopportunity_generated_folder_id"),
        ("labs", "0023_labsorg"),
        ("marketplace", "0006_org_operating_areas"),
    ]

    operations = [
        migrations.AddField(
            model_name="syntheticopportunity",
            name="labs_org",
            field=models.ForeignKey(
                blank=True,
                help_text="The labs-only organisation this opp is filed under; its slug is the organisation's key "
                "everywhere (org tree, Settings, page addresses). Assigned on first save "
                "(labs.synthetic.org_tree.assign_labs_org); org_name is only the label it started from. "
                "Ignored when labs_only=False.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="synthetic_opportunities",
                to="labs.labsorg",
            ),
        ),
        # Labs' own data, on the primary database only (multidb/db_router.py).
        migrations.RunPython(backfill, hints={"run_on_secondary": False}),
    ]
