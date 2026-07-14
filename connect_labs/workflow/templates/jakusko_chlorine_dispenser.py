"""Jakusko Chlorine Dispenser Pilot — synthetic priority-bucket dashboard.

Entirely driven by a labs-only SyntheticOpportunity (id 10034, generated from a
YAML manifest — see docs/plans or ask the author). No real Connect opportunity
or production data is read or written by this template.

Pipeline: one visit_level pipeline (alias "visits") over the synthetic opp's
generated visits. Each visit carries custom `form.dispenser.*` fields drawn
per-dispenser-cohort in the manifest (functionality/stock/open_issue/waterpoint/
compliance/hh_valid/hh_visited/ward/community/lga). All derivation (priority
buckets, carry-forward on missed visits, monthly bucketing, usage labels) happens
in RENDER_CODE — no Python job handler, same pattern as mbw_auditing_v5.
"""

from pathlib import Path

PIPELINE_SCHEMAS = [
    {
        "alias": "visits",
        "name": "Dispenser Visits (Synthetic)",
        "description": "Per-visit rows for the Jakusko chlorine dispenser pilot",
        "schema": {
            "data_source": {"type": "connect_csv"},
            "grouping_key": "entity_id",
            "terminal_stage": "visit_level",
            "fields": [
                {"name": "functionality", "path": "form.dispenser.functionality", "aggregation": "first"},
                {"name": "stock", "path": "form.dispenser.stock", "aggregation": "first"},
                {"name": "open_issue", "path": "form.dispenser.open_issue", "aggregation": "first"},
                {"name": "waterpoint", "path": "form.dispenser.waterpoint", "aggregation": "first"},
                {"name": "compliance", "path": "form.dispenser.compliance", "aggregation": "first"},
                {"name": "hh_valid", "path": "form.dispenser.hh_valid", "aggregation": "first"},
                {"name": "hh_visited", "path": "form.dispenser.hh_visited", "aggregation": "first"},
                {"name": "ward", "path": "form.dispenser.ward", "aggregation": "first"},
                {"name": "community", "path": "form.dispenser.community", "aggregation": "first"},
                {"name": "lga", "path": "form.dispenser.lga", "aggregation": "first"},
            ],
        },
    }
]

DEFINITION = {
    "name": "Jakusko Chlorine Dispenser Dashboard (Synthetic)",
    "description": "Priority-bucket operational dashboard for the Jakusko chlorine dispenser pilot. 100% synthetic data.",
    "version": 1,
    "templateType": "jakusko_chlorine_dispenser",
    "statuses": [
        {"id": "pending", "label": "Pending", "color": "gray"},
        {"id": "reviewed", "label": "Reviewed", "color": "green"},
    ],
    "config": {
        "showSummaryCards": True,
        "showFilters": True,
    },
    "pipeline_sources": [],
}

RENDER_CODE = (Path(__file__).parent / "jakusko_chlorine_dispenser_render.js").read_text(encoding="utf-8")

TEMPLATE = {
    "key": "jakusko_chlorine_dispenser",
    "name": "Jakusko Chlorine Dispenser Dashboard (Synthetic)",
    "description": "Priority-bucket dashboard (access/functionality vs. delays/usage) for a synthetic chlorine dispenser pilot.",
    "icon": "fa-tint",
    "color": "blue",
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
    "pipeline_schemas": PIPELINE_SCHEMAS,
}
