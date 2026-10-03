"""synthetic_clone_opp verbatim_paths: a copy of real values, gated on raw-visit access (connect-labs#2150).

One test (or group) per rule in the issue:
1. the copy happens only when the caller's own token reads the source's raw visits;
2. the clone is visible to its creator alone, and widens only to named people who pass
   the same check -- never by domain;
3. each copy is audited as a bulk-PHI EXPORT;
4. the opp is never generated data, and the report says it holds real values;
5. the values reach the generated fixture only, never the profile bundle.
"""

import json

import httpx
import pytest

from connect_labs.audit_trail.models import Action, AuditEvent
from connect_labs.labs.synthetic import clone_from_prod, gdrive, tasks, verbatim
from connect_labs.labs.synthetic.access import user_can_access_labs_only_program
from connect_labs.labs.synthetic.models import SyntheticOpportunity
from connect_labs.labs.synthetic.provenance import is_generated
from connect_labs.labs.synthetic.tests.test_bundle import _FakeDrive
from connect_labs.mcp.tool_registry import MCPToolError, get_tool
from connect_labs.users.models import User

pytestmark = pytest.mark.django_db

SOURCE = 2190
PATHS = ["form.village", "form.waterpoint_name"]
# "ZQX" marks every real value, so a leak anywhere is one substring search away.
SOURCE_VISITS = [
    {
        "username": user,
        "visit_date": f"2026-05-{day:02d}",
        "form_json": {"form": {"w": 10.0 + i, "village": f"Village-{user}-{i}-ZQX", "waterpoint_name": f"WP-{i}-ZQX"}},
    }
    for i, (user, day) in enumerate(
        [("a", 4), ("a", 5), ("a", 6), ("a", 11), ("a", 12), ("a", 13), ("b", 4), ("b", 8), ("b", 11), ("b", 14)]
    )
]
EXPORTS = {
    "": {"id": SOURCE, "name": "Chlorine dispensers"},
    "user_visits": SOURCE_VISITS,
    "user_data": [],
    "app_structure": {"learn_app": None, "deliver_app": {"modules": []}},
}


@pytest.fixture
def caller():
    return User.objects.create(username="owner", email="owner@dimagi.com")


@pytest.fixture
def drive(monkeypatch, settings):
    settings.LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID = "parent"
    fake = _FakeDrive()
    monkeypatch.setattr(gdrive, "DriveClient", lambda: fake)
    monkeypatch.setattr(tasks, "_progress_reporter", lambda task, **ctx: lambda *a, **k: None)
    monkeypatch.setattr(clone_from_prod, "_fetch_endpoint", lambda base, opp, key, tok: EXPORTS[key])
    return fake


def _gate(monkeypatch, *, readable: bool):
    def fetch(base, opp, key, tok):
        assert key == "user_visits" and tok == "caller-token"  # the CALLER's token, nobody else's
        if not readable:
            request = httpx.Request("GET", f"https://connect/export/opportunity/{opp}/user_visits/")
            raise httpx.HTTPStatusError("404", request=request, response=httpx.Response(404, request=request))
        return SOURCE_VISITS

    monkeypatch.setattr(verbatim, "_fetch_endpoint", fetch)


def _clone(caller, *, paths=PATHS, restricted=False):
    from django.core.cache import cache

    cache.clear()
    return tasks.run_synthetic_clone_opp.run(
        source_opportunity_ids=[SOURCE],
        program_name="Chlorine clone",
        case_timelines=True,
        oauth_token="caller-token",
        user_id=caller.pk,
        restricted=restricted,
        verbatim_paths=paths,
    )


def _files(fake, *, folder_suffix=None):
    """{filename: content} for every file under folders whose name ends with folder_suffix."""
    out = {}
    for parent, name, content in fake.files.values():
        folder_name = fake.folders[parent][0]
        if folder_suffix is None or folder_name.endswith(folder_suffix):
            out.setdefault(name, []).append(content)
    return out


# ---------------------------------------------------------------------------
# Rule 1 -- the gate is the caller's own raw-visit read, at copy time
# ---------------------------------------------------------------------------


def test_without_row_access_the_verbatim_part_is_refused_and_the_statistical_clone_still_runs(
    caller, drive, monkeypatch
):
    _gate(monkeypatch, readable=False)
    out = _clone(caller)

    (clone,) = out["clones"]
    assert "could not read the raw visits of opportunity 2190" in clone["verbatim"]["refused"]
    row = SyntheticOpportunity.objects.get(opportunity_id=clone["opportunity_id"])
    assert row.verbatim_paths == []  # an ordinary statistical clone
    assert row.allowed_domains == ["@dimagi.com", "@dimagi-ai.com"]
    assert not any(b"ZQX" in c for contents in _files(drive).values() for c in contents)
    # The refused read is on the record as an attempted export.
    event = AuditEvent.objects.get(action=Action.EXPORT, opportunity_id=SOURCE)
    assert event.outcome == "failure" and event.user_id == caller.pk


