import json
from types import SimpleNamespace
from unittest import mock

import pytest
from django.urls import reverse

from connect_labs.labs.models import WorkflowSchedule
from connect_labs.users.models import User


@pytest.fixture
def logged_in(client):
    user = User.objects.create(username="alice")
    client.force_login(user)
    return user


@pytest.mark.django_db
def test_upsert_creates_schedule_for_schedulable_workflow(client, logged_in):
    session = client.session
    session["labs_oauth"] = {"access_token": "tok"}
    session.save()

    with (
        mock.patch("connect_labs.workflow.views._resolve_schedule_scope", return_value=(1237, None)),
        mock.patch("connect_labs.workflow.views.template_supports_default_run", return_value=True),
        mock.patch("connect_labs.workflow.views.WorkflowDataAccess") as DA,
    ):
        # ``mock.Mock(name=...)`` is reserved — it names the mock, not a ``.name``
        # attribute — so set it after construction (see test_delete_backup.py).
        definition = mock.Mock(id=42, template_type="program_audit_creator")
        definition.name = "Weekly Review"
        DA.return_value.get_definition.return_value = definition
        url = reverse("labs:workflow:api_schedule_upsert", args=[42])
        resp = client.post(
            url,
            data=json.dumps({"cadence": "weekly", "hour": 6, "day_of_week": 0}),
            content_type="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

    assert resp.status_code == 200
    sched = WorkflowSchedule.objects.get(definition_id=42, owner=logged_in)
    assert sched.cadence == "weekly"
    assert sched.next_run_at is not None
    assert sched.definition_name == "Weekly Review"


@pytest.mark.django_db
def test_upsert_rejects_non_schedulable(client, logged_in):
    session = client.session
    session["labs_oauth"] = {"access_token": "tok"}
    session.save()
    with (
        mock.patch("connect_labs.workflow.views._resolve_schedule_scope", return_value=(1237, None)),
        mock.patch("connect_labs.workflow.views.template_supports_default_run", return_value=False),
        mock.patch("connect_labs.workflow.views.WorkflowDataAccess") as DA,
    ):
        definition = mock.Mock(id=42, template_type="performance_review")
        definition.name = "Not schedulable"
        DA.return_value.get_definition.return_value = definition
        url = reverse("labs:workflow:api_schedule_upsert", args=[42])
        resp = client.post(
            url,
            data=json.dumps({"cadence": "daily", "hour": 6}),
            content_type="application/json",
        )
    assert resp.status_code == 400
    assert not WorkflowSchedule.objects.filter(definition_id=42).exists()


@pytest.mark.django_db
def test_upsert_non_numeric_day_of_week_is_400(client, logged_in):
    session = client.session
    session["labs_oauth"] = {"access_token": "tok"}
    session.save()
    with (
        mock.patch("connect_labs.workflow.views._resolve_schedule_scope", return_value=(1237, None)),
        mock.patch("connect_labs.workflow.views.template_supports_default_run", return_value=True),
        mock.patch("connect_labs.workflow.views.WorkflowDataAccess") as DA,
    ):
        definition = mock.Mock(id=42, template_type="program_audit_creator")
        definition.name = "Weekly Review"
        DA.return_value.get_definition.return_value = definition
        url = reverse("labs:workflow:api_schedule_upsert", args=[42])
        resp = client.post(
            url,
            data=json.dumps({"cadence": "weekly", "hour": 6, "day_of_week": "nope"}),
            content_type="application/json",
        )
    assert resp.status_code == 400
    assert not WorkflowSchedule.objects.filter(definition_id=42).exists()


@pytest.mark.django_db
def test_upsert_non_object_json_body_is_400(client, logged_in):
    session = client.session
    session["labs_oauth"] = {"access_token": "tok"}
    session.save()
    with (
        mock.patch("connect_labs.workflow.views._resolve_schedule_scope", return_value=(1237, None)),
        mock.patch("connect_labs.workflow.views.template_supports_default_run", return_value=True),
        mock.patch("connect_labs.workflow.views.WorkflowDataAccess"),
    ):
        url = reverse("labs:workflow:api_schedule_upsert", args=[42])
        resp = client.post(url, data=json.dumps([]), content_type="application/json")
    assert resp.status_code == 400
    assert not WorkflowSchedule.objects.filter(definition_id=42).exists()


@pytest.mark.django_db
def test_delete_removes_only_owners_schedule(client, logged_in):
    sched = WorkflowSchedule.objects.create(
        definition_id=42, opportunity_id=1237, owner=logged_in, definition_name="A", cadence="daily", hour=6
    )
    url = reverse("labs:workflow:api_schedule_delete", args=[sched.id])
    resp = client.post(url)
    assert resp.status_code == 200
    assert not WorkflowSchedule.objects.filter(pk=sched.pk).exists()


@pytest.mark.django_db
def test_toggle_flips_enabled(client, logged_in):
    sched = WorkflowSchedule.objects.create(
        definition_id=42,
        opportunity_id=1237,
        owner=logged_in,
        definition_name="A",
        cadence="daily",
        hour=6,
        enabled=True,
    )
    url = reverse("labs:workflow:api_schedule_toggle", args=[sched.id])
    resp = client.post(url)
    assert resp.status_code == 200
    sched.refresh_from_db()
    assert sched.enabled is False


# ── Template-declared schedule settings ───────────────────────────────────────
#
# run_default reads its settings from config.schedule_defaults, which had no editing
# surface: render code writes RUN state only, and no endpoint updated a definition's
# config. So a cap set on a dashboard applied to that one run while the schedule fired
# without it. These pin the surface that closes that, and pin that it stays a NARROW
# whitelist rather than a general config-write endpoint.

SCHED_OPTS = [
    {
        "key": "opportunity_ids",
        "type": "multi_int",
        "label": "Opportunities to audit",
        "choices": [{"value": 1236, "label": "EHA"}, {"value": 1487, "label": "PIPN"}],
    },
    {"key": "max_per_flw", "type": "int", "label": "Max photos per field worker", "min": 1, "max": 500},
]


def _post_defaults(client, defaults, options=SCHED_OPTS, existing=None):
    """POST a schedule with `defaults`, returning (response, update_mock)."""
    session = client.session
    session["labs_oauth"] = {"access_token": "tok"}
    session.save()
    with (
        mock.patch("connect_labs.workflow.views._resolve_schedule_scope", return_value=(1237, None)),
        mock.patch("connect_labs.workflow.views.template_supports_default_run", return_value=True),
        mock.patch("connect_labs.workflow.views.schedule_options_for_definition", return_value=options),
        mock.patch("connect_labs.workflow.views.WorkflowDataAccess") as DA,
    ):
        definition = mock.Mock(id=42, template_type="kmc_image_audit")
        definition.name = "KMC Image Audit"
        definition.data = {"config": {"schedule_defaults": existing or {}}}
        DA.return_value.get_definition.return_value = definition
        resp = client.post(
            reverse("labs:workflow:api_schedule_upsert", args=[42]),
            data=json.dumps({"cadence": "daily", "hour": 6, "defaults": defaults}),
            content_type="application/json",
        )
        return resp, DA.return_value.update_schedule_defaults


@pytest.mark.django_db
def test_declared_settings_are_saved_to_the_definition(client, logged_in):
    resp, update = _post_defaults(client, {"max_per_flw": 20, "opportunity_ids": [1487, 1236]})

    assert resp.status_code == 200
    update.assert_called_once()
    definition_id, values = update.call_args.args
    assert definition_id == 42
    assert values["max_per_flw"] == 20
    # Sorted and de-duplicated, so the stored order does not depend on click order.
    assert values["opportunity_ids"] == [1236, 1487]


@pytest.mark.django_db
def test_blank_integer_clears_the_setting_rather_than_capping_at_zero(client, logged_in):
    """run_default treats a missing/None cap as "no cap"; storing 0 would read as a cap
    of zero to anything that checks presence instead of truthiness."""
    resp, update = _post_defaults(client, {"max_per_flw": ""})

    assert resp.status_code == 200
    assert update.call_args.args[1]["max_per_flw"] is None


@pytest.mark.django_db
def test_a_key_the_template_did_not_declare_is_refused(client, logged_in):
    """This must never become a general config-write endpoint."""
    resp, update = _post_defaults(client, {"agent_override": {"1487": "evil"}})

    assert resp.status_code == 400
    assert "not settable" in resp.json()["error"]
    update.assert_not_called()


@pytest.mark.django_db
def test_an_out_of_range_integer_is_refused(client, logged_in):
    resp, update = _post_defaults(client, {"max_per_flw": 5000})

    assert resp.status_code == 400
    assert "between 1 and 500" in resp.json()["error"]
    update.assert_not_called()


@pytest.mark.django_db
def test_a_non_numeric_integer_is_refused(client, logged_in):
    resp, update = _post_defaults(client, {"max_per_flw": "twenty"})

    assert resp.status_code == 400
    update.assert_not_called()


@pytest.mark.django_db
def test_an_opportunity_outside_the_workflows_own_list_is_refused(client, logged_in):
    """The choices come from the definition's own config, so this also stops a schedule
    being pointed at an opportunity the workflow was never set up for."""
    resp, update = _post_defaults(client, {"opportunity_ids": [1236, 9999]})

    assert resp.status_code == 400
    assert "9999" in resp.json()["error"]
    update.assert_not_called()


@pytest.mark.django_db
def test_selecting_no_opportunities_is_refused(client, logged_in):
    """Otherwise the schedule fires nightly and audits nothing."""
    resp, update = _post_defaults(client, {"opportunity_ids": []})

    assert resp.status_code == 400
    assert "at least one" in resp.json()["error"]
    update.assert_not_called()


@pytest.mark.django_db
def test_defaults_are_optional_and_absent_ones_write_nothing(client, logged_in):
    """Templates declaring no options must behave exactly as before this existed."""
    session = client.session
    session["labs_oauth"] = {"access_token": "tok"}
    session.save()
    with (
        mock.patch("connect_labs.workflow.views._resolve_schedule_scope", return_value=(1237, None)),
        mock.patch("connect_labs.workflow.views.template_supports_default_run", return_value=True),
        mock.patch("connect_labs.workflow.views.schedule_options_for_definition", return_value=[]),
        mock.patch("connect_labs.workflow.views.WorkflowDataAccess") as DA,
    ):
        definition = mock.Mock(id=42, template_type="program_audit_creator")
        definition.name = "No options"
        DA.return_value.get_definition.return_value = definition
        resp = client.post(
            reverse("labs:workflow:api_schedule_upsert", args=[42]),
            data=json.dumps({"cadence": "daily", "hour": 6}),
            content_type="application/json",
        )

    assert resp.status_code == 200
    DA.return_value.update_schedule_defaults.assert_not_called()
    assert WorkflowSchedule.objects.filter(definition_id=42).exists()


@pytest.mark.django_db
def test_a_rejected_setting_does_not_create_a_schedule(client, logged_in):
    """Settings are saved before the schedule precisely so a bad value cannot leave a
    schedule running on the old ones."""
    resp, _ = _post_defaults(client, {"max_per_flw": 5000})

    assert resp.status_code == 400
    assert not WorkflowSchedule.objects.filter(definition_id=42).exists()


@pytest.mark.django_db
def test_a_failed_config_write_does_not_create_a_schedule(client, logged_in):
    session = client.session
    session["labs_oauth"] = {"access_token": "tok"}
    session.save()
    with (
        mock.patch("connect_labs.workflow.views._resolve_schedule_scope", return_value=(1237, None)),
        mock.patch("connect_labs.workflow.views.template_supports_default_run", return_value=True),
        mock.patch("connect_labs.workflow.views.schedule_options_for_definition", return_value=SCHED_OPTS),
        mock.patch("connect_labs.workflow.views.WorkflowDataAccess") as DA,
    ):
        definition = mock.Mock(id=42, template_type="kmc_image_audit")
        definition.name = "KMC Image Audit"
        DA.return_value.get_definition.return_value = definition
        DA.return_value.update_schedule_defaults.return_value = None  # upstream write failed
        resp = client.post(
            reverse("labs:workflow:api_schedule_upsert", args=[42]),
            data=json.dumps({"cadence": "daily", "hour": 6, "defaults": {"max_per_flw": 10}}),
            content_type="application/json",
        )

    assert resp.status_code == 502
    assert not WorkflowSchedule.objects.filter(definition_id=42).exists()


@pytest.mark.django_db
def test_defaults_must_be_an_object(client, logged_in):
    resp, update = _post_defaults(client, ["max_per_flw"])

    assert resp.status_code == 400
    update.assert_not_called()


# ── The dialog itself ─────────────────────────────────────────────────────────
#
# A Django syntax error or a malformed seed in list.html breaks the workflow list page
# for EVERY workflow, not only schedulable ones, and the view tests above all mock the
# options away. These render the real thing.


def test_the_real_list_template_still_compiles():
    """Cheap guard on the file the new dialog block lives in."""
    from django.template.loader import get_template

    assert get_template("workflow/list.html") is not None


def test_the_seed_is_valid_json_and_safe_to_inline():
    """It is emitted with |safe into a <script>, so it must parse and must not be able
    to break out of the script context."""
    options = [
        {"key": "opportunity_ids", "type": "multi_int", "value": [1236], "selected": [1236]},
        {"key": "max_per_flw", "type": "int", "value": None},
    ]
    seed = json.dumps(
        {
            o["key"]: (o["selected"] if o["type"] == "multi_int" else ("" if o["value"] is None else o["value"]))
            for o in options
        }
    )

    parsed = json.loads(seed)
    assert parsed["opportunity_ids"] == [1236]
    # An unset integer becomes "", which Alpine binds to an empty input and the endpoint
    # reads back as "clear this setting" — not 0, which would mean a cap of zero.
    assert parsed["max_per_flw"] == ""
    assert "</script" not in seed


def test_the_dialog_renders_choices_and_prefills_saved_values():
    from django.template import Context, Template

    from connect_labs.workflow.templates import schedule_options_for_definition

    definition = SimpleNamespace(
        template_type="kmc_image_audit",
        data={
            "config": {
                "opp_names": {"1487": "PIPN (V3)", "1790": "BERI (V3)"},
                "schedule_defaults": {"opportunity_ids": [1487], "max_per_flw": 20},
            }
        },
    )
    options = schedule_options_for_definition(definition)

    fragment = Template(
        """{% for opt in workflow.schedule_options %}{{ opt.label }}|
        {% if opt.type == 'multi_int' %}{% for c in opt.choices %}
        chk:{{ c.value }}:{{ c.label }}:"(scheduleDefaults['{{ opt.key|escapejs }}'] || []).includes({{ c.value }})"
        {% endfor %}{% else %}num:{{ opt.min }}-{{ opt.max }}{% endif %}{% endfor %}"""
    ).render(Context({"workflow": {"schedule_options": options}}))

    assert "Opportunities to audit|" in fragment
    assert "chk:1487:PIPN (V3)" in fragment
    # The unselected one must still render, or it could never be added.
    assert "chk:1790:BERI (V3)" in fragment
    assert "num:1-500" in fragment


# ── The seed must not break the x-data attribute ──────────────────────────────
#
# The card's Alpine state lives inside a double-quoted x-data="{...}" HTML attribute, and
# the seed is a JSON object literal. Rendered with |safe, its double quotes terminate the
# attribute early and every workflow card on the page goes dead — the same failure that
# bit bulk_assessment.html three times (see
# audit/tests/test_bulk_assessment_template_html_integrity.py). Autoescape is what keeps
# it safe here, so these pin that it is left ON.


def _x_data_values(html):
    from html.parser import HTMLParser

    class Collector(HTMLParser):
        def __init__(self):
            super().__init__()
            self.values = []

        def handle_starttag(self, tag, attrs):
            for name, value in attrs:
                if name == "x-data":
                    self.values.append(value)

    parser = Collector()
    parser.feed(html)
    return parser.values


def test_the_real_template_does_not_mark_the_seed_safe():
    """A direct guard on the file: adding |safe here is the regression."""
    from pathlib import Path

    import connect_labs

    path = Path(connect_labs.__file__).resolve().parent / "templates" / "workflow" / "list.html"
    source = path.read_text(encoding="utf-8")

    seed_lines = [ln for ln in source.splitlines() if "schedule_defaults_seed" in ln]
    assert seed_lines, "the seed is no longer rendered — this guard needs updating"
    for line in seed_lines:
        assert "|safe" not in line, f"seed must stay autoescaped inside x-data: {line.strip()}"


def test_the_seed_survives_html_parsing_inside_x_data():
    from django.template import Context, Template

    seed = json.dumps({"opportunity_ids": [1236, 1487], "max_per_flw": 20})
    # Exactly the filter chain list.html uses, inside a double-quoted x-data attribute.
    html = Template('<div x-data="{ scheduleDefaults: {{ seed|default:"{}" }}, open: false }"></div>').render(
        Context({"seed": seed})
    )

    values = _x_data_values(html)
    assert len(values) == 1, "the attribute was split — a quote terminated it early"
    # HTMLParser decodes &quot; the way a browser does, so what Alpine would evaluate is
    # the original JSON, intact and followed by the rest of the object.
    assert "scheduleDefaults: " + seed in values[0]
    assert "open: false" in values[0]


def test_marking_the_seed_safe_would_break_the_attribute():
    """Negative control: without this, the test above could pass for the wrong reason."""
    from django.template import Context, Template

    seed = json.dumps({"max_per_flw": 20})
    broken = Template('<div x-data="{ scheduleDefaults: {{ seed|safe }}, open: false }"></div>').render(
        Context({"seed": seed})
    )

    values = _x_data_values(broken)
    # The raw `"` closes x-data at the first JSON key, so the attribute is truncated and
    # `open: false` is never part of it.
    assert not values or "open: false" not in values[0]


# ── Findings from the adversarial audit ───────────────────────────────────────


def test_a_stored_out_of_range_value_does_not_block_saving_the_schedule():
    """The dialog posts EVERY option, so an out-of-range value already in config — set by
    hand, or stranded by a narrowed max — made cadence, hour and opportunities unsaveable
    on a field the user never touched. Seeding a legal value means they are never posting
    something they did not enter."""
    from connect_labs.workflow.views import _schedule_seed_value

    option = {"key": "max_per_flw", "type": "int", "min": 1, "max": 500, "value": 5000}
    assert _schedule_seed_value(option) == 500

    option["value"] = 0
    assert _schedule_seed_value(option) == 1


def test_the_seed_helper_itself_is_what_the_view_uses():
    """Reimplementing it in the test let a change from "" to 0 for an unset int pass."""
    from connect_labs.workflow.views import _schedule_seed_value

    assert _schedule_seed_value({"key": "a", "type": "int", "min": 1, "max": 9, "value": None}) == ""
    assert _schedule_seed_value({"key": "a", "type": "int", "min": 1, "max": 9, "value": "junk"}) == ""
    assert _schedule_seed_value({"key": "b", "type": "bool", "value": None}) is False
    assert _schedule_seed_value({"key": "c", "type": "multi_int", "selected": [2, 1]}) == [2, 1]
    assert _schedule_seed_value({"key": "c", "type": "multi_int", "selected": None}) == []


@pytest.mark.django_db
def test_no_selectable_choices_rejects_every_value(client, logged_in):
    """With the `unavailable` state this is now an EXPECTED condition, and the omission is
    client-side only — so any id would otherwise persist and later be dereferenced with
    the schedule owner's minted token."""
    options = [{"key": "opportunity_ids", "type": "multi_int", "label": "Opportunities", "choices": []}]
    resp, update = _post_defaults(client, {"opportunity_ids": [999]}, options=options)

    assert resp.status_code == 400
    assert "999" in resp.json()["error"]
    update.assert_not_called()


@pytest.mark.django_db
def test_an_upstream_write_failure_returns_json_not_an_html_500(client, logged_in):
    """update_record RAISES rather than returning None, so without an except the caller
    got Django's HTML error page into `await response.json()` — surfacing as
    "Unexpected token '<'" instead of the intended message."""
    from connect_labs.labs.integrations.connect.api_client import LabsAPIError

    session = client.session
    session["labs_oauth"] = {"access_token": "tok"}
    session.save()
    with (
        mock.patch("connect_labs.workflow.views._resolve_schedule_scope", return_value=(1237, None)),
        mock.patch("connect_labs.workflow.views.template_supports_default_run", return_value=True),
        mock.patch("connect_labs.workflow.views.schedule_options_for_definition", return_value=SCHED_OPTS),
        mock.patch("connect_labs.workflow.views.WorkflowDataAccess") as DA,
    ):
        definition = mock.Mock(id=42, template_type="kmc_image_audit")
        definition.name = "KMC Image Audit"
        DA.return_value.get_definition.return_value = definition
        DA.return_value.update_schedule_defaults.side_effect = LabsAPIError("upstream 500")
        resp = client.post(
            reverse("labs:workflow:api_schedule_upsert", args=[42]),
            data=json.dumps({"cadence": "daily", "hour": 6, "defaults": {"max_per_flw": 10}}),
            content_type="application/json",
        )

    assert resp.status_code == 502
    assert resp["Content-Type"].startswith("application/json")
    assert "schedule settings" in resp.json()["error"]
    assert not WorkflowSchedule.objects.filter(definition_id=42).exists()


@pytest.mark.parametrize(
    "config",
    [
        {"schedule_defaults": "not-a-dict"},
        {"schedule_defaults": 7},
        {"schedule_defaults": {"opportunity_ids": "1487"}},
        {"schedule_defaults": {"opportunity_ids": 1487}},
        {"opp_names": "not-a-dict", "schedule_defaults": {}},
        "config-itself-not-a-dict",
    ],
)
def test_a_malformed_config_cannot_blank_the_workflow_list_page(config):
    """This runs while building the LIST page, so an exception here is swallowed by the
    page's blanket handler and renders ZERO workflows for everyone in the opportunity —
    including the page needed to fix it. The docs invite hand-patching this key."""
    from connect_labs.workflow.templates import schedule_options_for_definition

    definition = SimpleNamespace(template_type="kmc_image_audit", data={"config": config})
    options = schedule_options_for_definition(definition)

    assert isinstance(options, list)
    for opt in options:
        if opt["type"] == "multi_int":
            # Never the digits of a string, which is what iterating "1487" would give.
            assert opt["selected"] == [] or all(isinstance(v, int) for v in opt["selected"])
            assert isinstance(opt["choices"], list)
