"""`workflow_sandbox_scan`: which workflows' render code would break in the sandbox frame.

Spec: docs/superpowers/specs/2026-10-12-page-sandbox-design.md, Phase 2, "Rollout".
THIS REPOSITORY IS PUBLIC. Every name here is invented.
"""

import json
import re
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.test import override_settings

from connect_labs.labs.synthetic.models import LabsLocalRecord
from connect_labs.workflow import sandbox_routes
from connect_labs.workflow.sandbox_scan import representative, resolve_address, scan_render_code, strip_comments
from connect_labs.workflow.sandbox_scan_sources import Coverage, connect_targets, local_targets, template_targets
from connect_labs.workflow.templates import TEMPLATES

ROUTES_TS = Path(__file__).resolve().parents[3] / "components" / "workflow" / "sandbox" / "routes.ts"


def _ids(code, **kw):
    return {(f.pattern, f.severity) for f in scan_render_code(code, **kw)}


def _wrap(body):
    return "function WorkflowUI({ actions, view }) {\n" + body + "\n  return <div/>;\n}\n"


# --- each pattern ------------------------------------------------------------------

BREAKING = [
    ("window.top.postMessage('x', '*');", "top_or_parent"),
    ("parent.document.title = 'x';", "top_or_parent"),
    ("if (window.confirm('Delete?')) go();", "confirm"),
    ("if (confirm('Delete?')) go();", "confirm"),
    ("const n = prompt('Name?');", "prompt"),
    ("const x = new XMLHttpRequest();", "xhr"),
    ("const es = new EventSource('/labs/workflow/api/1/render-code/');", "fetch_refused"),
    ("import('https://cdn.example.org/m.js').then(m => m.go());", "dynamic_import"),
    ("const f = <form action='/audit/api/audit/create/' method='post'></form>;", "form_action"),
    ("$.ajax({url: '/audit/api/ai-agents/'});", "jquery_ajax"),
    ("fetch('/labs/workflow/api/1/render-code/', {method: 'POST'});", "fetch_refused"),
    ("fetch('/labs/mcp/tokens/create/', {method: 'POST'});", "fetch_refused"),
    ("fetch('https://collector.example.org/x');", "fetch_external"),
]


@pytest.mark.parametrize("line,pattern", BREAKING)
def test_each_breaking_pattern_is_found(line, pattern):
    assert (pattern, "breaks") in _ids(_wrap("  " + line))


CHANGING = [
    ("const f = <form onSubmit={save}><button/></form>;", "form_submit"),
]


@pytest.mark.parametrize("line,pattern", CHANGING)
def test_each_changing_pattern_is_found(line, pattern):
    assert (pattern, "changes") in _ids(_wrap("  " + line))


# What the frame compiles or shims into something that works (rewrite.ts, frameShims.ts).
WORKS = [
    ("window.location = '/audit/';", "location_assign"),
    ("window.location.href = url;", "location_assign"),
    ("location.href = '/tasks/';", "location_assign"),
    ("document.location = '/x/';", "location_assign"),
    ("window.location.assign('/x/');", "location_assign"),
    ("location.replace('/x/');", "location_assign"),
    ("top.location = '/x/';", "location_assign"),
    ("window.top.location.href = '/x/';", "location_assign"),
    ("window.location.reload();", "location_reload"),
    ("const es = new EventSource('/labs/workflow/api/job/a/status/');", "event_source"),
    ("const c = document.cookie;", "document_cookie"),
    ("alert('Saved');", "alert"),
    ("window.alert('Saved');", "alert"),
    # Kept by the host for this workflow (host.ts scopedStorage).
    ("localStorage.setItem('k', 'v');", "storage"),
    ("sessionStorage.getItem('k');", "storage"),
]


@pytest.mark.parametrize("line,pattern", WORKS)
def test_what_the_frame_rewrites_or_shims_is_info(line, pattern):
    ids = _ids(_wrap("  " + line))
    assert (pattern, "info") in ids
    assert not {p for p, s in ids if s == "breaks"}


def test_a_property_named_location_is_not_the_address():
    assert scan_render_code(_wrap("  row.location = 'Kano';\n  const v = item.location.href;")) == []