def test_a_session_that_may_not_see_visit_data_copies_nothing(caller, drive, monkeypatch):
    monkeypatch.setattr(verbatim, "_fetch_endpoint", lambda *a: pytest.fail("restricted must not read rows"))
    out = _clone(caller, restricted=True)
    (clone,) = out["clones"]
    assert "may not see user visit data" in clone["verbatim"]["refused"]
    assert SyntheticOpportunity.objects.get(opportunity_id=clone["opportunity_id"]).verbatim_paths == []


def test_there_is_no_way_to_assert_fields_are_safe():
    tool = get_tool("synthetic_clone_opp")
    props = tool.input_schema["properties"]
    assert set(props) == {"source_opportunity_ids", "program_name", "case_timelines", "verbatim_paths"}
    assert tool.input_schema["additionalProperties"] is False


# ---------------------------------------------------------------------------
# Rules 2-5 on a copy that passed the gate
# ---------------------------------------------------------------------------


@pytest.fixture
def copied(caller, drive, monkeypatch):
    _gate(monkeypatch, readable=True)
    out = _clone(caller)
    (clone,) = out["clones"]
    return SyntheticOpportunity.objects.get(opportunity_id=clone["opportunity_id"]), clone, drive


def test_values_are_copied_into_the_fixture_paired_by_worker_and_date(copied):
    row, clone, fake = copied
    assert clone["verbatim"]["paths"] == PATHS and clone["verbatim"]["rows_copied"] > 0
    (visits_json,) = _files(fake, folder_suffix="-verbatim")["user_visits.json"]
    visits = json.loads(visits_json)
    # flw_001 is the busiest source worker ("a"): its visits, in date order, carry a's values in date order.
    a_values = [v["form_json"]["form"]["village"] for v in SOURCE_VISITS if v["username"] == "a"]
    flw1 = sorted((v for v in visits if v["username"] == "flw_001"), key=lambda v: v["visit_date"])
    copied_values = [v["form_json"]["form"].get("village") for v in flw1][: len(a_values)]
    assert copied_values == a_values[: len(copied_values)] and copied_values
    assert all(str(v["form_json"]["form"].get("waterpoint_name", "")).endswith("ZQX") for v in flw1[: len(a_values)])


def test_rule5_the_profile_bundle_carries_no_copied_value(copied):
    _row, _clone, fake = copied
    bundle_files = {"manifest.yaml", "app_structure.json", "opportunity.json"}
    for name, contents in _files(fake).items():
        if name in bundle_files:
            assert not any(b"ZQX" in c for c in contents), f"real value leaked into bundle file {name}"
    # ...and the fixture is the one place they are.
    assert any(b"ZQX" in c for c in _files(fake, folder_suffix="-verbatim")["user_visits.json"])


def test_rule2_the_copy_is_visible_to_its_creator_alone(copied, caller):
    row, _clone, _fake = copied
    assert row.verbatim_paths == PATHS and row.allowed_domains == [] and row.allowed_emails == []
    caller.view_synthetic_opps = True
    assert row.is_accessible_to(caller) and row.is_visible_to(caller)
    staff = User.objects.create(username="staff", email="staff@dimagi.com", view_synthetic_opps=True)
    assert not row.is_accessible_to(staff) and not row.is_visible_to(staff)
    # Nor through the program it is filed under.
    assert not user_can_access_labs_only_program(staff, row.program_id)
    assert user_can_access_labs_only_program(caller, row.program_id)


def test_rule2_widening_by_domain_is_refused(copied, caller):
    row, _clone, _fake = copied
    with pytest.raises(MCPToolError) as e:
        get_tool("synthetic_set_allowed_domains").handler(
            user=caller, opportunity_id=row.opportunity_id, allowed_domains=["@dimagi.com"]
        )
    assert e.value.code == "PERMISSION_DENIED" and "individual address" in str(e.value)
    row.refresh_from_db()
    assert row.allowed_emails == [] and row.allowed_domains == []


def test_rule2_only_the_creator_may_widen_it(copied):
    row, _clone, _fake = copied
    staff = User.objects.create(username="staff", email="staff@dimagi.com")
    with pytest.raises(MCPToolError):
        get_tool("synthetic_set_allowed_domains").handler(
            user=staff, opportunity_id=row.opportunity_id, allowed_domains=["staff@dimagi.com"]
        )


def test_rule2_a_named_person_is_added_only_if_they_can_read_the_source(copied, caller, monkeypatch):
    row, _clone, _fake = copied
    reader = User.objects.create(username="reader", email="reader@partner.org", view_synthetic_opps=True)
    outsider = User.objects.create(username="outsider", email="outsider@partner.org")
    checked = []

    def readable(user, opp_id, *, base_url):
        checked.append((user.username, opp_id))
        return None if user == reader else "cannot read the raw visits of opportunity 2190 (ExportAPIError)"

    monkeypatch.setattr(verbatim, "raw_visits_readable_reason", readable)
    tool = get_tool("synthetic_set_allowed_domains")

    with pytest.raises(MCPToolError) as e:
        tool.handler(
            user=caller,
            opportunity_id=row.opportunity_id,
            allowed_domains=["reader@partner.org", "outsider@partner.org"],
        )
    assert "outsider@partner.org" in str(e.value)
    row.refresh_from_db()
    assert row.allowed_emails == []  # all or nothing

    out = tool.handler(user=caller, opportunity_id=row.opportunity_id, allowed_domains=["Reader@partner.org"])
    assert out["allowed_emails"] == ["reader@partner.org"]
    assert ("reader", SOURCE) in checked
    row.refresh_from_db()
    assert row.is_accessible_to(reader) and row.is_visible_to(reader)
    assert not row.is_accessible_to(outsider)
    # A suffix of an allowed address is not that address.
    lookalike = User.objects.create(username="look", email="xreader@partner.org")
    assert not row.is_accessible_to(lookalike)


