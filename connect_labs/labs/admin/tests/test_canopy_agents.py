"""Labs Admin › Canopy agents: which canopy agents labs shows, and which may read real
data, chosen in the UI from the agents canopy lists for labs (the site agents API) --
by Dimagi staff only, with history -- and labs reads them everywhere.

Every name here is invented (this repository is public).
"""

import re

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse

from connect_labs.audit_trail.models import AuditEvent
from connect_labs.labs import canopy, canopy_panel, canopy_settings
from connect_labs.labs.models import CanopyAgentSettings
from connect_labs.labs.tests.test_settings import LABS_SETTINGS
from connect_labs.testing.fake_canopy import agent, listing

pytestmark = pytest.mark.django_db

URL = "labs_admin:canopy_agents"
REAL = canopy_panel.visits(None)  # real (non-generated) visits

Tick = canopy_settings.AgentTicks


@pytest.fixture
def env(settings, fake_canopy, monkeypatch):
    settings.CANOPY_BASE_URL = "https://canopy.example"
    monkeypatch.setattr(canopy_panel, "CANOPY_EXECUTION_STATEMENTS", True)
    # canopy lists three agents for labs: the site's, a data agent that handles sensitive data, ready
    # on a sensitive-data runner, and a coach that is neither.
    fake_canopy.set(
        listing(
            agent("site-bot", description="Helps with labs"),
            agent("data-bot", handles_sensitive=True, description="Checks workflows against raw data"),
            agent("coach-bot", sensitive_data=False),
            version="v1",
        )
    )
    return fake_canopy


@pytest.fixture
def staff():
    return get_user_model().objects.create_user(username="s", email="s@dimagi.com", password="pw", is_staff=True)


@pytest.fixture
def dimagi_user():
    return get_user_model().objects.create_user(username="d", email="d@dimagi.com", password="pw")


@pytest.fixture
def outsider():
    return get_user_model().objects.create_user(username="o", email="o@partner.example", password="pw")


def _allow_data_bot(user, **kw):
    return canopy_settings.save(
        user,
        agents=[Tick("site-bot"), Tick("data-bot", real_data=True)],
        default_agent="site-bot",
        runner_requirements=[],
        listing=canopy_settings.site_agents(),
        **kw,
    )


def test_with_nothing_saved_nothing_is_set_up(env):
    """No environment variable chooses an agent: until staff save, none is offered, no
    token names a tenant, and canopy cannot act as the visitor."""
    assert canopy_settings.current() == canopy_settings.NOTHING
    assert not canopy_settings.current().is_set_up
    held = canopy_panel.availability("", canopy_panel.NO_VISIT_DATA)
    assert not held.ok and "nothing is set up yet" in held.reasons[0]
    host = canopy.host_settings()
    assert host["AGENT_SLUG"] == "" and host["CLIENT_ID"] == "" and host["RUNNER_REQUIREMENTS"] == []


def test_a_saved_change_is_what_labs_reads_everywhere(env, staff):
    assert not canopy_panel.availability("data-bot", REAL).ok
    saved = _allow_data_bot(staff)
    assert saved.site_agents_version == "v1"
    here = canopy_panel.availability("data-bot", REAL)
    assert here.ok and here.read_only
    assert (here.page_agent.agent, here.page_agent.runner_requirements) == ("data-bot", ("sensitive_data",))
    # The Data page offers it, read-only on sensitive-data runners.
    assert canopy_panel.data_page_agent().page_agent.agent == "data-bot"
    # The default agent is not allowed over real data.
    assert not canopy_panel.availability("site-bot", REAL).ok
    host = canopy.host_settings()
    assert host["AGENT_SLUG"] == "site-bot" and host["RUNNER_REQUIREMENTS"] == []


def test_only_dimagi_staff_can_change_it(env, dimagi_user, outsider):
    with pytest.raises(PermissionError):
        _allow_data_bot(outsider)
    assert not CanopyAgentSettings.objects.exists()
    # A Dimagi sign-in is enough: an OAuth-created labs user never gets Django's
    # is_staff, and no labs page grants it, so is_staff alone left nobody able to.
    assert _allow_data_bot(dimagi_user).real_data_agents == ("data-bot",)


