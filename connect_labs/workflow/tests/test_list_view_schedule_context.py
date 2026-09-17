from unittest import mock

import pytest

from connect_labs.labs.models import WorkflowSchedule
from connect_labs.users.models import User
from connect_labs.workflow.views import WorkflowListView


def _fake_def(def_id, template_type):
    return mock.Mock(id=def_id, template_type=template_type, pipeline_sources=[], name=f"W{def_id}")


def _fake_run(run_id, period_start, period_end, window_start=None, window_end=None):
    data = {}
    if window_start or window_end:
        data["state"] = {"window_start": window_start, "window_end": window_end}
    return mock.Mock(id=run_id, data=data, period_start=period_start, period_end=period_end)


@pytest.mark.django_db
def test_build_row_marks_schedulable_and_attaches_schedule():
    user = User.objects.create(username="alice")
    WorkflowSchedule.objects.create(
        definition_id=42,
        opportunity_id=1237,
        owner=user,
        definition_name="W42",
        cadence="weekly",
        hour=6,
        day_of_week=0,
    )
    view = WorkflowListView()

    schedules_by_def = {42: WorkflowSchedule.objects.get(definition_id=42)}
    with mock.patch(
        "connect_labs.workflow.views.template_supports_default_run",
        side_effect=lambda t: t == "program_audit_creator",
    ):
        row = view._build_workflow_row(_fake_def(42, "program_audit_creator"), [], mock.Mock(), {}, schedules_by_def)
        row_other = view._build_workflow_row(_fake_def(7, "performance_review"), [], mock.Mock(), {}, schedules_by_def)

    assert row["schedulable"] is True
    assert row["schedule"]["cadence"] == "weekly"
    assert row_other["schedulable"] is False
    assert row_other["schedule"] is None


def test_build_row_prefers_fired_window_over_frozen_shell_period():
    """A run's period_start/period_end are frozen at create_run time (often the
    generic '+Create Run' button's ISO-week default) and update_run_state
    deliberately never touches them once a batch actually fires with a
    different window (audit-window templates persist that into
    state.window_start/window_end instead — see _build_workflow_row's
    comment). The list view's displayed period should reflect what was
    actually audited, not the stale creation-time shell period."""
    view = WorkflowListView()
    run = _fake_run(13021, "2026-08-10", "2026-08-16", window_start="2026-08-12", window_end="2026-08-12")

    row = view._build_workflow_row(_fake_def(12705, "weekly_dual_track_audit"), [run], mock.Mock(), {}, {})

    fired = row["runs"][0]
    assert fired.display_period_start == "2026-08-12"
    assert fired.display_period_end == "2026-08-12"
    # The underlying stored period is untouched -- only the display field changes.
    assert fired.period_start == "2026-08-10"
    assert fired.period_end == "2026-08-16"


def test_build_row_falls_back_to_shell_period_when_nothing_has_fired():
    """A run with no fired batch yet (or a template that never writes
    state.window_start/window_end) shows its shell period unchanged."""
    view = WorkflowListView()
    run = _fake_run(13022, "2026-08-10", "2026-08-16")

    row = view._build_workflow_row(_fake_def(12705, "weekly_dual_track_audit"), [run], mock.Mock(), {}, {})

    fired = row["runs"][0]
    assert fired.display_period_start == "2026-08-10"
    assert fired.display_period_end == "2026-08-16"


def _schedule_keys_read_by_list_template():
    """Every ``workflow.schedule.<key>`` the list template reads.

    Derived from the template rather than hand-listed, so a key added there later is
    covered without anyone remembering to update this test.
    """
    import re
    from pathlib import Path

    import connect_labs

    path = Path(connect_labs.__file__).resolve().parent / "templates" / "workflow" / "list.html"
    raw = path.read_text(encoding="utf-8")
    return set(re.findall(r"workflow\.schedule\.(\w+)", raw))


@pytest.mark.django_db
def test_schedule_dict_carries_every_key_the_template_reads():
    """A key the template reads but the view omits renders as EMPTY, not as its default.

    Django returns ``string_if_invalid`` for a failed lookup and RETURNS EARLY -- the
    filters on the expression never run. So ``{{ workflow.schedule.interval_hours|
    default_if_none:6 }}`` emits ``interval_hours: ,`` inside the card's ``x-data``
    object literal, which is a JS syntax error. Alpine then cannot initialise that card
    at all: the title is an ``x-text`` inside a ``<template x-if>`` and renders as
    nothing, and Create Run / Copy / Share disappear with it. Only SCHEDULED cards break,
    because the ``{% else %}`` branch hardcodes literals.

    This happened in production on 2026-09-17: #1886 added ``interval_hours`` to the
    template's x-data but not to ``_build_workflow_row``'s dict.

    ``test_workflow_list_html_integrity`` cannot catch this by design -- it blanks out
    every ``{{ ... }}`` and checks only static text. This is the other half.
    """
    user = User.objects.create(username="carol")
    WorkflowSchedule.objects.create(
        definition_id=99,
        opportunity_id=1237,
        owner=user,
        definition_name="W99",
        cadence="weekly",
        hour=6,
        day_of_week=0,
    )
    view = WorkflowListView()
    schedules_by_def = {99: WorkflowSchedule.objects.get(definition_id=99)}

    with mock.patch(
        "connect_labs.workflow.views.template_supports_default_run",
        side_effect=lambda t: True,
    ):
        row = view._build_workflow_row(_fake_def(99, "program_audit_creator"), [], mock.Mock(), {}, schedules_by_def)

    provided = set(row["schedule"])
    missing = _schedule_keys_read_by_list_template() - provided
    assert not missing, (
        "workflow/list.html reads workflow.schedule.%s but _build_workflow_row does not "
        "provide it; it will render as an empty value and break the card's x-data" % ", ".join(sorted(missing))
    )


@pytest.mark.django_db
def test_a_scheduled_card_renders_no_empty_x_data_value():
    """The rendered symptom, asserted directly: no ``key: ,`` anywhere in the x-data.

    Guards the failure mode rather than the one key, so any future omission that renders
    empty is caught even if it never reaches the key-set check above.
    """
    import re
    from pathlib import Path

    from django.template import Context, Template

    import connect_labs

    user = User.objects.create(username="dave")
    WorkflowSchedule.objects.create(
        definition_id=101,
        opportunity_id=1237,
        owner=user,
        definition_name="W101",
        cadence="weekly",
        hour=6,
        day_of_week=0,
    )
    view = WorkflowListView()
    schedules_by_def = {101: WorkflowSchedule.objects.get(definition_id=101)}
    with mock.patch(
        "connect_labs.workflow.views.template_supports_default_run",
        side_effect=lambda t: True,
    ):
        row = view._build_workflow_row(_fake_def(101, "program_audit_creator"), [], mock.Mock(), {}, schedules_by_def)

    path = Path(connect_labs.__file__).resolve().parent / "templates" / "workflow" / "list.html"
    raw = path.read_text(encoding="utf-8")

    # The schedule block of the card's x-data, rendered exactly as the page renders it.
    block = re.search(r"scheduleForm:\s*(\{.*?\n\s*\}),", raw, re.S)
    assert block, "could not locate the scheduleForm block in list.html"
    rendered = Template(block.group(1)).render(Context({"workflow": row}))

    empties = re.findall(r"(\w+)\s*:\s*,", rendered)
    assert not empties, "empty x-data value(s) for {} -- this is a JS syntax error: {!r}".format(
        ", ".join(empties),
        rendered,
    )