@pytest.mark.parametrize(
    "body",
    [
        "  const del = async (s) => {\n    if (!window.confirm('Delete?')) return;\n  };",
        "  async function del() { if (confirm('Delete?')) go(); }",
        "  const b = <button onClick={() => { if (confirm('Sure?')) del(); }}>x</button>;",
        "  h('button', { onClick: function () { var ok = window.confirm('Discard?'); if (ok) d(); } });",
        "  el.addEventListener('click', function () { if (confirm('a')) b(); });",
        '  const go = async () => { try { if (confirm("it\'s {fine}")) x(); } catch (e) {} };',
    ],
)
def test_a_confirm_the_compile_makes_wait_is_info(body):
    ids = _ids(_wrap(body))
    assert ("confirm", "info") in ids and ("confirm", "breaks") not in ids


@pytest.mark.parametrize(
    "body",
    [
        "  function check() { return confirm('ok?'); }",
        "  const del = async () => { items.forEach(function (i) { if (confirm(i)) drop(i); }); };",
        "  if (cond) { if (confirm('x')) go(); }",
        "  const a = <a onClick={(e) => { if (!confirm('Leave?')) e.preventDefault(); }}>x</a>;",
        "  const a = <a onClick={({ target }) => { if (confirm('Go?')) go(target); }}>x</a>;",
    ],
)
def test_a_confirm_it_cannot_make_wait_still_breaks(body):
    assert ("confirm", "breaks") in _ids(_wrap(body))


def test_a_later_constant_part_of_an_address_is_resolved():
    code = _wrap(
        "  var KEYS = '/runs/history/?keys=a,b';\n"
        "  fetch('/labs/workflow/api/' + sv.id + KEYS, { credentials: 'same-origin' });"
    )
    assert scan_render_code(code) == []


def test_a_synchronous_xhr_is_named():
    code = _wrap("  const x = new XMLHttpRequest();\n  x.open('GET', '/audit/api/ai-agents/', false);")
    assert ("xhr_sync", "breaks") in _ids(code)


def test_info_patterns():
    code = _wrap(
        "  const t = document.querySelector('[name=csrfmiddlewaretoken]').value;\n"
        "  const g = eval(text);\n"
        "  fetch(buildUrl(a, b));"
    )
    assert {("csrf_read", "info"), ("eval", "info"), ("fetch_dynamic", "info")} <= _ids(code)


def test_clean_render_code_is_not_flagged():
    code = _wrap(
        "  // window.location = '/never/'; confirm('in a comment')\n"
        "  /* alert('also a comment') */\n"
        "  const location = props.where;\n"
        "  const ok = await view.confirm('Delete?');\n"
        "  if (ok) await actions.navigate('/audit/');\n"
        "  const url = 'http://example.org/not-a-comment';\n"
        "  fetch('/labs/workflow/api/' + instance.definition_id + '/pipeline-rows/?alias=' + a);\n"
        "  fetch(`/audit/api/opportunity/${oppId}/image-questions/`);\n"
        "  fetch('/labs/workflow/api/run/' + runId + '/state/', {method: 'POST', body: '{}'});\n"
        "  fetch('/labs/workflow/api/' + defId + '/runs/history/' + qs);"
    )
    assert scan_render_code(code) == []


def test_a_checked_seed_template_scans_without_a_break():
    findings = scan_render_code(TEMPLATES["performance_review"]["render_code"])
    assert not [f for f in findings if f.severity == "breaks"]


def test_a_guarded_fallback_is_info_not_breaks():
    code = _wrap(
        "  const go = (url) => {\n"
        "    if (actions && actions.navigate) actions.navigate(url);\n"
        "    else window.location.href = url;\n"
        "  };\n"
        "  const ask = (t) => view && view.confirm ? view.confirm(t) : Promise.resolve(window.confirm(t));"
    )
    ids = _ids(code)
    assert ("guarded_fallback", "info") in ids
    assert not {p for p, s in ids if s == "breaks"}


def test_an_event_source_is_checked_like_a_fetch():
    allowed = _wrap("  const es = new EventSource('/labs/workflow/api/' + d + '/pipeline-rows/stream/?alias=x');")
    assert _ids(allowed) == {("event_source", "info")}
    dynamic = _wrap("  if (typeof EventSource !== 'undefined') { const es = new EventSource(u); }")
    assert _ids(dynamic) == {("event_source", "info")}
    [refused] = scan_render_code(_wrap("  new EventSource('/labs/workflow/api/1/render-code/');"))
    assert refused.pattern == "fetch_refused" and refused.detail.startswith("EventSource: GET")
    # The deprecated MBW dashboard's own stream is on the table.
    assert _ids(_wrap("  new EventSource('/custom_analysis/mbw_monitoring/stream/?x=1');")) == {
        ("event_source", "info")
    }