@pytest.mark.parametrize(
    "agents, default, flags, message",
    [
        ([Tick("site-bot")], "", [], "default agent must be one labs shows"),
        ([Tick("site-bot"), Tick("Data Bot")], "site-bot", [], "not a canopy agent slug"),
        # Only agents canopy lists for labs can be shown.
        ([Tick("site-bot"), Tick("stranger-bot")], "site-bot", [], "canopy does not list stranger-bot"),
        # Real data needs canopy's sensitive-data declaration and a ready sensitive-data runner.
        ([Tick("site-bot", real_data=True)], "site-bot", [], "not declared to handle sensitive data"),
        (
            [Tick("site-bot"), Tick("coach-bot", real_data=True)],
            "site-bot",
            [],
            "not declared to handle sensitive data",
        ),
        ([Tick("site-bot")], "site-bot", ["gpu"], "Unknown runner flag"),
    ],
)
def test_choices_canopy_does_not_back_are_refused(env, staff, agents, default, flags, message):
    with pytest.raises(canopy_settings.InvalidSettings, match=message):
        canopy_settings.save(
            staff,
            agents=agents,
            default_agent=default,
            runner_requirements=flags,
            listing=canopy_settings.site_agents(),
        )


def test_real_data_cannot_be_newly_ticked_while_canopy_says_nothing(env, staff, canopy_agents):
    canopy_agents("site-bot")
    env.set(None)
    with pytest.raises(canopy_settings.InvalidSettings, match="canopy does not list data-bot"):
        _allow_data_bot(staff)


def test_ticking_real_data_shows_the_agent(env, staff):
    saved = canopy_settings.save(
        staff,
        agents=[Tick("site-bot"), Tick("data-bot", offered=False, real_data=True)],
        default_agent="site-bot",
        runner_requirements=[],
        listing=canopy_settings.site_agents(),
    )
    assert saved.ticks("data-bot").offered


def test_every_change_is_kept_audited_and_can_be_restored(env, staff):
    first = _allow_data_bot(staff)
    canopy_settings.save(
        staff,
        agents=[Tick("site-bot")],
        default_agent="site-bot",
        runner_requirements=["sensitive_data"],
        listing=canopy_settings.site_agents(),
    )
    restored = canopy_settings.restore(staff, first.row_id, listing=canopy_settings.site_agents())
    assert restored.row_id != first.row_id
    assert restored.as_dict() == first.as_dict()
    assert CanopyAgentSettings.objects.count() == 3
    event = AuditEvent.objects.filter(resource_type="canopy_agent_settings").latest("id")
    assert {"slug": "data-bot", "offered": True, "real_data": True} in event.metadata["after"]["agents"]
    assert event.metadata["after"]["site_agents_version"] == "v1"
    assert event.metadata["before"]["runner_requirements"] == ["sensitive_data"]


def test_drift_in_canopy_withdraws_the_agent_at_once(env, staff):
    _allow_data_bot(staff)
    assert canopy_panel.availability("data-bot", REAL).ok
    # Its author withdraws the sensitive-data declaration in canopy: labs stops offering it
    # over real data, and says why.
    env.set(listing(agent("site-bot"), agent("data-bot", handles_sensitive=False)))
    held = canopy_panel.availability("data-bot", REAL)
    assert not held.ok and "not declared to handle sensitive data" in held.reasons[0]
    # canopy drops it from labs' site: not shown anywhere.
    env.set(listing(agent("site-bot")))
    held = canopy_panel.availability("data-bot", canopy_panel.NO_VISIT_DATA)
    assert not held.ok and "canopy does not allow data-bot" in held.reasons[0]


def test_canopy_saying_nothing_shows_no_agent(env, staff, canopy_agents):
    canopy_agents("site-bot")
    env.set(None)
    held = canopy_panel.availability("site-bot", canopy_panel.NO_VISIT_DATA)
    assert not held.ok and "canopy has not said" in held.reasons[0]


@override_settings(**LABS_SETTINGS)
def test_the_page_is_a_table_of_canopys_agents_with_labs_ticks(env, client, staff, canopy_agents):
    canopy_agents("site-bot")
    client.force_login(staff)
    page = client.get(reverse(URL))
    assert page.status_code == 200
    rows = {r["slug"]: r for r in page.context["rows"]}
    assert list(rows) == ["site-bot", "data-bot", "coach-bot"]
    assert rows["data-bot"]["handles_sensitive"] and rows["data-bot"]["eligible_for_real_data"]
    assert not rows["coach-bot"]["eligible_for_real_data"]
    assert rows["site-bot"]["default"] and rows["site-bot"]["offered"]
    assert b"Checks workflows against raw data" in page.content


