"""Supply Stock Review: what is in field workers' hands, and what is happening to it.

The first template built on supply sources (workflow/supply_sources.py): it reads
the supply chain's own figures -- each worker's issued, given out, counted and on
hand, and the stores above them -- for every opportunity the workflow spans, across
programmes if it spans them, as the viewer.

It answers, in this order: who runs out soonest (and who already has); is the stock
where the work is; do the numbers add up (issued against given out against
counted); and what the stores hold behind them. Sachets lead -- the unit a worker
gives out and counts -- with cartons only for stores. A worker's pace is what their
own visits gave out, and says how many days it rests on.

It writes nothing. Supply writes become workflow actions when a view needs them.
"""

from pathlib import Path

_RENDER = (Path(__file__).parent / "supply_stock_review_render.js").read_text()

DEFINITION = {
    "name": "Supply Stock Review",
    "description": (
        "Stock in field workers' hands across the workflow's opportunities: who runs out soonest, "
        "whether issued, given out and counted add up, and what the stores hold behind them. Reads "
        "the supply chain's own figures, as you."
    ),
    "version": 1,
    "templateType": "supply_stock_review",
    "statuses": [],
    "config": {
        "multi_opp": True,
        "showFilters": False,
        "showSummaryCards": False,
        "templateType": "supply_stock_review",
        "renderWhileLoading": True,
        "noPipelineStream": True,
        # The commodity this review is about (a commodity slug or a SKU), and how
        # many of its base units make one course -- for the "children's courses" line.
        "item": "rutf",
        "course_size": 150,
        "course_label": "children's courses",
    },
    "pipeline_sources": [],
    "supply_sources": [
        # Pace is each worker's own last fortnight: recent, not a 90-day average.
        {"alias": "stock", "source": "worker_stock", "item": "rutf", "params": {"window_days": 14}},
        {"alias": "stores", "source": "network_stock", "item": "rutf"},
        {
            "alias": "worker",
            "source": "worker_stock_get",
            "item": "rutf",
            "load": "on_demand",
            "params": {"window_days": 14},
        },
    ],
}

TEMPLATE = {
    "key": "supply_stock_review",
    "name": DEFINITION["name"],
    "description": DEFINITION["description"],
    "icon": "fa-boxes-stacked",
    "color": "indigo",
    "multi_opp": True,
    "supports_saved_runs": False,
    "definition": DEFINITION,
    "render_code": _RENDER,
}
