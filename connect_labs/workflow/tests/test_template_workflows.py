"""Template workflows: a report's render, config defaults and snapshot spec as DATA.

The KMC program report needed a PR, a merge and a production deploy for each of
three layout tweaks on 2026-10-05 (connect-labs#2229). A template workflow holds
that render as data with a draft, published versions and a rollback; followers show
the published version on their next load.

Since #2236 it is all LabsRecords, under the LabsRecord ACL. These tests run the
REAL `WorkflowDataAccess` and template code against an in-memory LabsRecord store
that enforces Connect's rules: a scoped read or write needs access to that scope,
and a scope-less read sees public records only. They pin: follow, inherit,
override, ACL-based edit/publish (write scope allowed, read-only refused), follow
through a public read, draft vs publish, preview, and rollback.
"""

from __future__ import annotations

import copy
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from connect_labs.labs.integrations.connect.api_client import LabsAPIError
from connect_labs.labs.models import LocalLabsRecord, UserConnectToken
from connect_labs.mcp.models import MCPAccessToken
from connect_labs.mcp.testing import call_tool
from connect_labs.users.models import User
from connect_labs.workflow import render_source as rs
from connect_labs.workflow import template_workflows as tw
from connect_labs.workflow.data_access import WorkflowDataAccess
from connect_labs.workflow.templates import TEMPLATES

KEY = "__tv_template_workflow__"
TEMPLATE_RENDER = "function R(){return 'published v1'}"

TEMPLATE_SCOPE = {"program_id": 77}
FOLLOWER_SCOPE = {"opportunity_id": 523}
TWIN_SCOPE = {"opportunity_id": 10042}
OTHER_PROGRAM_OPP = {"opportunity_id": 900}


@pytest.fixture(autouse=True)
def code_template():
    TEMPLATES[KEY] = {
        "key": KEY,
        "name": "Seed",
        "description": "seed",
        "render_code": TEMPLATE_RENDER,
        "snapshot_inputs": {"builder": "semantic_snapshot", "scopes": ["programme"], "series": ["KMC"]},
        "definition": {
            "name": "Seed",
            "description": "seed",
            "statuses": [{"id": "a", "label": "A", "color": "gray"}],
            "config": {"templateType": KEY, "showFilters": False, "chart_order": ["a", "b"]},
        },
    }
    yield
    TEMPLATES.pop(KEY, None)


# -----------------------------------------------------------------------------
# An in-memory LabsRecord store with Connect's ACL
# -----------------------------------------------------------------------------


def _sk(scope: dict) -> str:
    if scope.get("opportunity_id") is not None:
        return f"opp:{int(scope['opportunity_id'])}"
    if scope.get("program_id") is not None:
        return f"program:{int(scope['program_id'])}"
    return ""


class Store:
    def __init__(self):
        self.rows: dict[int, dict] = {}
        self.next_id = 500
        #: token -> scope keys that person can read AND write (Connect checks both alike).
        self.access: dict[str, set] = {}

    def api(self, token, scope):
        return FakeLabsAPI(self, token, scope)

    def dao(self, access_token=None, opportunity_id=None, program_id=None, **_):
        wda = WorkflowDataAccess.__new__(WorkflowDataAccess)
        wda.opportunity_id, wda.program_id, wda.organization_id = opportunity_id, program_id, None
        wda.access_token = access_token
        wda.http_client = None
        wda.labs_api = self.api(access_token, {"opportunity_id": opportunity_id, "program_id": program_id})
        return wda

    def put_definition(self, wid, scope, data, render=None):
        self.rows[wid] = {
            "type": "workflow_definition",
            "data": copy.deepcopy(data),
            "scope": _sk(scope),
            "public": False,
            "labs_record_id": None,
            "scope_dict": dict(scope),
        }
        if render is not None:
            self.next_id += 1
            self.rows[self.next_id] = {
                "type": "workflow_render_code",
                "scope": _sk(scope),
                "public": False,
                "labs_record_id": None,
                "scope_dict": dict(scope),
                "data": {"definition_id": wid, "component_code": render, "version": 1},
            }

    def raw(self, wid):
        return self.rows[wid]["data"]