@override_settings(**LABS_SETTINGS)
def test_staff_save_ticks_from_the_page(env, client, staff):
    client.force_login(staff)
    resp = client.post(
        reverse(URL),
        {
            "slug": ["site-bot", "data-bot", "coach-bot"],
            "offered:site-bot": "on",
            "real_data:data-bot": "on",
            "default_agent": "site-bot",
            "note": "testing",
        },
    )
    assert resp.status_code == 302
    saved = canopy_settings.current()
    assert saved.offered == ("site-bot", "data-bot") and saved.real_data_agents == ("data-bot",)
    assert saved.runner_requirements == ()
    assert CanopyAgentSettings.objects.get().note == "testing"


@override_settings(**LABS_SETTINGS)
def test_a_dimagi_user_without_the_staff_bit_can_save(env, client, dimagi_user):
    client.force_login(dimagi_user)
    page = client.get(reverse(URL))
    assert page.status_code == 200 and b"Only Dimagi staff" not in page.content
    client.post(reverse(URL), {"slug": ["site-bot"], "offered:site-bot": "on", "default_agent": "site-bot"})
    assert CanopyAgentSettings.objects.get().default_agent == "site-bot"


@override_settings(**LABS_SETTINGS)
def test_an_agent_canopy_no_longer_lists_is_shown_in_red(env, client, staff):
    _allow_data_bot(staff)
    env.set(listing(agent("site-bot")))
    client.force_login(staff)
    rows = {r["slug"]: r for r in client.get(reverse(URL)).context["rows"]}
    assert not rows["data-bot"]["listed"] and rows["data-bot"]["held_back"]


def test_legacy_workflow_choices_read_as_the_agents_they_name(env, staff):
    _allow_data_bot(staff)
    ticks, current = canopy_settings.current(), canopy_settings.site_agents()
    assert canopy_settings.resolve_choice("site", ticks, current) == "site-bot"
    assert canopy_settings.resolve_choice("", ticks, current) == "site-bot"
    assert canopy_settings.resolve_choice("data", ticks, current) == "data-bot"


def test_site_json_says_what_labs_asks_and_uses(env, staff):
    _allow_data_bot(staff)
    needs = canopy.site_needs()
    assert needs["real_data"] == {"requires": {"handles_sensitive": True, "runner_flag": "sensitive_data"}}
    assert needs["uses"] == {"agents": ["site-bot", "data-bot"], "real_data_agents": ["data-bot"], "version": "v1"}
    # Every page labs registers has a title for people reading it in canopy.
    assert set(canopy.PAGE_TITLES) == set(canopy.PAGE_SCOPES)


def test_the_grant_is_a_saved_setting_and_its_client_id_is_derived(env, staff):
    """Whether canopy may act as the visitor is ticked on the page, not an environment
    variable; canopy's client id comes from CANOPY_BASE_URL."""
    assert not _allow_data_bot(staff).acts_as_visitor
    assert canopy.host_settings()["CLIENT_ID"] == "" and not canopy.grant_on()

    on = _allow_data_bot(staff, acts_as_visitor=True)
    assert on.acts_as_visitor
    assert canopy.host_settings()["CLIENT_ID"] == "https://canopy.example/oauth/client.json"
    assert canopy.grant_on()
    # A save that does not say keeps it as it is; restoring an earlier row restores it too.
    assert canopy_settings.save(
        staff, agents=[Tick("site-bot")], default_agent="site-bot", runner_requirements=[]
    ).acts_as_visitor


@override_settings(**LABS_SETTINGS)
def test_the_page_says_plainly_when_nothing_is_set_up(env, client, staff):
    client.force_login(staff)
    page = client.get(reverse(URL))
    assert "Nothing is set up yet — choose agents below" in page.content.decode()
    assert b"deployment's defaults" not in page.content


@override_settings(**LABS_SETTINGS)
def test_staff_turn_the_grant_on_from_the_page(env, client, staff):
    client.force_login(staff)
    client.post(
        reverse(URL),
        {"slug": ["site-bot"], "offered:site-bot": "on", "default_agent": "site-bot", "acts_as_visitor": "1"},
    )
    assert canopy_settings.current().acts_as_visitor
    checks = {c["label"]: c for c in client.get(reverse(URL)).context["checks"]}
    assert checks["Canopy can act as the visitor here"]["ok"]


