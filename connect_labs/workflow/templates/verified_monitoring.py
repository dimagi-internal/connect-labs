"""Verified Monitoring (N1) workflow template — vitamin-A CHC home-visit coverage.

A funder-facing verified-coverage dashboard for an independent rooftop survey.
The program ROTATES wards each bi-monthly cycle — a different program ward each
cycle, verified against an adjacent comparison ward — so the dashboard is a time
series of independent checks. The six-cycle trend (self-reported vs independently
verified) is the page hero; a round selector pivots the page; per cycle a compact
self-vs-verified readout plus a Mapbox map that re-centres on that cycle's two real
wards; and ONE drillable-metric block where every metric — the survey-quality
checks AND the independent back-check — opens its own computed evidence below.
Repeated cross-sectional snapshots, NOT a difference-in-differences estimate.

Self-contained: the render reads its data from `instance.state` (the payload
seeded by scripts/walkthroughs/verified-monitoring via survey_sim, which computes
every KPI from row-level records through connect_labs.labs.synthetic.generator.core.survey_quality).
Render code never fetches — all data arrives via props. No server-side job
handler. Map is Mapbox GL via the shared ConnectMap module + real admin
boundaries; charts are inline SVG. Renders an empty-state until seeded.

Render code lives in verified_monitoring_render.js (this template reads it as
RENDER_CODE, so iterating the .js iterates the template).
"""

from pathlib import Path

DEFINITION = {
    "name": "Verified Monitoring",
    "description": "Independent verified vitamin-A coverage — the program rotates wards each bi-monthly cycle, each verified against an adjacent comparison ward.",
    "version": 1,
    "templateType": "verified_monitoring",
    "statuses": [
        {"id": "in_progress", "label": "In Progress", "color": "blue"},
        {"id": "completed", "label": "Completed", "color": "green"},
    ],
    "config": {
        "showSummaryCards": False,
        "showFilters": False,
    },
    "pipeline_sources": [],
}

RENDER_CODE = (Path(__file__).parent / "verified_monitoring_render.js").read_text()

TEMPLATE = {
    "key": "verified_monitoring",
    "name": "Verified Monitoring",
    "description": "Funder-facing independent verified-coverage dashboard — rotating program/comparison wards, bi-monthly, with drillable metrics + an independent back-check.",
    "icon": "map",
    "color": "#7c3aed",
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
}
