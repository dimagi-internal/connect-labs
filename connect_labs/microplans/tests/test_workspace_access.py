"""The workspace access page: LLOs and Users (``labs.workspaces``).

LLOs are partner Connect organisations: every member of one is a Network Manager
of it. Users are explicit entries by email address -- any address, signed in yet
or not -- that win over every other rule. Only a Program Manager who signs in with
a Dimagi address manages either list. Every name, slug, email and id below is
made up.
"""

from __future__ import annotations

import importlib
import time
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.urls import reverse
from django.utils import timezone

from connect_labs.audit_trail.models import Action, AuditEvent
from connect_labs.labs import workspaces
from connect_labs.labs.models import WorkspaceAccess, WorkspaceLLO, WorkspaceUser
from connect_labs.marketplace.models import OrgMembership
from connect_labs.microplans.management.commands.setup_autoconnect_workspace import ensure_workspace
from connect_labs.microplans.models import SimpleMicroplan

pytestmark = pytest.mark.django_db

RIVER = ("riverbend-health", "Riverbend Health")
HILL = ("hilltop-partners", "Hilltop Partners")


def _tree(*memberships):
    return {
        "organizations": [{"id": i + 1, "slug": slug, "name": name} for i, (slug, name) in enumerate(memberships)],
        "programs": [],
        "opportunities": [],
    }


@pytest.fixture
def trees(monkeypatch):
    """username -> Connect tree; stands in for the session / cached Connect fetch."""
    found: dict[str, dict] = {}
    monkeypatch.setattr(workspaces, "connect_org_data", lambda user: found.get(getattr(user, "username", None)))
    return found


@pytest.fixture
def prog(db):
    _org, opp, _ = ensure_workspace("Access Test Org", "Access Test Program")
    return opp.opportunity_id


@pytest.fixture
def ws(prog):
    return workspaces.workspace_for_program(prog)


def _user(django_user_model, username, email=None):
    return django_user_model.objects.create(
        username=username, email=email or f"{username}@example.org", name=username.replace("_", " ").title()
    )


@pytest.fixture
def people(django_user_model, trees, prog):
    users = {
        "pm": _user(django_user_model, "pm_staff", "pm.staff@dimagi.com"),
        "river_member": _user(django_user_model, "river_member"),
        "hill_member": _user(django_user_model, "hill_member"),
        "stranger": _user(django_user_model, "stranger"),
        "org_admin": _user(django_user_model, "org_admin"),
    }
    trees["pm_staff"] = _tree(("program-owner", "Program Owner"), RIVER)
    trees["river_member"] = _tree(RIVER)
    trees["hill_member"] = _tree(HILL)
    trees["stranger"] = _tree(("elsewhere-org", "Elsewhere Org"))
    trees["org_admin"] = _tree()
    return users


def _fresh(user):
    return type(user).objects.get(pk=user.pk)


def _login(client, user):
    client.force_login(user)
    session = client.session
    session["labs_oauth"] = {"access_token": "test-token", "expires_at": time.time() + 3600}
    session.save()


def _settings(prog):
    return reverse("microplans:program_workspace_settings", args=[prog])


def _simple_list(prog):
    return reverse("microplans:program_simple_list", args=[prog])


def _role(user, ws):
    return workspaces.role_in(_fresh(user), ws)


# --- LLOs ---------------------------------------------------------------------


def test_a_member_of_an_added_llo_is_its_network_manager_and_others_are_refused(client, people, prog, ws):
    assert _role(people["river_member"], ws) is None  # no LLOs, no linked programs yet
    _login(client, people["pm"])
    resp = client.post(_settings(prog), {"action": "add_llo", "slug": " Riverbend-Health ", "name": ""})
    assert resp.status_code == 302
    llo = WorkspaceLLO.objects.get(workspace=ws)
    # The slug is normalised, and the Program Manager's own tree names it.
    assert (llo.slug, llo.name, llo.added_by) == (RIVER[0], RIVER[1], people["pm"])
    assert AuditEvent.objects.filter(action=Action.CREATE, resource_type="workspace_llo", program_id=prog).exists()

    role = _role(people["river_member"], ws)
    assert role.role == workspaces.NETWORK_MANAGER and role.teams == (RIVER[0],) and not role.testing
    assert _role(people["hill_member"], ws) is None
    assert _role(people["stranger"], ws) is None
    # Into the program: the member yes, a non-member is told why.
    _login(client, people["river_member"])
    assert client.get(_simple_list(prog)).status_code == 200
    _login(client, people["stranger"])
    resp = client.get(_simple_list(prog))
    assert resp.status_code == 404 and b"Your Connect organisation isn" in resp.content