def test_line_numbers_are_those_of_the_render_code():
    code = "function W() {\n  /* a\n  b */\n  confirm('x');\n}\n"
    [finding] = scan_render_code(code)
    assert (finding.pattern, finding.line) == ("confirm", 4)


def test_strip_comments_keeps_strings_and_lines():
    code = "a('http://x'); // gone\n/* x\ny */ b(`//kept`);"
    out = strip_comments(code)
    assert "http://x" in out and "//kept" in out and "gone" not in out
    assert out.count("\n") == code.count("\n")


# --- fetch addresses ---------------------------------------------------------------


def test_concatenated_and_template_addresses_are_normalised():
    code = "const base = '/audit/api/';\nconst u = base + id + '/bulk-data/';\n"
    assert representative(resolve_address("u", code, len(code))) == "/audit/api/1/bulk-data/"
    assert representative(resolve_address("`/tasks/api/${t.id}/`", "", 0)) == "/tasks/api/1/"
    assert resolve_address("makeUrl(x)", "", 0) is None


def test_a_query_built_after_the_path_is_dropped():
    assert (
        representative("/labs/workflow/api/\x00/runs/history/\x00\x00keys=\x00")
        == "/labs/workflow/api/1/runs/history/"
    )


def test_a_method_the_route_does_not_take_is_refused():
    # pipeline-query is POST only
    code = _wrap("  fetch('/labs/workflow/api/' + d + '/pipeline-query/');")
    [finding] = [f for f in scan_render_code(code) if f.pattern == "fetch_refused"]
    assert "GET" in finding.detail and "only POST" in finding.detail


def test_a_run_endpoint_on_a_page_is_refused():
    code = _wrap("  fetch('/labs/workflow/api/run/' + r + '/flags/', {method: 'POST'});")
    assert ("fetch_needs_run", "breaks") in _ids(code, is_page=True)
    assert not _ids(code, is_page=False)


# --- the route table mirror --------------------------------------------------------


def _js_unescape(s):
    return re.sub(r"\\(.)", r"\1", s)


def parse_routes_ts(source):
    """(methods, pattern, gate) per route and FETCH_DENIED, read from routes.ts itself."""
    consts = {
        m.group(1): _js_unescape(m.group(2)) for m in re.finditer(r"^const (\w+) = '((?:[^'\\]|\\.)*)';", source, re.M)
    }
    arrays = {
        m.group(1): tuple(re.findall(r"'([^']*)'", m.group(2)))
        for m in re.finditer(r"^const (\w+) = \[([^\]]*)\] as const;", source, re.M)
    }
    block = re.search(r"^export const ROUTES[^=]*= \[(.*?)^\];", source, re.M | re.S).group(1)
    objects, depth, start = [], 0, 0
    for i, ch in enumerate(block):
        if ch == "{":
            start = i if depth == 0 else start
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                objects.append(block[start : i + 1])
    routes = []
    for obj in objects:
        methods = arrays[re.search(r"methods:\s*(\w+)", obj).group(1)]
        gate = re.search(r"gate:\s*'(\w+)'", obj).group(1)
        template = re.search(r"path:\s*p\(\s*`((?:[^`\\]|\\.)*)`\s*,?\s*\)", obj, re.S).group(1)
        source_re = re.sub(r"\$\{(\w+)\}", lambda m: consts[m.group(1)], _js_unescape(template))
        routes.append((methods, "^" + source_re + "$", gate))
    denied = re.search(r"^export const FETCH_DENIED = \[(.*?)\];", source, re.M | re.S).group(1)
    return routes, tuple(re.findall(r"'([^']*)'", denied))


def test_route_mirror_matches_routes_ts():
    routes, denied = parse_routes_ts(ROUTES_TS.read_text())
    assert len(routes) > 30  # the parser found the table, not a fragment of it
    mirror = [(r.methods, r.path, r.gate) for r in sandbox_routes.ROUTES]
    assert mirror == routes, "components/workflow/sandbox/routes.ts changed: update workflow/sandbox_routes.py"
    assert sandbox_routes.FETCH_DENIED == denied


