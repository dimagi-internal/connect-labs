"""Template workflows: a report's render, config defaults and snapshot spec as DATA.

The KMC programme report needed a PR, a merge and a production deploy for each of
three layout tweaks on 2026-10-05 (connect-labs#2229). A template workflow holds
that render as data with a draft, published versions and a rollback; followers show
the published version on their next load. These tests pin: follow, inherit,
override, permissions, draft vs publish, preview, and rollback.
"""

from __future__ import annotations

import copy
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

from connect_labs.labs.models import LocalLabsRecord, UserConnectToken
from connect_labs.mcp.models import MCPAccessToken
from connect_labs.mcp.testing import call_tool
from connect_labs.users.models import User
from connect_labs.workflow import render_source as rs
from connect_labs.workflow import template_workflows as tw
from connect_labs.workflow.data_access import WorkflowDataAccess, WorkflowDefinitionRecord
from connect_labs.workflow.models import TemplateWorkflow
from connect_labs.workflow.templates import TEMPLATES

KEY = "__tv_template_workflow__"
TEMPLATE_RENDER = "function R(){return 'published v1'}"


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


def _person(username):
    user = User.objects.create(username=username)
    _, raw = MCPAccessToken.create_token(user, name="t")
    UserConnectToken.objects.create(user=user, access_token="tok", expires_at=timezone.now() + timedelta(hours=1))
    return user, raw


@pytest.fixture
def owner(db):
    return _person("tmpl_owner")


@pytest.fixture
def other(db):
    return _person("tmpl_other")


class FakeStore:
    """An in-memory LabsRecord store behind a WorkflowDataAccess-shaped fake.

    The read/write hooks are the REAL ones (`apply_to_record`, `strip_inherited`), so
    these tests exercise the inheritance the production data access applies.
    """

    def __init__(self):
        self.defs: dict[tuple, dict] = {}
        self.renders: dict[tuple, dict] = {}
        self.unreadable: set[tuple] = set()
        self.next_id = 500

    def put(self, wid, scope, data, render=None):
        self.defs[(wid, _sk(scope))] = copy.deepcopy(data)
        if render is not None:
            self.renders[(wid, _sk(scope))] = {"component_code": render, "version": 1}

    def raw(self, wid, scope):
        return self.defs[(wid, _sk(scope))]

    def dao(self, access_token=None, opportunity_id=None, program_id=None, **_):
        return FakeDAO(self, {"opportunity_id": opportunity_id} if opportunity_id else {"program_id": program_id})


def _sk(scope):
    return tw.scope_key(scope.get("opportunity_id"), scope.get("program_id"))


class FakeDAO:
    def __init__(self, store, scope):
        self.store, self.scope = store, scope

    def _record(self, wid):
        key = (wid, _sk(self.scope))
        if key not in self.store.defs or key in self.store.unreadable:
            return None
        return WorkflowDefinitionRecord(
            {
                "id": wid,
                "experiment": "x",
                "type": "workflow_definition",
                "data": copy.deepcopy(self.store.defs[key]),
                "opportunity_id": self.scope.get("opportunity_id"),
            }
        )

    def get_definition(self, wid):
        return tw.apply_to_record(self._record(wid))

    def update_definition(self, definition_id, data):
        self.store.defs[(definition_id, _sk(self.scope))] = copy.deepcopy(tw.strip_inherited(data))
        return self._record(definition_id)

    def create_definition(self, name, description, **kwargs):
        self.store.next_id += 1
        wid = self.store.next_id
        self.store.put(wid, self.scope, {"name": name, "description": description, "version": 1, **kwargs})
        return self._record(wid)

    def get_render_code(self, wid):
        r = self.store.renders.get((wid, _sk(self.scope)))
        if not r:
            return None
        rec = MagicMock()
        rec.data = dict(r)
        rec.version = r["version"]
        rec.component_code = r["component_code"]
        return rec

    def save_render_code(self, definition_id, component_code, version=1):
        self.store.renders[(definition_id, _sk(self.scope))] = {"component_code": component_code, "version": version}

    def close(self):
        pass


@pytest.fixture
def store():
    s = FakeStore()
    with patch("connect_labs.mcp.tools.workflow_templates_data.WorkflowDataAccess", side_effect=s.dao):
        yield s


def _ok(resp):
    assert resp["result"]["isError"] is False, resp
    return resp["result"]["structuredContent"]


def _err(resp):
    assert resp["result"]["isError"] is True, resp
    return resp["result"]["structuredContent"]["error"]