def test_a_member_of_two_llos_is_on_both_teams(people, ws, trees):
    WorkspaceLLO.objects.create(workspace=ws, slug=RIVER[0])
    WorkspaceLLO.objects.create(workspace=ws, slug=HILL[0], name="Hilltop")
    trees["river_member"] = _tree(RIVER, HILL)
    role = _role(people["river_member"], ws)
    assert role.teams == (RIVER[0], HILL[0])
    assert role.team_names == {RIVER[0]: RIVER[0], HILL[0]: "Hilltop"}


def test_adding_an_llo_twice_or_without_a_slug_is_refused(client, people, prog, ws):
    _login(client, people["pm"])
    for slug in (RIVER[0], RIVER[0], "  ", "two words"):
        client.post(_settings(prog), {"action": "add_llo", "slug": slug})
    assert list(WorkspaceLLO.objects.values_list("slug", flat=True)) == [RIVER[0]]


def test_deleting_an_llo_refuses_its_members_and_hands_its_microplans_to_program_managers(client, people, prog, ws):
    WorkspaceLLO.objects.create(workspace=ws, slug=RIVER[0])
    river_plan = SimpleMicroplan.objects.create(name="River grid", program_id=prog, team=RIVER[0])
    hill_plan = SimpleMicroplan.objects.create(name="Hill grid", program_id=prog, team=HILL[0])
    _login(client, people["river_member"])
    assert client.get(_simple_list(prog)).status_code == 200

    _login(client, people["pm"])
    resp = client.post(_settings(prog), {"action": "remove_llo", "slug": RIVER[0]})
    assert resp.status_code == 302
    assert not WorkspaceLLO.objects.exists()
    river_plan.refresh_from_db()
    hill_plan.refresh_from_db()
    assert (river_plan.team, hill_plan.team) == ("", HILL[0])  # nothing deleted
    assert SimpleMicroplan.objects.filter(program_id=prog).count() == 2
    event = AuditEvent.objects.get(action=Action.DELETE, resource_type="workspace_llo", program_id=prog)
    assert event.metadata["microplans_made_pm_only"] == 1

    assert _role(people["river_member"], ws) is None
    _login(client, people["river_member"])
    assert client.get(_simple_list(prog)).status_code == 404


def test_the_llo_list_shows_the_people_it_brought_in(client, people, prog, ws):
    WorkspaceLLO.objects.create(workspace=ws, slug=RIVER[0])
    _role(people["river_member"], ws)  # resolving a role notes it
    _login(client, people["pm"])
    body = client.get(_settings(prog)).content.decode()
    assert "1 person" in body and "river_member@example.org" in body


# --- recording who has had a role ------------------------------------------------


def test_a_resolved_role_is_noted_and_rewritten_only_when_stale_or_changed(people, ws, trees):
    WorkspaceLLO.objects.create(workspace=ws, slug=RIVER[0])
    _role(people["pm"], ws)
    _role(people["river_member"], ws)
    _role(people["stranger"], ws)  # no role: nothing noted
    rows = {a.user_id: a for a in WorkspaceAccess.objects.filter(workspace=ws)}
    assert set(rows) == {people["pm"].pk, people["river_member"].pk}
    assert rows[people["pm"].pk].role == workspaces.PROGRAM_MANAGER
    member = rows[people["river_member"].pk]
    assert (member.role, member.teams, member.explicit) == (workspaces.NETWORK_MANAGER, [RIVER[0]], False)

    # Seen again within the interval: not written.
    earlier = timezone.now() - timedelta(seconds=60)
    WorkspaceAccess.objects.filter(pk=member.pk).update(last_seen=earlier)
    _role(people["river_member"], ws)
    assert WorkspaceAccess.objects.get(pk=member.pk).last_seen == earlier
    # Stale: written.
    stale = timezone.now() - timedelta(seconds=workspaces.ACCESS_NOTE_INTERVAL_SECONDS + 60)
    WorkspaceAccess.objects.filter(pk=member.pk).update(last_seen=stale)
    _role(people["river_member"], ws)
    assert WorkspaceAccess.objects.get(pk=member.pk).last_seen > stale
    # Changed role: written at once.
    WorkspaceAccess.objects.filter(pk=member.pk).update(last_seen=earlier)
    WorkspaceUser.objects.create(workspace=ws, email="river_member@example.org", role="program_manager")
    _role(people["river_member"], ws)
    member = WorkspaceAccess.objects.get(pk=member.pk)
    assert member.role == workspaces.PROGRAM_MANAGER and member.last_seen > earlier