@override_settings(**LABS_SETTINGS)
def test_refresh_from_canopy_asks_now_and_says_what_came_back(env, client, staff, outsider, monkeypatch):
    client.force_login(staff)
    resp = client.post(reverse(URL), {"refresh": "1"}, follow=True)
    said = [str(m) for m in resp.context["messages"]]
    assert said == ["canopy answered: 3 agent(s) for connect-labs (site-bot, data-bot, coach-bot), version v1."]

    env.set(None)
    resp = client.post(reverse(URL), {"refresh": "1"}, follow=True)
    assert [str(m) for m in resp.context["messages"]] == [
        "canopy could not be asked (unreachable): canopy could not be reached"
    ]

    # Staff only, like a save.
    asked = []
    monkeypatch.setattr(canopy_settings, "refresh", lambda: asked.append(1))
    client.force_login(outsider)
    assert client.post(reverse(URL), {"refresh": "1"}).status_code == 403
    assert not asked


@override_settings(**LABS_SETTINGS)
def test_the_page_warns_when_canopys_list_changed_since_the_save(env, client, staff):
    _allow_data_bot(staff)
    client.force_login(staff)
    assert client.get(reverse(URL)).context["drift"] == ""
    env.set(listing(agent("site-bot"), agent("data-bot", handles_sensitive=True), version="v2"))
    drift = client.get(reverse(URL)).context["drift"]
    assert drift.startswith("canopy's list changed since these settings were saved")
    assert "v1" in drift and "v2" in drift


@override_settings(**LABS_SETTINGS)
def test_canopys_problem_sentences_link_what_they_name(env, client, staff, canopy_agents):
    canopy_agents("site-bot", offered=["coach-bot"])
    env.set(
        listing(
            agent("site-bot"),
            agent("coach-bot", ready=False, problems=["no GitHub token: connect one at https://canopy.example/x/y."]),
        )
    )
    client.force_login(staff)
    page = client.get(reverse(URL)).content.decode()
    assert '<a class="text-blue-600 underline" href="https://canopy.example/x/y" target="_blank"' in page
    # "canopy › Connected sites" links to canopy's page in labs' tenant.
    assert 'href="https://canopy.example/w/connect/settings/connected-apps"' in page
    # A reason in an attribute stays plain text.
    assert not any("<a" in title for title in re.findall(r'title="([^"]*)"', page))


def test_linkify_escapes_everything_else():
    from connect_labs.labs.admin.canopy_views import linkify

    out = linkify("<b>see https://c.example/a?b=1&c=2, or canopy › Runners</b>", {"canopy › Runners": "https://c/r"})
    assert out.startswith("&lt;b&gt;see <a ")
    assert 'href="https://c.example/a?b=1&amp;c=2"' in out
    assert ">canopy › Runners</a>&lt;/b&gt;" in out
    assert linkify("javascript:alert(1)") == "javascript:alert(1)"


@override_settings(**LABS_SETTINGS)
def test_unreachable_canopy_is_said_not_spoken_for(env, client, staff, canopy_agents):
    canopy_agents("site-bot")
    env.set(None)
    client.force_login(staff)
    page = client.get(reverse(URL))
    assert page.context["runner_flags"] == [{"flag": "sensitive_data", "meaning": ""}]
    assert b"canopy cannot be reached, so what each flag means is not shown" in page.content


@pytest.mark.parametrize(
    "client_id, key, expect",
    [
        ("https://canopy.example/oauth/client.json", "", True),  # the old env var, still set
        ("", "a key", True),  # the new task definition: key, canopy and public origin
        ("", "", False),  # nowhere the grant could have been on
    ],
)
def test_the_migration_keeps_the_grant_where_it_was_possible(settings, monkeypatch, client_id, key, expect):
    import importlib

    from django.apps import apps

    migration = importlib.import_module("connect_labs.labs.migrations.0035_canopy_agent_settings_acts_as_visitor")
    monkeypatch.setenv("CANOPY_CLIENT_ID", client_id)
    settings.CANOPY_SIGNING_KEY = key
    settings.CANOPY_BASE_URL = "https://canopy.example"
    settings.LABS_PUBLIC_URL = "https://labs.example"
    row = CanopyAgentSettings.objects.create(agents=[{"slug": "ace", "offered": True}], default_agent="ace")
    migration.forwards(apps, None)
    row.refresh_from_db()
    assert row.acts_as_visitor is expect