class FakeLabsAPI:
    def __init__(self, store, token, scope):
        self.store, self.token = store, token
        self.scope = {k: v for k, v in scope.items() if v is not None}

    def _member(self, scope_key):
        return scope_key in self.store.access.get(self.token, set()) or scope_key.startswith("opp:100")

    def _rec(self, rid, row, model_class=None):
        s = row["scope_dict"]
        return (model_class or LocalLabsRecord)(
            {
                "id": rid,
                "experiment": "workflow",
                "type": row["type"],
                "data": copy.deepcopy(row["data"]),
                "opportunity_id": s.get("opportunity_id"),
                "program_id": s.get("program_id"),
                "labs_record_id": row["labs_record_id"],
                "public": row["public"],
            }
        )

    def get_record_by_id(
        self,
        record_id,
        experiment=None,
        type=None,
        model_class=None,
        opportunity_id=None,
        organization_id=None,
        program_id=None,
    ):
        override = (
            {"opportunity_id": opportunity_id}
            if opportunity_id is not None
            else ({"program_id": program_id} if program_id is not None else None)
        )
        key = _sk(override or self.scope)
        if not self._member(key):
            raise LabsAPIError("not found", status_code=404)
        row = self.store.rows.get(int(record_id))
        if not row or row["scope"] != key or (type and row["type"] != type):
            return None
        return self._rec(int(record_id), row, model_class)

    def get_public_record_by_id(self, record_id, experiment=None, type=None, model_class=None):
        row = self.store.rows.get(int(record_id))
        if not row or not row["public"] or (type and row["type"] != type):
            return None
        return self._rec(int(record_id), row, model_class)

    def get_records(
        self,
        experiment=None,
        type=None,
        labs_record_id=None,
        public=None,
        program_id=None,
        opportunity_id=None,
        model_class=None,
        **filters,
    ):
        if public:
            rows = [(i, r) for i, r in self.store.rows.items() if r["public"]]
        else:
            key = _sk(
                {"opportunity_id": opportunity_id}
                if opportunity_id is not None
                else ({"program_id": program_id} if program_id is not None else self.scope)
            )
            if not self._member(key):
                raise LabsAPIError("not found", status_code=404)
            rows = [(i, r) for i, r in self.store.rows.items() if r["scope"] == key]
        return [
            self._rec(i, r, model_class)
            for i, r in rows
            if (not type or r["type"] == type) and (labs_record_id is None or r["labs_record_id"] == labs_record_id)
        ]

    def create_record(self, experiment, type, data, username=None, program_id=None, labs_record_id=None, public=False):
        key = _sk(self.scope)
        if not self._member(key):
            raise LabsAPIError("not found", status_code=404)
        self.store.next_id += 1
        rid = self.store.next_id
        self.store.rows[rid] = {
            "type": type,
            "data": copy.deepcopy(data),
            "scope": key,
            "public": bool(public),
            "labs_record_id": labs_record_id,
            "scope_dict": dict(self.scope),
        }
        return self._rec(rid, self.store.rows[rid])

    def update_record(self, record_id, experiment, type, data, public=None, **_):
        row = self.store.rows.get(int(record_id))
        if row is None or not self._member(row["scope"]):
            raise LabsAPIError("not found", status_code=404)
        row["data"] = copy.deepcopy(data)
        if public is not None:
            row["public"] = bool(public)
        return self._rec(int(record_id), row)

    def close(self):
        pass


# -----------------------------------------------------------------------------
# People
# -----------------------------------------------------------------------------


def _person(username):
    user = User.objects.create(username=username)
    _, raw = MCPAccessToken.create_token(user, name="t")
    UserConnectToken.objects.create(
        user=user, access_token=f"tok-{username}", expires_at=timezone.now() + timedelta(hours=1)
    )
    return user, raw


@pytest.fixture
def store():
    s = Store()
    with (patch("connect_labs.mcp.tools.workflow_templates_data.WorkflowDataAccess", side_effect=s.dao),):
        yield s


@pytest.fixture
def owner(db, store):
    """Writes program 77 (the template's home) and opportunity 523 (a follower)."""
    user, raw = _person("tmpl_owner")
    store.access["tok-tmpl_owner"] = {"program:77", "opp:523"}
    return user, raw


@pytest.fixture
def follower_only(db, store):
    """Has opportunity 523 but NOT program 77: may read the template only once it is public."""
    user, raw = _person("tmpl_follower")
    store.access["tok-tmpl_follower"] = {"opp:523", "opp:900"}
    return user, raw


@pytest.fixture
def co_editor(db, store):
    """Another member of program 77's managing org -- listed nowhere, edits anyway."""
    user, raw = _person("tmpl_coeditor")
    store.access["tok-tmpl_coeditor"] = {"program:77"}
    return user, raw