def test_rule2_the_per_person_check_reads_with_that_persons_own_token(monkeypatch, settings):
    from connect_labs.labs import connect_tokens
    from connect_labs.labs.integrations.connect import export_client

    person = User.objects.create(username="p", email="p@partner.org")
    monkeypatch.setattr(connect_tokens, "get_valid_access_token", lambda u: f"token-of-{u.username}")
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["Authorization"]
        seen["url"] = str(request.url)
        return httpx.Response(404, request=request)

    real_client = httpx.Client
    monkeypatch.setattr(
        export_client.httpx,
        "Client",
        lambda **kw: real_client(**{**kw, "transport": httpx.MockTransport(handler)}),
    )
    reason = verbatim.raw_visits_readable_reason(person, SOURCE, base_url="https://connect")
    assert reason and "2190" in reason
    assert seen["auth"] == "Bearer token-of-p" and "/export/opportunity/2190/user_visits/" in seen["url"]


def test_rule2_the_copy_cannot_be_re_exposed_through_its_folder(copied, caller):
    row, _clone, _fake = copied
    with pytest.raises(MCPToolError) as e:
        get_tool("synthetic_clone_to_labs_only").handler(user=caller, source_opportunity_id=row.opportunity_id)
    assert "real values" in str(e.value)
    with pytest.raises(MCPToolError):
        get_tool("synthetic_create_labs_only").handler(user=caller, label="x", gdrive_folder_id=row.gdrive_folder_id)


# ---------------------------------------------------------------------------
# Rule 3 -- audited as a bulk-PHI export
# ---------------------------------------------------------------------------


def test_rule3_the_copy_is_recorded_as_an_export(copied, caller):
    row, clone, _fake = copied
    event = AuditEvent.objects.get(action=Action.EXPORT, opportunity_id=SOURCE, outcome="success")
    assert event.user_id == caller.pk
    assert event.record_count == clone["verbatim"]["rows_copied"]
    assert event.metadata["verbatim_paths"] == PATHS
    assert event.metadata["copied_into_opportunity_id"] == row.opportunity_id
    assert event.metadata["endpoint"] == f"/export/opportunity/{SOURCE}/user_visits/"


# ---------------------------------------------------------------------------
# Rule 4 -- labelled honestly
# ---------------------------------------------------------------------------


def test_rule4_the_copy_is_never_generated_data(copied):
    row, _clone, _fake = copied
    assert row.generated_folder_id is None and not is_generated(row)
    assert row.label.startswith(f"[Real values from opp {SOURCE}]")
    # Its folder is not named like generated output, so the backfill cannot mark it.
    from connect_labs.labs.synthetic.generator.io.uploader import GENERATED_FOLDER_NAME_RE

    assert not GENERATED_FOLDER_NAME_RE.match(_fake.folders[row.gdrive_folder_id][0])


def test_rule4_the_report_says_real_values_instead_of_synthetic(copied):
    from connect_labs.workflow.snapshot_builders import _data_provenance_meta

    row, _clone, _fake = copied
    meta = _data_provenance_meta([row.opportunity_id])
    assert meta["synthetic"] is False
    assert meta["real_values"] == [
        {"opportunity_id": row.opportunity_id, "source_opportunity_id": SOURCE, "fields": PATHS}
    ]


def test_rule4_a_plain_clone_still_says_synthetic(caller, drive, monkeypatch):
    from connect_labs.workflow.snapshot_builders import _data_provenance_meta

    (clone,) = _clone(caller, paths=[])["clones"]
    assert _data_provenance_meta([clone["opportunity_id"]]) == {"synthetic": True}


def test_rule4_the_renders_show_real_values_before_any_synthetic_claim():
    from pathlib import Path

    root = Path(__file__).parents[3] / "workflow" / "templates"
    report = (root / "indicator_report_render.js").read_text()
    assert report.index("meta.real_values") < report.index("Built on synthetic data")
    assert "Contains real values copied from opportunity " in report
    kmc = (root / "kmc_programme_metrics_render.js").read_text()
    assert "P.meta.real_values" in kmc and "Contains real values copied from opportunity " in kmc


def test_paths_are_validated():
    assert verbatim.normalise_paths(["form.village", " form.village "]) == ["form.village"]
    with pytest.raises(ValueError):
        verbatim.normalise_paths(["form..village"])