def test_a_failed_note_never_breaks_role_resolution(people, ws, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(WorkspaceAccess.objects, "filter", boom)
    assert _role(people["pm"], ws).is_program_manager


# --- Users ----------------------------------------------------------------------


def test_an_entry_for_someone_not_signed_in_yet_applies_when_they_sign_in(
    client, people, prog, ws, django_user_model, trees
):
    WorkspaceLLO.objects.create(workspace=ws, slug=RIVER[0])
    _login(client, people["pm"])
    client.post(
        _settings(prog),
        {"action": "add_user", "email": "New.Person@Partner.example", "access": f"network_manager:{RIVER[0]}"},
    )
    entry = WorkspaceUser.objects.get(workspace=ws)
    assert (entry.email, entry.user, entry.role, entry.team) == (
        "new.person@partner.example",
        None,
        "network_manager",
        RIVER[0],
    )
    # They sign in later, with the address in another case, in no LLO themselves.
    newcomer = _user(django_user_model, "new_person", "NEW.person@partner.example")
    trees["new_person"] = _tree()
    role = _role(newcomer, ws)
    assert role.role == workspaces.NETWORK_MANAGER and role.teams == (RIVER[0],) and role.testing
    entry.refresh_from_db()
    assert entry.user == newcomer
    _login(client, newcomer)
    assert client.get(_simple_list(prog)).status_code == 200


def test_switching_an_entry_between_program_manager_and_network_manager_and_its_llo(client, people, prog, ws):
    WorkspaceLLO.objects.create(workspace=ws, slug=RIVER[0])
    WorkspaceLLO.objects.create(workspace=ws, slug=HILL[0])
    entry = WorkspaceUser.objects.create(
        workspace=ws, email="stranger@example.org", role="network_manager", team=RIVER[0]
    )
    _login(client, people["pm"])

    def change(access):
        resp = client.post(_settings(prog), {"action": "change_user", "entry_id": entry.pk, "access": access})
        assert resp.status_code == 302
        entry.refresh_from_db()
        return _role(people["stranger"], ws)

    role = change(f"network_manager:{HILL[0]}")
    assert (entry.role, entry.team) == ("network_manager", HILL[0]) and role.teams == (HILL[0],)
    role = change("program_manager")
    assert (entry.role, entry.team) == ("program_manager", "") and role.is_program_manager
    role = change(f"network_manager:{RIVER[0]}")
    assert entry.role == "network_manager" and role.teams == (RIVER[0],)
    # Not to a team that is not an LLO.
    change("network_manager:made-up-team")
    assert entry.team == RIVER[0]
    assert WorkspaceUser.objects.count() == 1
    assert AuditEvent.objects.filter(action=Action.UPDATE, resource_type="workspace_user").count() == 3


def test_deleting_an_entry_falls_back_to_the_usual_rules(client, people, prog, ws):
    WorkspaceLLO.objects.create(workspace=ws, slug=RIVER[0])
    entry = WorkspaceUser.objects.create(workspace=ws, email="river_member@example.org", role="program_manager")
    assert _role(people["river_member"], ws).is_program_manager
    _login(client, people["pm"])
    client.post(_settings(prog), {"action": "remove_user", "entry_id": entry.pk})
    assert not WorkspaceUser.objects.exists()
    assert _role(people["river_member"], ws).teams == (RIVER[0],)  # a member of the LLO again


def test_any_email_can_be_added_and_a_program_manager_outside_dimagi_is_flagged(client, people, prog, ws):
    WorkspaceLLO.objects.create(workspace=ws, slug=RIVER[0])
    _login(client, people["pm"])
    client.post(
        _settings(prog),
        {"action": "add_user", "email": "someone@gmail.example", "access": f"network_manager:{RIVER[0]}"},
    )
    client.post(
        _settings(prog), {"action": "add_user", "email": "owner.personal@mail.example", "access": "program_manager"}
    )
    client.post(_settings(prog), {"action": "add_user", "email": "colleague@dimagi.com", "access": "program_manager"})
    entries = {e.email: e.role for e in WorkspaceUser.objects.all()}
    assert entries == {
        "someone@gmail.example": "network_manager",
        "owner.personal@mail.example": "program_manager",
        "colleague@dimagi.com": "program_manager",
    }
    flagged = {
        WorkspaceUser.objects.get(pk=int(e.resource_id)).email
        for e in AuditEvent.objects.filter(resource_type="workspace_user", action=Action.CREATE)
        if e.metadata.get("non_dimagi_pm")
    }
    assert flagged == {"owner.personal@mail.example"}
    # The page asks before giving an address outside Dimagi Program Manager access.
    body = client.get(_settings(prog)).content.decode()
    assert "This gives a non-Dimagi account full Program Manager access, including deleting microplans." in body
    assert "not a Dimagi address" in body


def test_a_bad_email_or_access_is_refused(client, people, prog, ws):
    _login(client, people["pm"])
    for email, access in [
        ("not-an-email", "program_manager"),
        ("a@b.example", "owner"),
        ("a@b.example", "network_manager:nope"),
    ]:
        client.post(_settings(prog), {"action": "add_user", "email": email, "access": access})
    assert not WorkspaceUser.objects.exists()


def test_an_entry_wins_over_dimagi_staff_and_org_admin(people, ws):
    WorkspaceLLO.objects.create(workspace=ws, slug=RIVER[0])
    OrgMembership.objects.create(org=ws.org, user=people["org_admin"], role="admin")
    for who in ("pm", "org_admin"):
        WorkspaceUser.objects.create(workspace=ws, email=people[who].email, role="network_manager", team=RIVER[0])
        role = _role(people[who], ws)
        assert role.role == workspaces.NETWORK_MANAGER and role.teams == (RIVER[0],), who


# --- switching yourself ---------------------------------------------------------


def test_dimagi_staff_can_view_as_a_network_manager_and_land_on_a_page_they_can_open(client, people, prog, ws):
    WorkspaceLLO.objects.create(workspace=ws, slug=RIVER[0])
    _login(client, people["pm"])
    resp = client.post(
        _settings(prog),
        {"action": "add_user", "email": "PM.Staff@dimagi.com", "access": f"network_manager:{RIVER[0]}"},
    )
    # Not the settings page, which is now a 404 for them.
    assert resp.status_code == 302 and resp["Location"] == _simple_list(prog)
    assert _role(people["pm"], ws).teams == (RIVER[0],)
    assert client.get(_settings(prog)).status_code == 404


# --- only Dimagi Program Managers ----------------------------------------------

ACCESS_ACTIONS = [
    {"action": "add_llo", "slug": "sneaky-org"},
    {"action": "remove_llo", "slug": RIVER[0]},
    {"action": "add_user", "email": "me@example.org", "access": "program_manager"},
    {"action": "change_user", "entry_id": "ENTRY", "access": "program_manager"},
    {"action": "remove_user", "entry_id": "ENTRY"},
]


@pytest.mark.parametrize("data", ACCESS_ACTIONS)
def test_every_action_is_404_for_anyone_but_a_program_manager(client, people, prog, ws, data):
    WorkspaceLLO.objects.create(workspace=ws, slug=RIVER[0])
    entry = WorkspaceUser.objects.create(
        workspace=ws, email="hill_member@example.org", role="network_manager", team=RIVER[0]
    )
    data = {k: (entry.pk if v == "ENTRY" else v) for k, v in data.items()}
    for who in ("river_member", "stranger"):
        _login(client, people[who])
        assert client.post(_settings(prog), data).status_code == 404, who
    assert list(WorkspaceLLO.objects.values_list("slug", flat=True)) == [RIVER[0]]
    entry.refresh_from_db()
    assert (entry.role, entry.team) == ("network_manager", RIVER[0])
    assert WorkspaceUser.objects.count() == 1


@pytest.mark.parametrize("how", ["users_entry", "org_admin"])
@pytest.mark.parametrize("data", ACCESS_ACTIONS)
def test_a_program_manager_outside_dimagi_cannot_manage_access(client, people, prog, ws, data, how):
    WorkspaceLLO.objects.create(workspace=ws, slug=RIVER[0])
    entry = WorkspaceUser.objects.create(
        workspace=ws, email="hill_member@example.org", role="network_manager", team=RIVER[0]
    )
    if how == "users_entry":
        outsider = people["stranger"]
        WorkspaceUser.objects.create(workspace=ws, email=outsider.email, role="program_manager")
    else:
        outsider = people["org_admin"]
        OrgMembership.objects.create(org=ws.org, user=outsider, role="admin")
    assert _role(outsider, ws).is_program_manager  # still one, for everything but access
    before = WorkspaceUser.objects.count()
    _login(client, outsider)
    assert client.get(_settings(prog)).status_code == 404
    data = {k: (entry.pk if v == "ENTRY" else v) for k, v in data.items()}
    assert client.post(_settings(prog), data).status_code == 404
    assert list(WorkspaceLLO.objects.values_list("slug", flat=True)) == [RIVER[0]]
    entry.refresh_from_db()
    assert (entry.role, entry.team) == ("network_manager", RIVER[0])
    assert WorkspaceUser.objects.count() == before
    # Nor are they shown the way in.
    body = client.get(_simple_list(prog)).content.decode()
    assert 'id="workspace-settings-link"' not in body and "Workspace settings" not in body


def test_a_dimagi_program_manager_sees_the_way_in(client, people, prog, ws):
    _login(client, people["pm"])
    assert 'id="workspace-settings-link"' in client.get(_simple_list(prog)).content.decode()
    assert client.get(_settings(prog)).status_code == 200


# --- the page -------------------------------------------------------------------


def test_both_lists_have_search_pagination_and_confirmed_deletes(client, people, prog, ws):
    for i in range(12):
        WorkspaceLLO.objects.create(workspace=ws, slug=f"llo-{i:02d}")
    WorkspaceUser.objects.create(workspace=ws, email="someone@example.org", role="program_manager")
    _login(client, people["pm"])
    body = client.get(_settings(prog)).content.decode()
    assert body.count('x-data="pagedList(10)"') == 2
    assert body.count("data-list-search") == 2
    assert body.count("data-list-pager") == 2
    assert body.count("data-row ") == 13  # every row is in the page; the browser pages them
    assert body.count('@submit.prevent="ask($event)"') == 13  # each trash icon asks first
    assert 'id="workspace-confirm"' in body
    assert "Delete the LLO llo-00?" in body and "Delete the entry for someone@example.org?" in body


# --- the data migration ------------------------------------------------------


def test_testers_are_carried_over_as_network_manager_entries(people, ws):
    migration = importlib.import_module("connect_labs.labs.migrations.0029_workspace_llos_and_users")
    testers = [
        SimpleNamespace(
            workspace_id=ws.pk,
            user=people["pm"],
            user_id=people["pm"].pk,
            team="test-llo-a",
            added_by_id=people["org_admin"].pk,
        )
    ]

    class _Rows:
        def select_related(self, *a):
            return self

        def order_by(self, *a):
            return testers

    class _Apps:
        def get_model(self, app_label, name):
            if name == "WorkspaceTester":
                return SimpleNamespace(objects=_Rows())
            from django.apps import apps

            return apps.get_model(app_label, name)

    migration.testers_to_users(_Apps(), None)
    migration.testers_to_users(_Apps(), None)  # idempotent
    entry = WorkspaceUser.objects.get()
    assert (entry.email, entry.user, entry.role, entry.team, entry.added_by) == (
        "pm.staff@dimagi.com",
        people["pm"],
        "network_manager",
        "test-llo-a",
        people["org_admin"],
    )
    # No LLO is made for a tester's team: that would let in all its members.
    assert not WorkspaceLLO.objects.exists()