def test_the_mirror_parser_notices_a_drift():
    source = ROUTES_TS.read_text().replace("`/audit/api/ai-agents/`", "`/audit/api/ai-agents/v2/`")
    routes, _ = parse_routes_ts(source)
    assert [(r.methods, r.path, r.gate) for r in sandbox_routes.ROUTES] != routes


def test_mirror_patterns_compile_and_answer():
    assert sandbox_routes.check_path("/audit/api/ai-agents/").allowed
    assert not sandbox_routes.check_path("/o/token/").allowed
    assert sandbox_routes.check_path("/labs/workflow/api/run/5/flags/", "POST").needs_run
    # Another run's state is an effect a page may ask for too (declared reads bind it).
    assert not sandbox_routes.check_path("/labs/workflow/api/run/5/state/", "POST").needs_run


# --- enumeration -------------------------------------------------------------------

STORED_BREAKS = "function W(){ window.parent.postMessage('x', '*'); return null; }"
PUBLISHED_BREAKS = "function W(){ if (confirm('x')) {} return null; }"
STORED_CLEAN = "function W(){ return null; }"


def _local(type_, data, **scope):
    return LabsLocalRecord.objects.create(experiment="workflow", type=type_, data=data, **scope)


@pytest.fixture
def local_records():
    a = _local("workflow_definition", {"name": "Field review", "config": {}}, opportunity_id=10001)
    _local("workflow_render_code", {"definition_id": a.id, "component_code": STORED_CLEAN}, opportunity_id=10001)
    _local("workflow_render_code", {"definition_id": a.id, "component_code": STORED_BREAKS}, opportunity_id=10001)
    b = _local(
        "workflow_definition",
        {"name": "Follows a checked template", "config": {"templateType": "performance_review"},
         "render_source": {"template": "performance_review"}},
        program_id=10500,
    )  # fmt: skip
    _local("workflow_render_code", {"definition_id": b.id, "component_code": STORED_BREAKS}, program_id=10500)
    template = _local("workflow_definition", {"name": "Report template", "config": {}}, program_id=10500)
    version = _local(
        "workflow_template_version", {"render_code": PUBLISHED_BREAKS}, program_id=10500, labs_record_id=template.id
    )
    template.data = {
        **template.data,
        "template_workflow": {
            "template_type": "page_blank",
            "published": {"version": 3, "record_id": version.id, "config": {"sandbox": True}},
        },
    }
    template.save()
    c = _local(
        "workflow_definition",
        {"name": "Follows the report template", "config": {},
         "render_source": {"workflow": template.id, "program_id": 10500}},
        opportunity_id=10002,
    )  # fmt: skip
    _local("workflow_render_code", {"definition_id": c.id, "component_code": STORED_CLEAN}, opportunity_id=10002)
    return SimpleNamespace(stored=a, follower=b, template=template, tw_follower=c)


@pytest.mark.django_db
def test_local_records_of_every_owner_with_their_effective_render(local_records):
    with override_settings(LABS_WORKFLOW_SANDBOX="opt_in"):
        targets = {t.id: t for t in local_targets()}
    stored = targets[local_records.stored.id]
    assert stored.owner == "opportunity:10001" and stored.render_source == "stored"
    # the newest stored copy is what the run page draws
    assert {f.pattern for f in stored.findings} == {"top_or_parent"}
    assert (stored.sandboxed_now, stored.sandboxed_all) == (False, True)

    follower = targets[local_records.follower.id]
    assert follower.owner == "program:10500" and follower.render_source == "template:performance_review"
    assert not [f for f in follower.findings if f.severity == "breaks"]  # the template's code, not the stored one
    assert follower.sandboxed_now  # performance_review is checked

    tw = targets[local_records.tw_follower.id]
    assert tw.render_source == f"template_workflow:{local_records.template.id}@v3"
    assert {f.pattern for f in tw.findings} == {"confirm"}  # the published version, not the stored copy
    assert tw.sandboxed_now  # config.sandbox inherited from the published version


@pytest.mark.django_db
def test_an_unpublished_template_workflow_falls_back_to_the_stored_copy(local_records):
    local_records.template.data = {"name": "Report template", "template_workflow": {"template_type": "x"}}
    local_records.template.save()
    tw = {t.id: t for t in local_targets()}[local_records.tw_follower.id]
    assert tw.render_source.endswith("(unresolved)") and tw.findings == [] and "no published version" in tw.note


