"""Supply's report templates are followed: a deploy reaches every tab drawn from them.

A stored copy froze each Supply tab at the code it was created with, so merged
label and number fixes never reached the tabs already pinned.
"""

import pytest

from connect_labs.workflow.templates import TEMPLATES, create_workflow_from_template

from .test_template_companions import _data_access


@pytest.mark.parametrize("key", ["supply_stock_review", "supply_stock_forecast"])
def test_a_supply_report_follows_the_deployed_template(key):
    dao = _data_access()
    definition, _render, _pipelines = create_workflow_from_template(dao, key, request=None, opportunity_ids=[10113])
    assert definition.data["render_source"] == {"template": key}
    assert TEMPLATES[key]["follow_template"] is True


def test_stock_review_reads_how_long_it_lasts_from_the_forecast_and_says_so():
    from connect_labs.workflow.supply_sources import declaration_problems
    from connect_labs.workflow.templates import supply_stock_review

    sources = supply_stock_review.DEFINITION["supply_sources"]
    assert declaration_problems(sources) == []
    assert {s["source"] for s in sources} >= {"worker_stock", "stock_forecast"}
    render = supply_stock_review.TEMPLATE["render_code"]
    assert "runs_dry_on" in render
    assert "Days until each FLW runs out, based on the children they're" in render
    assert "at recent pace" in render


def test_stock_review_course_tile_uses_the_forecasts_expected_course_and_names_its_basis():
    """The 'With workers' tile counted courses at the report's own setting (168 -> 52 courses) while the
    Forecast page's expected course was 40 sachets: one label, two courses."""
    from connect_labs.workflow.templates import supply_stock_review

    render = supply_stock_review.TEMPLATE["render_code"]
    assert "src.forecast.rows[0].basis.course" in render
    assert "forecastCourse.completed_cases" in render
    assert "expected course of" in render and "protocol course of" in render
    # The setting is only the last resort, and the tile never divides by it when the forecast has a course.
    assert render.index("forecastSize > 0") < render.index("Number(cfg.course_size) > 0")
    assert "totals.on_hand / courseSize" in render and "courseNote" in render
