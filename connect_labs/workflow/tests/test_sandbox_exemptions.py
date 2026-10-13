"""Recorded sandbox exemptions: a live copy kept out of the frame under `all`, by hand.

Spec: docs/superpowers/specs/2026-10-12-page-sandbox-design.md, "Recorded exemptions".
THIS REPOSITORY IS PUBLIC. Every name here is invented.
"""

from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError

from connect_labs.audit_trail.models import AuditEvent
from connect_labs.workflow.models import SandboxExemption
from connect_labs.workflow.sandbox import render_code_digest, sandbox_exemption, should_sandbox
from connect_labs.workflow.sandbox_scan_sources import _target

CODE = "function W(){ return fetch('/custom_analysis/retired/api/x/').then(() => null); }"
EDITED = CODE + "\n// one more line"
WORKFLOW = {"name": "Old supervisor board", "config": {}}
PAGE = {"name": "Old supervisor board", "kind": "page", "config": {}}


def _defn(id_=4101, data=WORKFLOW, opportunity_id=7401, program_id=None):
    return SimpleNamespace(
        id=id_, data=data, opportunity_id=opportunity_id, program_id=program_id, organization_id=None
    )


def _exempt(definition_id=4101, code=CODE, **owner):
    owner = owner or {"opportunity_id": 7401}
    return SandboxExemption.objects.create(
        definition_id=definition_id,
        render_code_sha256=render_code_digest(code),
        reason="needs a retired endpoint; kept readable",
        added_by="field.admin",
        **owner,
    )


def _all(definition, code=CODE, **kw):
    return should_sandbox(definition, mode="all", render_code=code, **kw)


# --- the decision ----------------------------------------------------------------


@pytest.mark.django_db
def test_a_recorded_copy_drawing_the_recorded_code_stays_out_under_all():
    assert _all(_defn())  # no row: sandboxed
    _exempt()
    assert not _all(_defn())
    # The same code with CRLF and outer whitespace is the same digest.
    assert not _all(_defn(), code="\n" + CODE.replace("\n", "\r\n") + "  ")
    assert sandbox_exemption(_defn(), CODE)["reason"] == "needs a retired endpoint; kept readable"
    # ?sandbox=1 still draws it sandboxed; opt_in is unchanged by the row.
    assert _all(_defn(), request=SimpleNamespace(GET={"sandbox": "1"}))
    assert not should_sandbox(_defn(), mode="opt_in", render_code=CODE)


@pytest.mark.django_db
def test_an_edit_to_the_code_voids_the_exemption():
    _exempt()
    assert _all(_defn(), code=EDITED)
    assert _all(_defn(), code=None)
    assert _all(_defn(), code="")


@pytest.mark.django_db
def test_the_same_id_on_another_owner_is_sandboxed():
    _exempt()
    assert _all(_defn(opportunity_id=7402))
    assert _all(_defn(opportunity_id=None, program_id=7401))
    _exempt(definition_id=4102, program_id=176)
    assert not _all(_defn(4102, opportunity_id=None, program_id=176))
    assert _all(_defn(4102, opportunity_id=None, program_id=177))


@pytest.mark.django_db
def test_a_page_ignores_exemptions():
    _exempt()
    assert _all(_defn(data=PAGE))
    # ...and a workflow drawn as a page (a page's address, a supply tab).
    assert should_sandbox(_defn(), page_mode=True, mode="all", render_code=CODE)


# --- the scan reports it ----------------------------------------------------------


@pytest.mark.django_db
def test_the_scan_reports_a_recorded_copy_as_kept_out_and_an_edited_one_as_breaking():
    _exempt()
    breaking = "function W(){ window.parent.postMessage('x', '*'); return null; }"
    SandboxExemption.objects.filter(definition_id=4101).update(render_code_sha256=render_code_digest(breaking))
    kept = _target("connect", 4101, WORKFLOW, "opportunity:7401", breaking, "stored")
    assert kept.worst == "breaks" and not kept.sandboxed_all and kept.exempt == "recorded"
    assert "kept out (exempt: needs a retired endpoint; kept readable)" in kept.note
    edited = _target("connect", 4101, WORKFLOW, "opportunity:7401", breaking + "\n// x", "stored")
    assert edited.worst == "breaks" and edited.sandboxed_all and edited.exempt == ""
    assert "recorded exemption void (its code changed since)" in edited.note
    moved = _target("connect", 4101, WORKFLOW, "opportunity:7402", breaking, "stored")
    assert moved.sandboxed_all and "its owner is not the one recorded" in moved.note


# --- the command ------------------------------------------------------------------


class _FakeDA:
    definition = None
    seen_scope = None

    def __init__(self, access_token=None, **scope):
        _FakeDA.seen_scope = scope
        self.labs_api = None

    def get_definition(self, definition_id):
        d = _FakeDA.definition
        return d if d is not None and d.id == definition_id else None

    def close(self):
        pass


@pytest.fixture
def admin_user(db):
    return get_user_model().objects.create(username="field.admin")


def _run(*args, code=CODE, definition=None):
    _FakeDA.definition = definition if definition is not None else _defn()
    out = StringIO()
    with (
        patch("connect_labs.labs.connect_tokens.get_valid_access_token", return_value="t"),
        patch("connect_labs.workflow.data_access.WorkflowDataAccess", _FakeDA),
        patch("connect_labs.workflow.render_source.resolve_render_code", return_value=(code, {"source": "stored"})),
    ):
        call_command("workflow_sandbox_exempt", *args, stdout=out)
    return out.getvalue()