def test_seed_templates_are_scanned_apart():
    targets = {t.id: t for t in template_targets()}
    assert set(targets) == set(TEMPLATES)
    assert targets["performance_review"].sandboxed_now
    assert targets["kmc_image_audit"].sandboxed_now  # checked since #2473
    # The deprecated MBW v1 is never checked: in the frame only under "all".
    assert not targets["mbw_monitoring"].sandboxed_now
    assert targets["mbw_monitoring"].sandboxed_all


class _FakeDA:
    """WorkflowDataAccess, answering from a dict of scope -> definitions."""

    scopes = {}

    def __init__(self, access_token, **scope):
        from connect_labs.labs.integrations.connect.api_client import LabsAPIError

        self.scope = tuple(scope.items())[0]
        if self.scope == ("opportunity_id", 3):
            self._refuse = LabsAPIError("nope", status_code=404)
        else:
            self._refuse = None
        self.labs_api = SimpleNamespace(get_records=lambda **kw: self.scopes.get(self.scope, {}).get("codes", []))

    def list_definitions(self):
        if self._refuse:
            raise self._refuse
        return self.scopes.get(self.scope, {}).get("defs", [])

    def close(self):
        pass


def _rec(id_, data, **scope):
    return SimpleNamespace(
        id=id_, data=data, opportunity_id=scope.get("opportunity_id"), program_id=scope.get("program_id"),
        organization_id=None,
    )  # fmt: skip


def test_connect_scopes_are_read_as_the_user_and_deduplicated():
    shared = _rec(70, {"name": "Program dashboard", "config": {}}, program_id=25)
    follower = _rec(
        71, {"name": "Follows", "config": {"templateType": "performance_review"},
             "render_source": {"template": "performance_review"}}, opportunity_id=1,
    )  # fmt: skip
    _FakeDA.scopes = {
        ("opportunity_id", 1): {
            "defs": [shared, follower],
            "codes": [SimpleNamespace(data={"definition_id": 70, "component_code": STORED_BREAKS})],
        },
        ("program_id", 25): {"defs": [shared], "codes": []},
    }
    org = {
        "opportunities": [{"id": 1}, {"id": 3}, {"id": 10001, "labs_only": True}],
        "programs": [{"id": 25}],
        "organizations": [],
    }
    user = SimpleNamespace(username="field.lead")
    coverage = Coverage()
    with (
        patch("connect_labs.labs.connect_tokens.get_valid_access_token", return_value="t"),
        patch("connect_labs.labs.integrations.connect.oauth.fetch_user_organization_data", return_value=org),
        patch("connect_labs.workflow.data_access.WorkflowDataAccess", _FakeDA),
    ):
        targets = connect_targets([user], coverage, seen=set())
    by_id = {t.id: t for t in targets}
    assert sorted(by_id) == [70, 71]
    assert {f.pattern for f in by_id[70].findings} == {"top_or_parent"}
    assert by_id[71].render_source == "template:performance_review"
    assert (coverage.scopes_read, coverage.scopes_refused, coverage.connect_definitions) == (2, 1, 2)
    assert coverage.users_scanned == ["field.lead"] and coverage.real_opps_covered


# --- the command -------------------------------------------------------------------

MARKER = "CODE_MARKER_NEVER_PRINTED"


@pytest.mark.django_db
def test_command_prints_ids_and_names_never_code_and_states_the_gap():
    d = _local("workflow_definition", {"name": "Supervisor board", "config": {}}, opportunity_id=10003)
    code = f"function W(){{ const k = '{MARKER}'; if (window.confirm('x')) go(); return null; }}"
    _local("workflow_render_code", {"definition_id": d.id, "component_code": code}, opportunity_id=10003)
    out = StringIO()
    call_command("workflow_sandbox_scan", "--fix-suggestions", stdout=out)
    text = out.getvalue()
    assert f"local:{d.id} | Supervisor board | opportunity:10003" in text
    assert "confirm | breaks | 1" in text
    assert MARKER not in text and "window.confirm" not in text
    assert "NOT COVERED" in text and "--all-users" in text
    assert "Deployed seed templates" in text
    assert "confirm -> await view.confirm(text)  (in an async function) | lines 1" in text


