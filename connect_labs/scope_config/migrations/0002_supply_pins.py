"""Each programme's pinned supply tabs (SupplyWorkflowView) become its `supply` Settings.

Keyed as supply_chain/config.py reads them: a pin that replaces a built-in tab under
that tab's view name, a pin that adds one under its slug. Slugs are kept, so
/supply/views/<slug>/ keeps working. Recorded as a change via "migration", so the
Settings history says where the tabs came from. The old rows are left in place
(unread) for one release; reversing deletes only what this wrote.
"""

from django.db import migrations

VIA = "migration:supply_pins"


def forward(apps, schema_editor):
    SupplyWorkflowView = apps.get_model("supply_chain", "SupplyWorkflowView")
    ScopeConfig = apps.get_model("scope_config", "ScopeConfig")
    ScopeConfigChange = apps.get_model("scope_config", "ScopeConfigChange")

    by_program: dict[int, dict] = {}
    # The header ordered pins by (position, id); config orders them by (position, slug).
    # Writing each pin's rank in the old order as its position keeps the tabs where
    # they were even when two pins shared a position.
    for rank, pin in enumerate(SupplyWorkflowView.objects.order_by("program_id", "position", "id")):
        key = pin.replaces or pin.slug
        tab = {
            "label": pin.label,
            "slug": pin.slug,
            "fill": {"workflow": pin.workflow_definition_id, "opportunity_id": pin.opportunity_id},
            "position": rank,
        }
        if pin.created_by:
            tab["pinned_by"] = pin.created_by
        by_program.setdefault(pin.program_id, {})[key] = tab

    for program_id, tabs in by_program.items():
        row, _ = ScopeConfig.objects.get_or_create(scope_type="program", scope_key=str(program_id), namespace="supply")
        before = row.data
        after = {**before, "tabs": {**(before.get("tabs") or {}), **tabs}}
        if after == before:
            continue
        row.data = after
        row.version += 1
        row.updated_by = "migration"
        row.save()
        ScopeConfigChange.objects.create(
            config=row, version=row.version, before=before, after=after, changed_by="migration", via=VIA
        )


def backward(apps, schema_editor):
    ScopeConfigChange = apps.get_model("scope_config", "ScopeConfigChange")
    for change in ScopeConfigChange.objects.filter(via=VIA).select_related("config"):
        config = change.config
        config.data = change.before
        config.save()
        change.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("scope_config", "0001_initial"),
        ("supply_chain", "0052_forecast_cases"),
    ]

    # Labs' own data, on the primary database only (multidb/db_router.py).
    operations = [migrations.RunPython(forward, backward, hints={"run_on_secondary": False})]
