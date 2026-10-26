"""A data space's dictionary and its de-identified exports, end to end over the real
reader (``engine``) and real cached visits.

What must hold: a field is exported only once a person reviewed it; an export refuses a
field the dictionary does not know; what leaves never carries a name, username or id from
the source; the person who asked cannot sign it off; and only someone who can still open
the space downloads it, until it expires.

THIS REPOSITORY IS PUBLIC. Every name, phone and place here is invented.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import date, timedelta

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from connect_labs.labs import canopy_panel
from connect_labs.labs.access import scopes
from connect_labs.labs.analysis.backends.sql.models import RawVisitCache
from connect_labs.labs.analysis.config import USER_VISITS_RAW_SLOT
from connect_labs.labsdata import dictionary, exports, scope, tasks
from connect_labs.labsdata.models import DictionaryFlag, SpaceExport
from connect_labs.labsdata.scope import Opportunity

pytestmark = pytest.mark.django_db

O1 = Opportunity(id=9811, name="Opp Export", llo="LLO X")
O2 = Opportunity(id=9812, name="Opp Other", llo="LLO Y")
DIRECTORY = {o.id: o for o in (O1, O2)}
HOLDS = {"ann": {O1.id}, "ben": {O1.id}, "cat": {O2.id}}
MOTHERS = ["Zainab Okafor", "Halima Bello", "Ngozi Eze", "Amaka Uche"]


@pytest.fixture
def people():
    User = get_user_model()
    return {n: User.objects.create_user(username=f"exp-{n}", password="x") for n in HOLDS}


@pytest.fixture(autouse=True)
def connect(monkeypatch, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)

    def holdings(caller):
        user = caller.user or getattr(caller.request, "user", None)
        opps = HOLDS.get((getattr(user, "username", "") or "").removeprefix("exp-"), set())
        return scopes.Holdings(org_slugs=frozenset(), opportunity_ids=frozenset(opps), program_ids=frozenset())

    monkeypatch.setattr(scopes, "holdings", holdings)
    monkeypatch.setattr(scope, "directory", lambda caller: dict(DIRECTORY))
    monkeypatch.setattr(canopy_panel, "panel", lambda *a, **k: None)
    # Build in the request, not on a worker.
    monkeypatch.setattr(tasks.build_space_export, "delay", lambda pk: exports.build(pk))


def _form(n: int, *, extra: dict | None = None, lethargy_key: str = "lethargy", build: int = 7):
    return {
        "form": {
            "meta": {"appVersion": f"CommCare Android. App Version: {build}.", "username": f"worker{n % 2}"},
            "mother_name": MOTHERS[n % 4],
            "phone": f"0803{n:07d}",
            "weight_g": str(2400 + 13 * n),
            "place": "home" if n % 2 else "hospital",
            "danger": {lethargy_key: "yes" if n % 3 == 0 else "no"},
            "visit_note": f"seen {MOTHERS[n % 4].split()[0]} today",
            "case": {"@case_id": f"7c9e6679-7425-40de-944b-e07fc1f9{n % 4:04d}"},
            **(extra or {}),
        }
    }


def _load(n: int = 24, opp: int = O1.id, **kw):
    RawVisitCache.objects.filter(opportunity_id=opp).delete()
    RawVisitCache.objects.bulk_create(
        [
            RawVisitCache(
                opportunity_id=opp,
                pipeline_id=USER_VISITS_RAW_SLOT,
                visit_count=n,
                expires_at=timezone.now() + timedelta(hours=1),
                visit_id=f"v-{opp}-{i:03d}",
                username=f"worker{i % 2}",
                entity_id=f"7c9e6679-7425-40de-944b-e07fc1f9{i % 4:04d}",
                entity_name=MOTHERS[i % 4],
                deliver_unit="Visit form",
                visit_date=date(2026, 9, 1) + timedelta(days=i),
                status="approved",
                form_json=_form(i, **kw),
            )
            for i in range(n)
        ]
    )


def _client(user):
    c = Client()
    c.force_login(user)
    return c


def _post(client, url, body=None):
    return client.post(url, json.dumps(body or {}), content_type="application/json")


@pytest.fixture
def space(people):
    _load()
    res = _post(_client(people["ann"]), "/labs/data/api/spaces/", {"name": "KMC export", "opportunity_ids": [O1.id]})
    assert res.status_code == 200, res.content
    return res.json()["space"]["id"]


def _api(sid, tail=""):
    return f"/labs/data/api/spaces/{sid}/{tail}"


def _dict(client, sid):
    return client.get(_api(sid, "dictionary/")).json()


def _entry(d, key):
    return next(e for e in d["entries"] if e["key"] == key)


def _scan(client, sid):
    res = _post(client, _api(sid, "dictionary/scan/"))
    assert res.status_code == 200, res.content
    return res.json()


# --- the dictionary -----------------------------------------------------------------


def test_a_scan_finds_every_field_judges_its_kind_and_exports_nothing_yet(people, space):
    ann = _client(people["ann"])
    out = _scan(ann, space)
    d = out["dictionary"]
    assert _entry(d, "form.mother_name")["kind"] == "identifier"
    assert _entry(d, "form.phone")["kind"] == "identifier"
    assert _entry(d, "form.case.@case_id")["suggested_setting"] == "pseudonymise"
    assert _entry(d, "form.place")["kind"] == "categorical"
    assert _entry(d, "form.weight_g")["suggested_setting"] == "keep"
    # meta (the worker's username, the device) is never walked.
    assert not any(e["key"].startswith("form.meta") for e in d["entries"])
    # Drop unless allowed: nothing is exported until someone reviews it.
    assert d["counts"]["exported"] == 0
    assert {f["kind"] for f in d["flags"]} == {DictionaryFlag.NEW_FIELD}
    src = _entry(d, "form.weight_g")["sources"][0]
    assert src["builds"] == [7] and src["visits"] == 24 and "value" not in json.dumps(src)


def test_a_scan_stores_no_value(people, space):
    _scan(_client(people["ann"]), space)
    from connect_labs.labsdata.models import DataDictionary

    stored = json.dumps(
        [
            list(DataDictionary.objects.values()),
            list(__import__("connect_labs.labsdata.models", fromlist=["x"]).DictionarySource.objects.values()),
            list(DictionaryFlag.objects.values("detail")),
        ],
        default=str,
    )
    for name in MOTHERS:
        assert name not in stored
    assert "0803" not in stored


def test_a_recased_question_joins_its_entry_and_a_dropped_field_is_flagged(people, space):
    ann = _client(people["ann"])
    _scan(ann, space)
    _load(lethargy_key="Lethargy", build=8)  # the next app build renamed the case of a question
    d = _scan(ann, space)["dictionary"]
    entry = _entry(d, "form.danger.lethargy")
    assert {s["path"] for s in entry["sources"]} == {"form.danger.lethargy", "form.danger.Lethargy"}
    kinds = {(f["kind"], tuple(f["detail"].get("paths", []))) for f in d["flags"]}
    assert ("new_spelling", ("form.danger.Lethargy",)) in kinds
    # The old spelling is not in build 8: the newest build stopped sending it.
    assert ("gone", ("form.danger.lethargy",)) in kinds


def test_only_a_manager_changes_a_setting_and_a_name_is_never_kept(people, space):
    ann, ben = _client(people["ann"]), _client(people["ben"])
    _scan(ann, space)
    # Ben can open the space (he holds its source) but does not manage it.
    assert ben.get(_api(space, "dictionary/")).status_code == 200
    assert _post(ben, _api(space, "dictionary/entry/"), {"key": "form.place", "setting": "keep"}).status_code == 403
    refused = _post(ann, _api(space, "dictionary/entry/"), {"key": "form.mother_name", "setting": "keep"})
    assert refused.status_code == 400 and refused.json()["error"] == "identifies"
    ok = _post(
        ann, _api(space, "dictionary/entry/"), {"key": "form.weight_g", "setting": "round", "params": {"step": 25}}
    )
    assert ok.status_code == 200 and ok.json()["entry"]["effective"] == "round"
    changes = _dict(ann, space)["changes"]
    assert changes[0]["action"] == "update" and changes[0]["after"]["setting"] == "round"


def test_someone_who_does_not_hold_the_source_never_sees_the_dictionary(people, space):
    assert _client(people["cat"]).get(_api(space, "dictionary/")).status_code == 404
    assert _client(people["cat"]).get(f"/labs/data/space/{space}/exports/").status_code == 404


def test_merge_takes_the_stricter_setting_and_needs_review_again(people, space):
    ann = _client(people["ann"])
    _scan(ann, space)
    _post(ann, _api(space, "dictionary/accept/"), {"keys": ["form.place", "form.visit_note"]})
    res = _post(ann, _api(space, "dictionary/merge/"), {"into": "form.place", "other": "form.visit_note"})
    merged = res.json()["entry"]
    assert merged["setting"] == "drop" and not merged["reviewed"]
    assert {s["path"] for s in merged["sources"]} == {"form.place", "form.visit_note"}


# --- exports --------------------------------------------------------------------------


def _ready_dictionary(
    ann, sid, keys=("form.weight_g", "form.place", "form.case.@case_id", "@entity_id", "@visit_date")
):
    _scan(ann, sid)
    res = _post(ann, _api(sid, "dictionary/accept/"), {"keys": list(keys)})
    assert res.status_code == 200, res.content


def _ask(client, sid, **body):
    return _post(client, _api(sid, "exports/"), body)


def _export(sid, pk):
    return SpaceExport.objects.get(pk=pk, space_id=sid)


def _zip(client, sid, pk) -> zipfile.ZipFile:
    res = client.get(_api(sid, f"exports/{pk}/download/"))
    assert res.status_code == 200, getattr(res, "content", res)
    return zipfile.ZipFile(io.BytesIO(b"".join(res.streaming_content)))


def test_nothing_reviewed_means_no_export(people, space):
    ann = _client(people["ann"])
    _scan(ann, space)
    res = _ask(ann, space)
    assert res.status_code == 400 and res.json()["error"] == "nothing_allowed"


def test_an_export_carries_only_reviewed_fields_coded_and_signed_off_by_someone_else(
    people, space, django_capture_on_commit_callbacks
):
    ann, ben = _client(people["ann"]), _client(people["ben"])
    _ready_dictionary(ann, space)
    with django_capture_on_commit_callbacks(execute=True):
        res = _ask(ben, space, date_from="2026-09-01", date_to="2026-09-30")
    assert res.status_code == 200, res.content
    export = _export(space, res.json()["export"]["id"])
    assert export.status == SpaceExport.READY, export.checks
    assert export.rows == export.source_rows == 24
    assert {c["name"]: c["status"] for c in export.checks["checks"]}["pii_scan"] == "pass"

    # Ben asked, so Ben cannot sign it off, and nobody can download it yet.
    assert ben.get(_api(space, f"exports/{export.pk}/download/")).status_code == 403
    space_row = export.space
    space_row.managers.add(people["ben"])
    own = _post(ben, _api(space, f"exports/{export.pk}/sign-off/"), {"note": "for the evaluators"})
    assert own.status_code == 403 and own.json()["error"] == "own_export"
    assert _post(ann, _api(space, f"exports/{export.pk}/sign-off/"), {"note": ""}).json()["error"] == "note_required"
    warned = [c["name"] for c in export.checks["checks"] if c["status"] == "warn"]
    signed = _post(
        ann,
        _api(space, f"exports/{export.pk}/sign-off/"),
        {"note": "for the evaluators", "acknowledge_warnings": bool(warned)},
    )
    assert signed.status_code == 200, signed.content

    z = _zip(ben, space, export.pk)
    data = z.read("data.csv").decode()
    header = data.splitlines()[0].split(",")
    assert set(header) == {"form.weight_g", "form.place", "form.case.@case_id", "entity_id", "visit_date"}
    for secret in [*MOTHERS, "0803", "worker0", "7c9e6679", "seen"]:
        assert secret not in data
    # The same case has the same code in the form and in the visits column.
    rows = [r.split(",") for r in data.splitlines()[1:]]
    col = {h: i for i, h in enumerate(header)}
    assert all(r[col["form.case.@case_id"]] == r[col["entity_id"]] for r in rows)
    assert len({r[col["entity_id"]] for r in rows}) == 4
    dictionary_csv = z.read("dictionary.csv").decode()
    assert "form.weight_g" in dictionary_csv and not any(m in dictionary_csv for m in MOTHERS)
    assert {"README.md", "checks.json"} <= set(z.namelist())


def test_a_field_the_dictionary_does_not_know_blocks_the_export(people, space, django_capture_on_commit_callbacks):
    ann = _client(people["ann"])
    _ready_dictionary(ann, space)
    _load(extra={"new_question": "x"})  # a new app version, not yet scanned
    with django_capture_on_commit_callbacks(execute=True):
        pk = _ask(ann, space).json()["export"]["id"]
    export = _export(space, pk)
    assert export.status == SpaceExport.BLOCKED and not export.file_name
    untagged = export.checks["checks"][0]
    assert (
        untagged["name"] == "untagged"
        and {"path": "form.new_question", "deliver_unit": "Visit form"} in untagged["detail"]["fields"]
    )


def test_a_kept_field_that_names_someone_fails_the_leak_scan(people, space, django_capture_on_commit_callbacks):
    ann = _client(people["ann"])
    _ready_dictionary(ann, space)
    # Someone allows the free-text note as is: it carries the mothers' names.
    _post(ann, _api(space, "dictionary/entry/"), {"key": "form.visit_note", "setting": "keep"})
    with django_capture_on_commit_callbacks(execute=True):
        pk = _ask(ann, space).json()["export"]["id"]
    export = _export(space, pk)
    scan = next(c for c in export.checks["checks"] if c["name"] == "pii_scan")
    assert export.status == SpaceExport.BLOCKED and scan["status"] == "fail"
    assert "form.visit_note" in scan["detail"]["fail"] and not export.file_name
    assert not any(m.split()[0] in json.dumps(export.checks) for m in MOTHERS)


def test_scrubbing_the_note_passes(people, space, django_capture_on_commit_callbacks):
    ann = _client(people["ann"])
    _ready_dictionary(ann, space)
    _post(ann, _api(space, "dictionary/entry/"), {"key": "form.visit_note", "setting": "scrub"})
    with django_capture_on_commit_callbacks(execute=True):
        pk = _ask(ann, space).json()["export"]["id"]
    assert _export(space, pk).status == SpaceExport.READY


def test_an_expired_export_is_deleted_and_cannot_be_downloaded(people, space, django_capture_on_commit_callbacks):
    ann, ben = _client(people["ann"]), _client(people["ben"])
    _ready_dictionary(ann, space)
    with django_capture_on_commit_callbacks(execute=True):
        pk = _ask(ben, space).json()["export"]["id"]
    export = _export(space, pk)
    _post(ann, _api(space, f"exports/{pk}/sign-off/"), {"note": "n", "acknowledge_warnings": True})
    export.refresh_from_db()
    assert export.status == SpaceExport.APPROVED
    assert exports.sweep(now=export.expires_at + timedelta(seconds=1)) == 1
    export.refresh_from_db()
    assert export.status == SpaceExport.EXPIRED and not export.file_name
    assert ben.get(_api(space, f"exports/{pk}/download/")).status_code == 403


# --- lineage ------------------------------------------------------------------------------


class _Field:
    def __init__(self, name, path="", paths=None):
        self.name, self.path, self.paths = name, path, paths


class _Config:
    def __init__(self, *fields):
        self.fields = list(fields)


def test_a_property_inherits_the_strictest_of_its_fields_and_unset_is_sensitive():
    from connect_labs.labsdata import lineage

    entries = [
        {"column": None, "reviewed": True, "setting": "round", "sources": [{"path": "form.weight_g"}]},
        {"column": None, "reviewed": True, "setting": "keep", "sources": [{"path": "form.place"}]},
        {"column": None, "reviewed": False, "setting": "keep", "sources": [{"path": "form.danger.lethargy"}]},
        {"column": "visit_date", "reviewed": True, "setting": "keep", "sources": []},
    ]
    props_doc = {
        "entity": {"name": "baby", "key": "baby_case_id"},
        "pipelines": {"entity": "children"},
        "aggregates": [
            {"name": "max_weight", "sql": "MAX(weight_g)"},
            {"name": "home_visits", "sql": "COUNT(*) FILTER (WHERE place = 'home')"},
            {"name": "lethargic", "sql": "COUNT(*) FILTER (WHERE lethargy = 'yes')"},
            {"name": "first_visit", "sql": "MIN(visit_date)"},
        ],
        "properties": [
            {"name": "heavy_at_home", "sql": "max_weight > 2500 AND home_visits > 0"},
            {"name": "ever_lethargic", "sql": "lethargic > 0"},
            {"name": "started", "sql": "first_visit IS NOT NULL"},
        ],
    }
    configs = {
        "children": _Config(
            _Field("weight_g", "form.weight_g"),
            _Field("place", "form.place"),
            _Field("lethargy", paths=["form.danger.lethargy", "form.danger.Lethargy"]),
        )
    }
    out = lineage.compute(entries, props_doc, configs)
    got = {p["name"]: p["inherits"] for p in out["properties"]}
    assert got == {"heavy_at_home": "round", "ever_lethargic": "unset", "started": "keep"}
    unset = next(p for p in out["properties"] if p["name"] == "ever_lethargic")["unset_paths"]
    assert set(unset) == {"form.danger.lethargy", "form.danger.Lethargy"}


def test_dictionary_effective_is_drop_until_reviewed():
    assert dictionary.effective({"setting": "keep", "reviewed": False}) == "drop"
    assert dictionary.effective({"setting": "keep", "reviewed": True}) == "keep"


# --- the MCP tools ------------------------------------------------------------------------


def test_the_tools_read_structure_and_ask_but_never_return_a_value(
    people, space, monkeypatch, django_capture_on_commit_callbacks
):
    from connect_labs.mcp.tools import labsdata as tools

    monkeypatch.setattr(tools, "require_connect_token", lambda user: "tok")
    ann = _client(people["ann"])
    _ready_dictionary(ann, space)
    out = tools.data_dictionary(people["ann"], space)
    assert _entry(out, "form.weight_g")["effective"] == "keep" and "changes" not in out
    with django_capture_on_commit_callbacks(execute=True):
        asked = tools.data_export_request(people["ben"], space, date_from="2026-09-01")
    listed = tools.data_exports(people["ann"], space)
    text = json.dumps([out, asked, listed])
    for secret in [*MOTHERS, "0803", "worker0", "7c9e6679"]:
        assert secret not in text
    assert listed["exports"][0]["status"] == SpaceExport.READY
    assert not hasattr(tools, "data_export_sign_off") and not hasattr(tools, "data_export_download")
    with pytest.raises(tools.MCPToolError):
        tools.data_dictionary(people["cat"], space)


def test_the_page_draws_for_a_holder_and_links_from_the_space(people, space):
    page = _client(people["ben"]).get(f"/labs/data/space/{space}/exports/")
    assert page.status_code == 200
    html = page.content.decode()
    assert "labsdata/exports.js" in html and f"/labs/data/api/spaces/{space}/dictionary/" in html
    assert "isManager: false" in html  # Ben holds the source but does not manage the space
    assert (
        f"/labs/data/space/{space}/exports/"
        in _client(people["ann"]).get(f"/labs/data/space/{space}/").content.decode()
    )
