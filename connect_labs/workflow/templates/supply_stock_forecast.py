"""Supply Stock Forecast: where the stock is heading, given the children in treatment and those to come.

Built on one supply source, `stock_forecast` (supply_chain/stock/services/forecast.py),
which joins Connect's service delivery -- children enrolled, their visits, the
sachets each has received -- to the stock at every worker and store, and lays the
need forward week by week. This template only draws it:

- the programme: weekly given out so far running into the forecast (committed to
  children in treatment, plus new children), the stock left in the network, and the
  day it runs dry;
- children in treatment by the week they enrolled, and what they are still owed;
- workers and stores, soonest dry first;
- every assumption as a chip (course size and where it came from, enrolment, data
  to), and a scenario control for enrolment.

Exploratory, not a decision tool: it writes nothing.
"""

from pathlib import Path

_RENDER = (Path(__file__).parent / "supply_stock_forecast_render.js").read_text()

DEFINITION = {
    "name": "Supply Stock Forecast",
    "description": (
        "Where stock is heading: what the children already in treatment are still owed, plus the children "
        "each worker is expected to enrol, against the stock at every worker and store -- and when each "
        "runs dry. Joins Connect's visits to the supply chain's own figures, as you."
    ),
    "version": 1,
    "templateType": "supply_stock_forecast",
    "statuses": [],
    "config": {
        "multi_opp": True,
        "showFilters": False,
        "showSummaryCards": False,
        "templateType": "supply_stock_forecast",
        "renderWhileLoading": True,
        "noPipelineStream": True,
        "item": "rutf",
        "course_label": "children's courses",
    },
    "pipeline_sources": [],
    "supply_sources": [
        # course_size is the course to assume when neither the visits (completed
        # cases) nor the commodity's protocol give one; the forecast says which it used.
        {"alias": "forecast", "source": "stock_forecast", "item": "rutf", "params": {"course_size": 150}},
    ],
}

TEMPLATE = {
    "key": "supply_stock_forecast",
    "name": DEFINITION["name"],
    "description": DEFINITION["description"],
    "icon": "fa-chart-line",
    "color": "indigo",
    "multi_opp": True,
    "supports_saved_runs": False,
    "definition": DEFINITION,
    "render_code": _RENDER,
}