ADD = ("--definition", "4101", "--opportunity", "7401", "--as-user", "field.admin", "--reason", "retired endpoint")


@pytest.mark.django_db
def test_the_command_records_the_digest_of_the_code_read_as_the_user(admin_user):
    text = _run(*ADD)
    row = SandboxExemption.objects.get(definition_id=4101)
    assert (row.opportunity_id, row.program_id, row.added_by) == (7401, None, "field.admin")
    assert row.render_code_sha256 == render_code_digest(CODE) and row.reason == "retired endpoint"
    assert _FakeDA.seen_scope == {"opportunity_id": 7401}
    assert "recorded: definition 4101 (opportunity:7401)" in text and CODE not in text
    assert not _all(_defn())
    event = AuditEvent.objects.get(resource_type="sandbox_exemption", action="create")
    assert event.resource_id == "4101" and event.username == "field.admin"
    # Run again after an edit: the row is updated to the new code (a deliberate re-record).
    _run(*ADD, code=EDITED)
    assert SandboxExemption.objects.get(definition_id=4101).render_code_sha256 == render_code_digest(EDITED)
    assert AuditEvent.objects.filter(resource_type="sandbox_exemption", action="update").count() == 1


@pytest.mark.django_db
def test_the_command_removes_and_lists(admin_user):
    _run(*ADD)
    listing = StringIO()
    call_command("workflow_sandbox_exempt", "--list", stdout=listing)
    assert "4101 | opportunity:7401 |" in listing.getvalue() and "retired endpoint" in listing.getvalue()
    call_command(
        "workflow_sandbox_exempt", "--definition", "4101", "--remove", "--as-user", "field.admin", stdout=StringIO()
    )
    assert not SandboxExemption.objects.exists()
    assert _all(_defn())
    assert AuditEvent.objects.filter(resource_type="sandbox_exemption", action="delete").count() == 1
    with pytest.raises(CommandError):
        call_command("workflow_sandbox_exempt", "--definition", "4101", "--remove", stdout=StringIO())


@pytest.mark.django_db
@pytest.mark.parametrize(
    "args, definition, code, message",
    [
        # The owner named is not the definition's.
        (("--definition", "4101", "--opportunity", "7402"), None, CODE, "owned by opportunity:7401"),
        # A page is never exempt.
        (("--definition", "4101", "--opportunity", "7401"), _defn(data=PAGE), CODE, "is a page"),
        # Nothing to hash.
        (("--definition", "4101", "--opportunity", "7401"), None, "", "no render code"),
        # Not readable as this user.
        (("--definition", "4999", "--opportunity", "7401"), None, CODE, "not readable"),
    ],
)
def test_the_command_refuses(admin_user, args, definition, code, message):
    with pytest.raises(CommandError, match=message):
        _run(*args, "--as-user", "field.admin", "--reason", "r", code=code, definition=definition)
    assert not SandboxExemption.objects.exists()


@pytest.mark.django_db
def test_the_command_needs_a_reason_an_owner_and_a_user(admin_user):
    with pytest.raises(CommandError, match="--reason"):
        _run("--definition", "4101", "--opportunity", "7401", "--as-user", "field.admin")
    with pytest.raises(CommandError, match="owner"):
        _run("--definition", "4101", "--as-user", "field.admin", "--reason", "r")
    with pytest.raises(CommandError, match="--as-user"):
        _run("--definition", "4101", "--opportunity", "7401", "--reason", "r")


# --- the run page -----------------------------------------------------------------


def _render_run_as(is_staff):
    from django.test import override_settings

    from connect_labs.workflow.data_access import WorkflowRunRecord
    from connect_labs.workflow.tests.test_page_mode import _request
    from connect_labs.workflow.tests.test_page_sandbox import _wda
    from connect_labs.workflow.tests.test_workflow_sandbox import RUN_503
    from connect_labs.workflow.views import WorkflowRunView

    request = _request("/labs/workflow/47/run/?program_id=25&run_id=503")
    request.user.is_staff = is_staff
    with (
        override_settings(LABS_WORKFLOW_SANDBOX="all"),
        patch("connect_labs.workflow.views.WorkflowDataAccess") as MockWDA,
    ):
        wda = _wda(MockWDA, WORKFLOW)
        wda.get_run.return_value = WorkflowRunRecord(RUN_503)
        response = WorkflowRunView.as_view()(request, definition_id=47)
        response.render()
    return response


@pytest.mark.django_db
def test_the_run_page_draws_an_exempt_workflow_plain_and_tells_staff_why():
    # The test definition: id 47, owned by program 25, drawing this stored code.
    _exempt(47, code="function WorkflowUI() {}", program_id=25)
    staff = _render_run_as(is_staff=True)
    body = staff.content.decode()
    assert "Content-Security-Policy" not in staff and '"sandbox": {' not in body
    assert '"sandbox_exemption": {"kind": "recorded", "reason": "needs a retired endpoint; kept readable"' in body
    viewer = _render_run_as(is_staff=False).content.decode()
    assert "needs a retired endpoint" not in viewer
    # Without the row the same page is drawn in the frame.
    SandboxExemption.objects.all().delete()
    assert _render_run_as(is_staff=True)["Content-Security-Policy"].startswith("frame-src ")
