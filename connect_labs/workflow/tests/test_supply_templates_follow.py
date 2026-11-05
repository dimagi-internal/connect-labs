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
    # The one run-out answer (forecast.worker_run_out), read and never recomputed here.
    assert "f.run_out" in render and "runs_dry_on" not in render
    assert "Days until each FLW runs out, based on the children they're" in render
    assert "at recent pace" in render


def test_stock_review_course_tile_uses_the_forecasts_expected_course_and_names_its_basis():
    """The 'With workers' tile counted courses at the report's own setting (168 -> 52 courses) while the
    Forecast page's expected course was 40 sachets: one label, two courses."""
    from connect_labs.workflow.templates import supply_stock_review

    render = supply_stock_review.TEMPLATE["render_code"]
    assert "src.forecast.rows[0].basis.course" in render
    assert "forecastCourse.completed_cases" in render
    # Both estimates are named, and which one the plan uses.
    assert "low estimate of" in render and "protocol (high) estimate of" in render
    # The setting is only the last resort, and the tile never divides by it when the forecast has a course.
    assert render.index("forecastSize > 0") < render.index("Number(cfg.course_size) > 0")
    assert "totals.on_hand / courseSize" in render and "courseNote" in render


def test_stock_review_and_forecast_say_which_figure_is_official_and_what_day_they_run_to():
    from connect_labs.workflow.templates import supply_stock_forecast, supply_stock_review

    review = supply_stock_review.TEMPLATE["render_code"]
    forecast = supply_stock_forecast.TEMPLATE["render_code"]
    # The official figure (phone until the partner's handovers are recorded) is the forecast's, never recomputed.
    assert "official_stock" in review and "Official figure" in review
    assert "official_stock" in forecast and "phone balance" in forecast
    # The day the visits stop, and a date behind us flagged; one date format with the year.
    assert "Based on visits up to" in review and "based on visits up to" in forecast
    assert "may already have run out" in forecast
    assert "'en-GB'" in review and "'en-GB'" in forecast