def _ok(resp):
    assert resp["result"]["isError"] is False, resp
    return resp["result"]["structuredContent"]


def _err(resp):
    assert resp["result"]["isError"] is True, resp
    return resp["result"]["structuredContent"]["error"]


def _create(raw, **extra):
    return _ok(
        call_tool(
            raw,
            "workflow_template_create",
            {
                "name": "KMC Program Metrics",
                "template_scope": "program:77",
                "template_key": KEY,
                **TEMPLATE_SCOPE,
                **extra,
            },
        )
    )


def _follower(store, wid=19778, scope=FOLLOWER_SCOPE, **config):
    data = {
        "name": "JJ - KMC",
        "version": 3,
        "config": {"templateType": KEY, "showFilters": False, "chart_order": ["a", "b"], **config},
        "snapshot_inputs": {"builder": "semantic_snapshot", "scopes": ["programme"], "series": ["KMC"]},
    }
    store.put_definition(wid, scope, data, render="function S(){return 'stored copy'}")
    return wid


def _follow(raw, wid=19778, scope=FOLLOWER_SCOPE, tid=None, **extra):
    return call_tool(
        raw,
        "workflow_follow_template",
        {"workflow_id": wid, **scope, "template_workflow_id": tid, "template_program_id": 77, **extra},
    )


def _page(store, token="tok-tmpl_owner", wid=19778, scope=FOLLOWER_SCOPE):
    """What a viewer with `token` sees on a follower's run page."""
    dao = store.dao(access_token=token, **scope)
    definition = dao.get_definition(wid)
    code, source = rs.resolve_render_code(dao, definition)
    return definition, code, source


def _edit(raw, tid, rev, old, new):
    return call_tool(
        raw,
        "workflow_template_update_draft",
        {"template_workflow_id": tid, **TEMPLATE_SCOPE, "expected_revision": rev, "edits": [{"old": old, "new": new}]},
    )


# -----------------------------------------------------------------------------
# Storage
# -----------------------------------------------------------------------------


@pytest.mark.django_db
class TestStorage:
    def test_a_template_is_three_labsrecord_types_in_its_home_scope(self, store, owner):
        _, raw = owner
        out = _create(raw)
        tid = out["template_workflow_id"]
        assert out["published_version"] == 1 and out["owned_by"] == "program 77"
        assert out["you_can_edit"] is True and "owners" not in out
        assert out["seeded_from"] == f"code:{KEY}" and out["template_type"] == KEY
        types = sorted(r["type"] for r in store.rows.values() if r["scope"] == "program:77")
        assert types == ["workflow_definition", "workflow_template_draft", "workflow_template_version"]
        definition = store.raw(tid)
        assert definition["is_template"] is True and definition["template_scope"] == "program:77"
        assert definition["render_source"] == {"workflow": tid, "program_id": 77}
        meta = definition["template_workflow"]
        assert meta["published"]["version"] == 1 and meta["published"]["config"]["chart_order"] == ["a", "b"]
        draft = next(r for r in store.rows.values() if r["type"] == "workflow_template_draft")
        version = next(r for r in store.rows.values() if r["type"] == "workflow_template_version")
        assert draft["labs_record_id"] == tid and version["labs_record_id"] == tid
        assert draft["public"] is False and version["public"] is False
        # The template's own page shows the published render.
        _, code, source = _page(store, wid=tid, scope=TEMPLATE_SCOPE)
        assert code == TEMPLATE_RENDER and source["source"] == "template_workflow"

    def test_creating_in_a_scope_you_cannot_write_is_refused_by_the_acl(self, store, follower_only):
        _, raw = follower_only
        resp = call_tool(raw, "workflow_template_create", {"name": "x", "template_key": KEY, **TEMPLATE_SCOPE})
        assert resp["result"]["isError"] is True
        assert not [r for r in store.rows.values() if r["scope"] == "program:77"]

    def test_from_workflow_keeps_the_seed_workflows_own_scope(self, store, owner):
        _, raw = owner
        _follower(store)
        out = _ok(
            call_tool(
                raw,
                "workflow_template_create",
                {"name": "From 19778", "from_workflow": {"workflow_id": 19778, **FOLLOWER_SCOPE}},
            )
        )
        assert out["scope"] == FOLLOWER_SCOPE and out["owned_by"] == "opportunity 523"

    def test_global_picker_scope_is_admin_only(self, store, owner):
        _, raw = owner
        err = _err(
            call_tool(
                raw,
                "workflow_template_create",
                {"name": "x", "template_scope": "global", "template_key": KEY, **TEMPLATE_SCOPE},
            )
        )
        assert err["code"] == "PERMISSION_DENIED"