TEMPLATE_SCOPE = {"program_id": 77}
FOLLOWER_SCOPE = {"opportunity_id": 523}
TWIN_SCOPE = {"opportunity_id": 10042}


def _create(raw, **extra):
    return _ok(
        call_tool(
            raw,
            "workflow_template_create",
            {
                "name": "KMC Programme Metrics",
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
    store.put(wid, scope, data, render="function S(){return 'stored copy'}")
    return wid


def _follow(raw, wid=19778, scope=FOLLOWER_SCOPE, tid=None, **extra):
    return call_tool(
        raw,
        "workflow_follow_template",
        {"workflow_id": wid, **scope, "template_workflow_id": tid, "template_program_id": 77, **extra},
    )


def _page(store, wid=19778, scope=FOLLOWER_SCOPE):
    dao = store.dao(**scope)
    definition = dao.get_definition(wid)
    code, source = rs.resolve_render_code(dao, definition)
    return definition, code, source


@pytest.mark.django_db
class TestCreate:
    def test_seeds_from_a_code_template_and_publishes_v1(self, store, owner):
        _, raw = owner
        out = _create(raw)
        assert out["published_version"] == 1 and out["owners"] == ["tmpl_owner"]
        assert out["seeded_from"] == f"code:{KEY}" and out["template_type"] == KEY
        tid = out["template_workflow_id"]
        # The template's own record follows itself, so its page shows the published render.
        record = store.raw(tid, TEMPLATE_SCOPE)
        assert record["is_template"] is True and record["template_scope"] == "program:77"
        assert record["render_source"] == {"workflow": tid, "program_id": 77}
        _, code, source = _page(store, tid, TEMPLATE_SCOPE)
        assert code == TEMPLATE_RENDER and source["source"] == "template_workflow"

    def test_global_scope_is_admin_only(self, store, owner):
        _, raw = owner
        err = _err(
            call_tool(
                raw,
                "workflow_template_create",
                {"name": "x", "template_scope": "global", "template_key": KEY, **TEMPLATE_SCOPE},
            )
        )
        assert err["code"] == "PERMISSION_DENIED"


@pytest.mark.django_db
class TestFollowInheritOverride:
    def test_follow_renders_published_and_inherits_with_own_keys_winning(self, store, owner):
        _, raw = owner
        tid = _create(raw)["template_workflow_id"]
        _follower(store, enrollment_targets={"goal": 12771}, showFilters=True)
        out = _ok(_follow(raw, tid=tid))

        # Keys equal to the template's became inherited; real overrides stayed.
        assert out["now_inherited"]["config"] == ["chart_order", "templateType"]
        assert out["overrides"]["config"] == ["enrollment_targets", "showFilters"]
        assert out["now_inherited"]["snapshot_inputs"] == ["builder", "scopes", "series"]
        raw_record = store.raw(19778, FOLLOWER_SCOPE)
        assert "snapshot_inputs" not in raw_record
        assert raw_record["config"] == {"enrollment_targets": {"goal": 12771}, "showFilters": True}

        definition, code, source = _page(store)
        assert code == TEMPLATE_RENDER  # the template, not the stored copy
        assert source == {"source": "template_workflow", "template_workflow": tid, "version": 1, "draft": False}
        cfg = definition.data["config"]
        assert cfg["chart_order"] == ["a", "b"] and cfg["templateType"] == KEY  # inherited
        assert cfg["showFilters"] is True and cfg["enrollment_targets"] == {"goal": 12771}  # own wins
        assert definition.data["snapshot_inputs"]["series"] == ["KMC"]  # inherited spec
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
        assert cfg["chart_order"] == ["b", "a"]  # inherited value moved with the template
        assert cfg["showFilters"] is True  # the follower's override held

    def test_a_follower_snapshot_inputs_key_overrides_the_template(self, store, owner):
        _, raw = owner
        tid = _create(raw)["template_workflow_id"]
        _follower(store)
        store.raw(19778, FOLLOWER_SCOPE)["snapshot_inputs"]["scopes"] = ["programme", "llo"]
        _ok(_follow(raw, tid=tid))
        inputs = _page(store)[0].data["snapshot_inputs"]
        assert inputs["scopes"] == ["programme", "llo"] and inputs["builder"] == "semantic_snapshot"

    def test_a_read_modify_write_does_not_bake_inherited_values_in(self, store, owner):
        _, raw = owner
        tid = _create(raw)["template_workflow_id"]
        _follower(store, showFilters=True)
        _ok(_follow(raw, tid=tid))
        dao = store.dao(**FOLLOWER_SCOPE)
        effective = dao.get_definition(19778).data
        dao.update_definition(19778, {**effective, "name": "renamed"})
        assert store.raw(19778, FOLLOWER_SCOPE)["config"] == {"showFilters": True}

    def test_a_different_template_type_cannot_follow(self, store, owner):
        _, raw = owner
        tid = _create(raw)["template_workflow_id"]
        _follower(store)
        store.raw(19778, FOLLOWER_SCOPE)["config"]["templateType"] = "kmc_opp_report"
        assert _err(_follow(raw, tid=tid))["code"] == "INVALID_SCHEMA"

    def test_stored_render_edits_are_refused_while_following(self, store, owner):
        _, raw = owner
        tid = _create(raw)["template_workflow_id"]
        _follower(store)
        _ok(_follow(raw, tid=tid))
        with pytest.raises(rs.RenderFollowsTemplateWorkflow):
            rs.refuse_edit_if_following(store.dao(**FOLLOWER_SCOPE).get_definition(19778))

    def test_unfollow_forks_without_the_page_changing(self, store, owner):
        _, raw = owner
        tid = _create(raw)["template_workflow_id"]
        _follower(store, showFilters=True)
        _ok(_follow(raw, tid=tid))
        before, _, _ = _page(store)
        _ok(_follow(raw, follow=False))
        after, code, source = _page(store)
        assert source["source"] == "stored" and code == TEMPLATE_RENDER
        assert after.data["config"] == before.data["config"]
        assert after.data["snapshot_inputs"] == before.data["snapshot_inputs"]
        assert not TemplateWorkflow.objects.get(workflow_id=tid).followers.exists()

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


@pytest.mark.django_db
class TestPermissions:
    def test_only_owners_edit_publish_roll_back_and_preview(self, store, owner, other):
        _, raw = owner
        _, other_raw = other
        tid = _create(raw)["template_workflow_id"]
        base = {"template_workflow_id": tid, **TEMPLATE_SCOPE}
        for tool, args in [
            ("workflow_template_update_draft", {"expected_revision": 1, "render_code": "x"}),
            ("workflow_template_publish", {}),
            ("workflow_template_rollback", {"to_version": 1}),
            ("workflow_template_preview", {}),
        ]:
            assert _err(call_tool(other_raw, tool, {**base, **args}))["code"] == "PERMISSION_DENIED", tool

    def test_an_added_owner_can_edit(self, store, owner, other):
        _, raw = owner
        _, other_raw = other
        tid = _create(raw)["template_workflow_id"]
        base = {"template_workflow_id": tid, **TEMPLATE_SCOPE}
        _ok(call_tool(raw, "workflow_template_set_owners", {**base, "add": ["tmpl_other"]}))
        _ok(
            call_tool(
                other_raw, "workflow_template_update_draft", {**base, "expected_revision": 1, "render_code": "y"}
            )
        )

    def test_anyone_who_can_read_the_template_may_follow(self, store, owner, other):
        _, raw = owner
        _, other_raw = other
        tid = _create(raw)["template_workflow_id"]
        _follower(store)
        _ok(_follow(other_raw, tid=tid))

    def test_someone_who_cannot_read_it_may_not(self, store, owner, other):
        _, raw = owner
        _, other_raw = other
        tid = _create(raw)["template_workflow_id"]
        _follower(store)
        store.unreadable.add((tid, "program:77"))
        assert _err(_follow(other_raw, tid=tid))["code"] == "PERMISSION_DENIED"

    def test_a_global_template_needs_no_read_of_its_scope(self, store, owner, other):
        _, raw = owner
        _, other_raw = other
        tid = _create(raw)["template_workflow_id"]
        TemplateWorkflow.objects.filter(workflow_id=tid).update(template_scope="global")
        _follower(store)
        store.unreadable.add((tid, "program:77"))
        _ok(_follow(other_raw, tid=tid))

    def test_draft_content_is_hidden_from_non_owners(self, store, owner, other):
        _, raw = owner
        _, other_raw = other
        tid = _create(raw)["template_workflow_id"]
        _ok(
            call_tool(
                raw,
                "workflow_template_update_draft",
                {
                    "template_workflow_id": tid,
                    **TEMPLATE_SCOPE,
                    "expected_revision": 1,
                    "edits": [{"old": "published v1", "new": "secret draft"}],
                },
            )
        )
        out = _ok(
            call_tool(
                other_raw,
                "workflow_template_get",
                {"template_workflow_id": tid, **TEMPLATE_SCOPE, "include_code": True},
            )
        )
        assert "render_code" not in out["draft"] and out["published"]["render_code"] == TEMPLATE_RENDER


@pytest.mark.django_db
class TestDraftPreviewPublish:
    def _edit(self, raw, tid, rev, old, new):
        return call_tool(
            raw,
            "workflow_template_update_draft",
            {
                "template_workflow_id": tid,
                **TEMPLATE_SCOPE,
                "expected_revision": rev,
                "edits": [{"old": old, "new": new}],
            },
        )

    def test_a_draft_does_not_reach_followers_until_published(self, store, owner):
        user, raw = owner
        tid = _create(raw)["template_workflow_id"]
        _follower(store)
        _ok(_follow(raw, tid=tid))
        _ok(self._edit(raw, tid, 1, "published v1", "draft v2"))
        assert _page(store)[1] == TEMPLATE_RENDER  # followers: still published

        # Preview: the owner sees the follower rendered with the draft...
        with tw.preview_drafts(user):
            _, code, source = _page(store)
        assert "draft v2" in code and source["draft"] is True and source["draft_revision"] == 2
        # ...and nobody else does, even asking for it.
        stranger = User.objects.create(username="stranger")
        with tw.preview_drafts(stranger):
            assert _page(store)[1] == TEMPLATE_RENDER

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

    def test_a_stale_draft_edit_is_refused(self, store, owner):
        _, raw = owner
        tid = _create(raw)["template_workflow_id"]
        _ok(self._edit(raw, tid, 1, "published v1", "a"))
        assert _err(self._edit(raw, tid, 1, "a", "b"))["code"] == "VERSION_CONFLICT"

    def test_an_edit_must_match_exactly_once(self, store, owner):
        _, raw = owner
        tid = _create(raw)["template_workflow_id"]
        assert _err(self._edit(raw, tid, 1, "not in the render", "x"))["code"] == "INVALID_SCHEMA"

    def test_rollback_publishes_a_copy_of_the_old_version(self, store, owner):
        _, raw = owner
        tid = _create(raw)["template_workflow_id"]
        _follower(store)
        _ok(_follow(raw, tid=tid))
        base = {"template_workflow_id": tid, **TEMPLATE_SCOPE}
        _ok(self._edit(raw, tid, 1, "published v1", "broken v2"))
        _ok(call_tool(raw, "workflow_template_publish", base))
        assert "broken v2" in _page(store)[1]

        out = _ok(call_tool(raw, "workflow_template_rollback", {**base, "to_version": 1}))
        assert out["published_now"] == 3 and out["restores_version"] == 1
        assert [v["number"] for v in out["versions"]] == [3, 2, 1]
        assert out["versions"][0]["restores_version"] == 1 and out["versions"][0]["live"] is True
        _, code, source = _page(store)
        assert code == TEMPLATE_RENDER and source["version"] == 3
        # The draft keeps the unpublished edit unless asked to reset.
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


@pytest.mark.django_db
class TestTheRealDataAccessHooks:
    """`WorkflowDataAccess` itself applies the effective view on read and strips on write."""

    def _dao(self, record_data):
        dao = WorkflowDataAccess.__new__(WorkflowDataAccess)
        dao.labs_api = MagicMock()
        dao.labs_api.get_record_by_id.return_value = LocalLabsRecord(
            {"id": 9, "experiment": "x", "type": "workflow_definition", "data": record_data, "opportunity_id": 523}
        )
        dao.labs_api.update_record.return_value = None
        return dao

    def test_read_and_write(self, owner):
        user, _ = owner
        t = tw.create_template(
            user=user,
            workflow_id=1,
            opportunity_id=None,
            program_id=77,
            name="t",
            template_scope="program:77",
            render_code="r",
            config={"templateType": KEY, "a": 1},
            snapshot_inputs={"builder": "b"},
            seeded_from="test",
        )
        follow = {"config": {"templateType": KEY, "own": 2}, "render_source": tw.render_source_for(t)}
        dao = self._dao(follow)
        record = dao.get_definition(9)
        assert record.data["config"] == {"templateType": KEY, "a": 1, "own": 2}
        assert record.data["snapshot_inputs"] == {"builder": "b"}
        dao.update_definition(9, record.data)
        written = dao.labs_api.update_record.call_args.kwargs["data"]
        assert written["config"] == {"own": 2} and "snapshot_inputs" not in written

    def test_a_workflow_that_follows_nothing_is_untouched(self, db):
        data = {"config": {"x": 1}}
        record = self._dao(data).get_definition(9)
        assert record.data == data
