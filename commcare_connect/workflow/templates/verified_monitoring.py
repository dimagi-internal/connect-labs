"""Verified Monitoring (N1) workflow template — vitamin-A CHC home-visit coverage.

A funder-facing verified-coverage dashboard for an independent rooftop survey in
two adjacent wards (treatment vs control), over bi-monthly rounds. The latest
round is the hero; prior rounds show as a trend. Repeated cross-sectional
snapshots, NOT a difference-in-differences estimate.

Self-contained: the render reads its data from `instance.state` (the payload
seeded by scripts/walkthroughs/verified-monitoring via the synthetic generator +
walkthrough_kit). Render code never fetches — all data arrives via props. No
server-side job handler. Map is Leaflet (loaded dynamically); charts are inline
SVG. Renders an empty-state until seeded.

Render code lives in verified_monitoring_render.js alongside this file.
"""

from pathlib import Path

DEFINITION = {
    "name": "Verified Monitoring",
    "description": "Independent verified vitamin-A coverage, treatment vs control ward, over bi-monthly rounds.",
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
    "description": "Funder-facing independent verified-coverage dashboard (treatment vs control, bi-monthly).",
    "icon": "map",
    "color": "#7c3aed",
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
}