# -----------------------------------------------------------------------------
# Follow, inherit, override
# -----------------------------------------------------------------------------


@pytest.mark.django_db
class TestFollowInheritOverride:
    def test_follow_renders_published_and_inherits_with_own_keys_winning(self, store, owner):
        _, raw = owner
        tid = _create(raw)["template_workflow_id"]
        _follower(store, enrollment_targets={"goal": 12771}, showFilters=True)
        out = _ok(_follow(raw, tid=tid))

        assert out["now_inherited"]["config"] == ["chart_order", "templateType"]
        assert out["overrides"]["config"] == ["enrollment_targets", "showFilters"]
        assert out["now_inherited"]["snapshot_inputs"] == ["builder", "scopes", "series"]
        assert out["listed_on_template"] is True
        raw_record = store.raw(19778)
        assert "snapshot_inputs" not in raw_record
        assert raw_record["config"] == {"enrollment_targets": {"goal": 12771}, "showFilters": True}

        definition, code, source = _page(store)
        assert code == TEMPLATE_RENDER
        assert source == {"source": "template_workflow", "template_workflow": tid, "version": 1, "draft": False}
        cfg = definition.data["config"]
        assert cfg["chart_order"] == ["a", "b"] and cfg["templateType"] == KEY
        assert cfg["showFilters"] is True and cfg["enrollment_targets"] == {"goal": 12771}
        assert definition.data["snapshot_inputs"]["series"] == ["KMC"]
        assert definition.own_data["config"] == raw_record["config"]

    def test_a_published_config_change_reaches_followers_but_not_their_overrides(self, store, owner):
        _, raw = owner
        tid = _create(raw)["template_workflow_id"]
        _follower(store, showFilters=True)
        _ok(_follow(raw, tid=tid))
        _ok(
            call_tool(
                raw,
                "workflow_template_update_draft",
                {
                    "template_workflow_id": tid,
                    **TEMPLATE_SCOPE,
                    "expected_revision": 1,
                    "config": {"chart_order": ["b", "a"], "showFilters": False},
                },
            )
        )
        _ok(call_tool(raw, "workflow_template_publish", {"template_workflow_id": tid, **TEMPLATE_SCOPE}))
        cfg = _page(store)[0].data["config"]
        assert cfg["chart_order"] == ["b", "a"] and cfg["showFilters"] is True

    def test_a_read_modify_write_does_not_bake_inherited_values_in(self, store, owner):
        _, raw = owner
        tid = _create(raw)["template_workflow_id"]
        _follower(store, showFilters=True)
        _ok(_follow(raw, tid=tid))
        dao = store.dao(access_token="tok-tmpl_owner", **FOLLOWER_SCOPE)
        effective = dao.get_definition(19778).data
        dao.update_definition(19778, {**effective, "name": "renamed"})
        assert store.raw(19778)["config"] == {"showFilters": True}

    def test_a_different_template_type_cannot_follow(self, store, owner):
        _, raw = owner
        tid = _create(raw)["template_workflow_id"]
        _follower(store)
        store.raw(19778)["config"]["templateType"] = "kmc_opp_report"
        assert _err(_follow(raw, tid=tid))["code"] == "INVALID_SCHEMA"

    def test_stored_render_edits_are_refused_while_following(self, store, owner):
        _, raw = owner
        tid = _create(raw)["template_workflow_id"]
        _follower(store)
        _ok(_follow(raw, tid=tid))
        dao = store.dao(access_token="tok-tmpl_owner", **FOLLOWER_SCOPE)
        with pytest.raises(rs.RenderFollowsTemplateWorkflow):
            rs.refuse_edit_if_following(dao.get_definition(19778))

    def test_unfollow_forks_without_the_page_changing(self, store, owner):
        _, raw = owner
        tid = _create(raw)["template_workflow_id"]
        _follower(store, showFilters=True)
        _ok(_follow(raw, tid=tid))
        before, _, _ = _page(store)
        out = _ok(_follow(raw, follow=False))
        assert out["removed_from_template_list"] is True
        after, code, source = _page(store)
        assert source["source"] == "stored" and code == TEMPLATE_RENDER
        assert after.data["config"] == before.data["config"]
        assert after.data["snapshot_inputs"] == before.data["snapshot_inputs"]
        assert store.raw(tid)["template_workflow"]["followers"] == []

    def test_the_template_lists_its_followers_with_preview_urls(self, store, owner):
        _, raw = owner
        tid = _create(raw)["template_workflow_id"]
        _follower(store)
        _follower(store, 5456, TWIN_SCOPE)
        _ok(_follow(raw, tid=tid))
        _ok(_follow(raw, 5456, TWIN_SCOPE, tid=tid))
        out = _ok(call_tool(raw, "workflow_template_get", {"template_workflow_id": tid, **TEMPLATE_SCOPE}))
        urls = {f["workflow_id"]: f["draft_preview_url"] for f in out["followers"]}
        assert urls == {
            5456: "/labs/workflow/5456/run/?opportunity_id=10042&template_draft=1",
            19778: "/labs/workflow/19778/run/?opportunity_id=523&template_draft=1",
        }