@pytest.mark.django_db
def test_command_summary_prints_counts_and_no_rows():
    d = _local("workflow_definition", {"name": "Supervisor board", "config": {}}, opportunity_id=10003)
    code = "function W(){ window.parent.postMessage('x', '*'); window.location.reload(); return null; }"
    _local("workflow_render_code", {"definition_id": d.id, "component_code": code}, opportunity_id=10003)
    out = StringIO()
    call_command("workflow_sandbox_scan", "--summary", stdout=out)
    text = out.getvalue()
    assert "== Coverage" in text and "by worst finding:" in text
    assert "top_or_parent      breaks      1 / 1" in text
    assert "location_reload    info        1 / 1" in text
    assert "breaking AND sandboxed under `all`, by template" in text
    assert "Supervisor board" not in text and " | " not in text


@pytest.mark.django_db
def test_command_json_has_no_code():
    d = _local("workflow_definition", {"name": "Supervisor board", "config": {}}, opportunity_id=10003)
    code = f"function W(){{ const k = '{MARKER}'; confirm('x'); return null; }}"
    _local("workflow_render_code", {"definition_id": d.id, "component_code": code}, opportunity_id=10003)
    out = StringIO()
    call_command("workflow_sandbox_scan", "--json", stdout=out)
    assert MARKER not in out.getvalue()
    report = json.loads(out.getvalue())
    [row] = [r for r in report["definitions"] if r["id"] == d.id]
    assert row["findings"] == [
        {
            "pattern": "confirm",
            "severity": "breaks",
            "line": 1,
            "detail": "not in an async function or event handler: answered No",
        }
    ]
    assert report["coverage"]["real_opps_covered"] is False
    assert len(report["templates"]) == len(TEMPLATES)


@pytest.mark.django_db
def test_command_refuses_an_unknown_user():
    from django.core.management.base import CommandError

    with pytest.raises(CommandError):
        call_command("workflow_sandbox_scan", "--as-user", "nobody.here", stdout=StringIO())


# --- no workflow is kept out of the sandbox (2026-10-13 ruling) -----------------------

MBW_CODE = TEMPLATES["mbw_monitoring"]["render_code"]


def _mbw(id_, opp, key="mbw_monitoring"):
    return SimpleNamespace(id=id_, opportunity_id=opp, data={"name": "MBW", "config": {"templateType": key}})


def test_under_all_every_workflow_is_sandboxed_deprecated_copies_included():
    from connect_labs.workflow.sandbox import should_sandbox

    # The live MBW monitoring copies the 2026-10-13 scan found (once pinned out).
    for id_, opp in ((1035, 740), (897, 765), (2411, 709)):
        assert should_sandbox(_mbw(id_, opp), mode="all")
        assert should_sandbox(_mbw(id_, opp, "mbw_monitoring_v2"), mode="all")
    assert not should_sandbox(_mbw(1035, 740), mode="off")


def test_the_deprecated_mbw_dashboard_scans_without_a_break():
    """Its own /custom_analysis/mbw_monitoring/ endpoints and stream are on the table now."""
    from connect_labs.workflow.sandbox_scan import BREAKS

    breaks = [f for f in scan_render_code(MBW_CODE) if f.severity == BREAKS]
    assert breaks == []


def test_the_live_copies_gaps_are_answered():
    """What the 2026-10-13 live scan found breaking, as the copies write it."""
    from connect_labs.workflow.sandbox_scan import BREAKS

    code = _wrap("""
  function attachPipeline() {
    if (!window.confirm('Attach anyway?')) return;
    fetch('/labs/workflow/api/' + definitionId() + '/pipeline-sources/' + path + '/', { method: 'POST' });
  }
  fetch('/labs/workflow/api/pipeline/' + pid + '/');
  fetch('/labs/workflow/api/available-pipelines/');
  fetch("/labs/workflow/?opportunity_id=" + instance.opportunity_id, { credentials: "same-origin" });
  var el = <button onClick={attachPipeline}>Attach</button>;
""")
    assert [f for f in scan_render_code(code) if f.severity == BREAKS] == []


def test_a_fetch_to_an_address_labs_no_longer_has_is_info():
    [gone] = [f for f in scan_render_code(_wrap("  fetch('/labs/workflow/api/commcare-user-cases/');"))]
    assert gone.pattern == "fetch_gone" and gone.severity == "info"
