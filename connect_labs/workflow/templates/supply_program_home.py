"""A program's supply home: the page a program opens on, drawn from its own supply figures.

A page (workflow/page_mode.py), so it has no runs and draws in the page sandbox. It
reads, as the viewer, the supply sources every Supply screen reads
(workflow/supply_sources.py) across the opportunities it lists:

- the one headline for workers in trouble ("N of M workers below minimum at their
  own 14-day pace", worded by the server), what is in their hands, and how many
  checks are open;
- the forecast: the day the network runs dry at the protocol's ration, and what
  children are actually receiving against that protocol;
- the stores, with their own cover and their workers' (subtree) cover;
- links to the program's Supply tabs, read from its Settings (`config.supply.tabs`),
  and to the workflows that fill them -- so no workflow id is written into the page.

Like every workflow it shares nothing with the canopy agent panel unless an instance
turns `config.agent.share` on (the seeded demo's does). It writes nothing. Seeded as
the RUTF clone demo's program home by supply_chain/demo/clone_pages.py; follows the
deployed template, so a deploy reaches it.
"""

from pathlib import Path

_RENDER = (Path(__file__).parent / "supply_program_home_render.js").read_text()

DEFINITION = {
    "name": "Supply home",
    "description": (
        "A program's supply at a glance: workers below minimum, when the network runs dry at protocol, "
        "what children are actually receiving, the stores, and the way into each Supply tab. Read as you."
    ),
    "version": 1,
    "kind": "page",
    # /labs/p/programme/<id>/supply/; the program's home in the seeded demo.
    "page": {"slug": "supply"},
    "templateType": "supply_program_home",
    "statuses": [],
    "config": {
        "templateType": "supply_program_home",
        "multi_opp": True,
        "showFilters": False,
        "showSummaryCards": False,
        "renderWhileLoading": True,
        "noPipelineStream": True,
        "item": "rutf",
        # A child's full course at the protocol, for "about N children's courses".
        "course_size": 168,
        "course_label": "children's courses",
    },
    "pipeline_sources": [],
    "workflow_sources": [],
    "config_reads": ["supply"],
    "supply_sources": [
        {"alias": "stock", "source": "worker_stock", "item": "rutf"},
        {"alias": "stores", "source": "network_stock", "item": "rutf"},
        {"alias": "forecast", "source": "stock_forecast", "item": "rutf", "params": {"course_size": 168}},
        {"alias": "checks", "source": "checks_list"},
    ],
}

TEMPLATE = {
    "key": "supply_program_home",
    "name": DEFINITION["name"],
    "description": DEFINITION["description"],
    "icon": "fa-house",
    "color": "indigo",
    "kind": "page",
    "multi_opp": True,
    "supports_saved_runs": False,
    # An instance carries no code of its own: a deploy reaches every page drawn from it.
    "follow_template": True,
    "definition": DEFINITION,
    "render_code": _RENDER,
}