# -----------------------------------------------------------------------------
# Permissions: the LabsRecord ACL, nothing else
# -----------------------------------------------------------------------------


@pytest.mark.django_db
class TestPermissions:
    def test_write_access_to_the_scope_edits_and_publishes_with_no_owner_list(self, store, owner, co_editor):
        _, raw = owner
        _, co_raw = co_editor
        tid = _create(raw)["template_workflow_id"]
        base = {"template_workflow_id": tid, **TEMPLATE_SCOPE}
        _ok(_edit(co_raw, tid, 1, "published v1", "co-edited"))
        out = _ok(call_tool(co_raw, "workflow_template_publish", base))
        assert out["published_now"] == 2 and out["versions"][0]["published_by"] == "tmpl_coeditor"
        _ok(call_tool(co_raw, "workflow_template_rollback", {**base, "to_version": 1}))

    def test_read_only_access_is_refused_every_edit(self, store, owner, follower_only):
        _, raw = owner
        _, ro_raw = follower_only
        tid = _create(raw, public=True)["template_workflow_id"]
        base = {"template_workflow_id": tid, **TEMPLATE_SCOPE}
        for tool, args in [
            ("workflow_template_update_draft", {"expected_revision": 1, "render_code": "x"}),
            ("workflow_template_publish", {}),
            ("workflow_template_rollback", {"to_version": 1}),
            ("workflow_template_preview", {}),
            ("workflow_template_set_sharing", {"public": False}),
        ]:
            err = _err(call_tool(ro_raw, tool, {**base, **args}))
            assert err["code"] == "PERMISSION_DENIED", tool
            assert "program 77" in err["message"], tool
        assert store.raw(tid)["template_workflow"]["published"]["version"] == 1

    def test_a_scoped_member_may_follow(self, store, owner, co_editor):
        _, raw = owner
        tid = _create(raw)["template_workflow_id"]
        store.access["tok-tmpl_coeditor"].add("opp:523")
        _follower(store)
        _ok(_follow(co_editor[1], tid=tid))

    def test_an_unshared_template_cannot_be_followed_from_outside_its_scope(self, store, owner, follower_only):
        _, raw = owner
        tid = _create(raw)["template_workflow_id"]
        _follower(store, 900, OTHER_PROGRAM_OPP)
        err = _err(_follow(follower_only[1], 900, OTHER_PROGRAM_OPP, tid=tid))
        assert err["code"] == "PERMISSION_DENIED" and "workflow_template_set_sharing" in err["message"]

    def test_a_shared_template_is_followed_through_the_public_read(self, store, owner, follower_only):
        _, raw = owner
        tid = _create(raw)["template_workflow_id"]
        out = _ok(
            call_tool(
                raw, "workflow_template_set_sharing", {"template_workflow_id": tid, **TEMPLATE_SCOPE, "public": True}
            )
        )
        assert out["sharing"]["public"] is True
        assert all(r["public"] for r in store.rows.values() if r["type"] == "workflow_template_version")
        assert not any(r["public"] for r in store.rows.values() if r["type"] == "workflow_template_draft")
        _follower(store, 900, OTHER_PROGRAM_OPP)
        out = _ok(_follow(follower_only[1], 900, OTHER_PROGRAM_OPP, tid=tid))
        # The follow works; the template's list is the owners' to write.
        assert out["listed_on_template"] is False
        # Its viewers render the template although they are not members of program 77.
        _, code, source = _page(store, "tok-tmpl_follower", 900, OTHER_PROGRAM_OPP)
        assert code == TEMPLATE_RENDER and source["source"] == "template_workflow"

    def test_unsharing_drops_outside_followers_back_to_their_stored_copy_with_a_warning(
        self, store, owner, follower_only
    ):
        _, raw = owner
        tid = _create(raw, public=True)["template_workflow_id"]
        _follower(store, 900, OTHER_PROGRAM_OPP)
        _ok(_follow(follower_only[1], 900, OTHER_PROGRAM_OPP, tid=tid))
        _ok(
            call_tool(
                raw, "workflow_template_set_sharing", {"template_workflow_id": tid, **TEMPLATE_SCOPE, "public": False}
            )
        )
        _, code, source = _page(store, "tok-tmpl_follower", 900, OTHER_PROGRAM_OPP)
        assert code == "function S(){return 'stored copy'}" and "not readable here" in source["warning"]

    def test_a_reader_sees_the_published_template_never_the_draft(self, store, owner, follower_only):
        _, raw = owner
        tid = _create(raw, public=True)["template_workflow_id"]
        _ok(_edit(raw, tid, 1, "published v1", "secret draft"))
        out = _ok(
            call_tool(
                follower_only[1],
                "workflow_template_get",
                {"template_workflow_id": tid, **TEMPLATE_SCOPE, "include_code": True},
            )
        )
        assert out["draft"] is None and out["you_can_edit"] is False
        assert out["published"]["render_code"] == TEMPLATE_RENDER
        assert [v["number"] for v in out["versions"]] == [1]