def test_restore_is_checked_against_canopys_list(env, staff):
    first = _allow_data_bot(staff)
    canopy_settings.save(
        staff,
        agents=[Tick("coach-bot")],
        default_agent="coach-bot",
        runner_requirements=[],
        listing=canopy_settings.site_agents(),
    )
    # canopy drops the old default agent from labs' site: that row cannot come back.
    env.set(listing(agent("data-bot", handles_sensitive=True), agent("coach-bot"), version="v2"))
    with pytest.raises(canopy_settings.InvalidSettings, match="its default agent, site-bot, is no longer listed"):
        canopy_settings.restore(staff, first.row_id, listing=canopy_settings.site_agents())
    # Nor one whose real-data agent canopy no longer clears.
    env.set(listing(agent("site-bot"), agent("data-bot"), agent("coach-bot")))
    with pytest.raises(canopy_settings.InvalidSettings, match="cannot be restored: data-bot cannot be allowed"):
        canopy_settings.restore(staff, first.row_id, listing=canopy_settings.site_agents())
    assert canopy_settings.current().default_agent == "coach-bot"


def test_the_workspace_is_learnt_from_canopy_and_names_the_tenant(env, staff, canopy_agents, client):
    """Blank until canopy's answer names it (production's existing row): meanwhile the
    default agent names the tenant. Then CANOPY_HOST["WORKSPACE"] carries it."""
    canopy_agents("site-bot")
    assert canopy.host_settings()["WORKSPACE"] == "" and canopy.host_settings()["AGENT_SLUG"] == "site-bot"
    client.force_login(staff)
    with override_settings(**LABS_SETTINGS):
        page = client.get(reverse(URL))
    assert canopy_settings.current().workspace == "connect"  # the fake listing's tenant
    assert canopy.host_settings()["WORKSPACE"] == "connect"
    assert b"<code>connect</code>" in page.content or b"connect" in page.content
    # Every save records the workspace canopy's answer names.
    env.set(listing(agent("site-bot"), tenant="other-ws"))
    assert _save_site_bot(staff).workspace == "other-ws"


def _save_site_bot(user):
    return canopy_settings.save(
        user,
        agents=[Tick("site-bot")],
        default_agent="site-bot",
        runner_requirements=[],
        listing=canopy_settings.site_agents(),
    )


@override_settings(**LABS_SETTINGS)
def test_a_workspace_canopy_refuses_can_be_forgotten(env, client, staff):
    _save_site_bot(staff)
    assert canopy_settings.current().workspace == "connect"
    env.fail("workspace_mismatch", "site-bot is not in workspace connect", status=409)
    client.force_login(staff)
    page = client.get(reverse(URL))
    assert page.context["status"]["text"].startswith(
        "canopy has not said which agents labs may show (workspace_mismatch: site-bot is not in workspace connect)"
    )
    assert page.context["offer_forget_workspace"]
    client.post(reverse(URL), {"forget_workspace": "1"})
    assert canopy_settings.current().workspace == "" and canopy_settings.current().default_agent == "site-bot"


@override_settings(**LABS_SETTINGS)
def test_refresh_reports_canopys_own_reason(env, client, staff):
    env.fail("unknown_issuer", "canopy does not know connect-labs")
    client.force_login(staff)
    resp = client.post(reverse(URL), {"refresh": "1"}, follow=True)
    assert [str(m) for m in resp.context["messages"]] == [
        "canopy could not be asked (unknown_issuer): canopy does not know connect-labs"
    ]


@override_settings(**LABS_SETTINGS)
def test_canopys_own_links_are_used_where_it_gives_them(env, client, staff, canopy_agents):
    canopy_agents("site-bot", offered=["coach-bot"])
    env.set(
        listing(
            agent("site-bot"),
            agent("coach-bot", ready=False, problem_links=[("No ready runner.", "https://canopy.example/fix/1")]),
            settings_url="https://canopy.example/w/connect/settings/connected-apps#site-7",
        )
    )
    client.force_login(staff)
    page = client.get(reverse(URL))
    html = page.content.decode()
    assert 'href="https://canopy.example/fix/1" target="_blank" rel="noopener">No ready runner.</a>' in html
    assert page.context["connected_sites_url"] == "https://canopy.example/w/connect/settings/connected-apps#site-7"