# -----------------------------------------------------------------------------
# Draft, preview, publish, rollback
# -----------------------------------------------------------------------------


@pytest.mark.django_db
class TestDraftPreviewPublish:
    def test_a_draft_does_not_reach_followers_until_published(self, store, owner, follower_only):
        _, raw = owner
        tid = _create(raw, public=True)["template_workflow_id"]
        _follower(store)
        _ok(_follow(raw, tid=tid))
        _ok(_edit(raw, tid, 1, "published v1", "draft v2"))
        assert _page(store)[1] == TEMPLATE_RENDER

        # Preview: someone who can write the template's scope sees the draft...
        with tw.preview_drafts():
            _, code, source = _page(store)
        assert "draft v2" in code and source["draft"] is True and source["draft_revision"] == 2
        # ...and a viewer who can only read it does not, even asking for it.
        with tw.preview_drafts():
            assert _page(store, "tok-tmpl_follower")[1] == TEMPLATE_RENDER

        out = _ok(call_tool(raw, "workflow_template_preview", {"template_workflow_id": tid, **TEMPLATE_SCOPE}))
        assert any("draft v2" in line for line in out["render_diff"])
        assert out["draft"]["differs_from_published"] is True

        _ok(
            call_tool(
                raw,
                "workflow_template_publish",
                {"template_workflow_id": tid, **TEMPLATE_SCOPE, "note": "v2", "expected_revision": 2},
            )
        )
        _, code, source = _page(store)
        assert "draft v2" in code and source["version"] == 2
        # A new version of a shared template is shared too.
        assert all(r["public"] for r in store.rows.values() if r["type"] == "workflow_template_version")

    def test_a_stale_draft_edit_is_refused(self, store, owner):
        _, raw = owner
        tid = _create(raw)["template_workflow_id"]
        _ok(_edit(raw, tid, 1, "published v1", "a"))
        assert _err(_edit(raw, tid, 1, "a", "b"))["code"] == "VERSION_CONFLICT"

    def test_an_edit_must_match_exactly_once(self, store, owner):
        _, raw = owner
        tid = _create(raw)["template_workflow_id"]
        assert _err(_edit(raw, tid, 1, "not in the render", "x"))["code"] == "INVALID_SCHEMA"

    def test_rollback_publishes_a_copy_of_the_old_version(self, store, owner):
        _, raw = owner
        tid = _create(raw)["template_workflow_id"]
        _follower(store)
        _ok(_follow(raw, tid=tid))
        base = {"template_workflow_id": tid, **TEMPLATE_SCOPE}
        _ok(_edit(raw, tid, 1, "published v1", "broken v2"))
        _ok(call_tool(raw, "workflow_template_publish", base))
        assert "broken v2" in _page(store)[1]

        out = _ok(call_tool(raw, "workflow_template_rollback", {**base, "to_version": 1}))
        assert out["published_now"] == 3 and out["restores_version"] == 1
        assert [v["number"] for v in out["versions"]] == [3, 2, 1]
        assert out["versions"][0]["restores_version"] == 1 and out["versions"][0]["live"] is True
        _, code, source = _page(store)
        assert code == TEMPLATE_RENDER and source["version"] == 3
        assert out["draft"]["differs_from_published"] is True
        out = _ok(call_tool(raw, "workflow_template_rollback", {**base, "to_version": 1, "reset_draft": True}))
        assert out["draft"]["differs_from_published"] is False

    def test_rollback_to_a_missing_version(self, store, owner):
        _, raw = owner
        tid = _create(raw)["template_workflow_id"]
        err = _err(
            call_tool(
                raw, "workflow_template_rollback", {"template_workflow_id": tid, **TEMPLATE_SCOPE, "to_version": 9}
            )
        )
        assert err["code"] == "NOT_FOUND"

    def test_list_shows_the_scopes_templates_and_the_public_ones(self, store, owner, follower_only):
        _, raw = owner
        mine = _create(raw)["template_workflow_id"]
        shared = _create(raw, public=True, name="Shared")["template_workflow_id"]
        out = _ok(call_tool(raw, "workflow_template_list", TEMPLATE_SCOPE))
        assert {t["template_workflow_id"] for t in out["templates"]} == {mine, shared}
        out = _ok(call_tool(follower_only[1], "workflow_template_list", {}))
        assert [(t["template_workflow_id"], t["you_can_edit"]) for t in out["templates"]] == [(shared, False)]


# -----------------------------------------------------------------------------
# The way back: export a template's changes for a code PR
# -----------------------------------------------------------------------------


@pytest.mark.django_db
class TestExport:
    def _publish(self, raw, tid, rev, old, new, note):
        _ok(_edit(raw, tid, rev, old, new))
        _ok(
            call_tool(
                raw,
                "workflow_template_publish",
                {"template_workflow_id": tid, **TEMPLATE_SCOPE, "note": note, "expected_revision": rev + 1},
            )
        )

    def test_the_published_render_comes_back_as_a_diff_against_its_seed(self, store, owner):
        _, raw = owner
        tid = _create(raw)["template_workflow_id"]
        self._publish(raw, tid, 1, "published v1", "fix one", "first fix")
        self._publish(raw, tid, 2, "fix one", "fix two", "second fix")
        args = {"template_workflow_id": tid, **TEMPLATE_SCOPE}
        out = _ok(call_tool(raw, "workflow_template_export", {**args, "include_code": True}))
        assert out["base"]["version"] == 1 and out["target"] == "v3"
        assert [c["note"] for c in out["changes"]] == ["first fix", "second fix"]
        assert out["base_code"] == TEMPLATE_RENDER and "fix two" in out["code"]
        assert "-function R(){return 'published v1'}" in out["diff"]
        assert "+function R(){return 'fix two'}" in out["diff"]
        # An inline code template has no file to merge into.
        assert out["code_template"] == {"path": None, "deployed_equals_base": None}
        # Without include_code the payload carries the diff only.
        slim = _ok(call_tool(raw, "workflow_template_export", {**args, "version": 2}))
        assert "code" not in slim and slim["target"] == "v2" and len(slim["changes"]) == 1

    def test_a_reader_exports_what_is_published_but_not_the_draft(self, store, owner, follower_only):
        _, raw = owner
        _, f_raw = follower_only
        tid = _create(raw, public=True)["template_workflow_id"]
        self._publish(raw, tid, 1, "published v1", "shared fix", "fix")
        args = {"template_workflow_id": tid, **TEMPLATE_SCOPE}
        assert "+function R(){return 'shared fix'}" in _ok(call_tool(f_raw, "workflow_template_export", args))["diff"]
        assert (
            _err(call_tool(f_raw, "workflow_template_export", {**args, "draft": True}))["code"] == "PERMISSION_DENIED"
        )

    def test_a_file_backed_code_template_names_its_render_file(self):
        assert (
            tw.code_template_path("indicator_worker_review")
            == "connect_labs/workflow/templates/indicator_worker_review_render.js"
        )
        # The programme and opportunity reports share one render.
        assert tw.code_template_path("indicator_opp_report") == tw.code_template_path("indicator_programme_report")
