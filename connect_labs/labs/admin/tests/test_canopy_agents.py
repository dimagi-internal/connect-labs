"""Labs Admin › Canopy agents: which canopy agents labs shows, and which may read real
data, chosen in the UI from the agents canopy lists for labs (the site agents API) --
by Dimagi staff only, with history -- and labs reads them everywhere.

Every name here is invented (this repository is public).
"""

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
    settings.CANOPY_AGENT_SLUG = "site-bot"
    settings.CANOPY_DATA_AGENT_SLUG = ""
    settings.CANOPY_PHI_AGENTS = []
    settings.CANOPY_OFFERED_AGENTS = []
    settings.CANOPY_RUNNER_REQUIREMENTS = []
    monkeypatch.setattr(canopy_panel, "CANOPY_EXECUTION_STATEMENTS", True)
    # canopy lists three agents for labs: the site's, a PHI-safe data agent ready on
    # ZDR, and a coach that is neither.
    fake_canopy.set(
        listing(
            agent("site-bot", description="Helps with labs"),
            agent("data-bot", phi_safe=True, description="Checks workflows against raw data"),
            agent("coach-bot", zdr=False),
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


def test_with_nothing_saved_the_environment_is_in_force(env):
    assert canopy_settings.current() == canopy_settings.from_environment()
    assert canopy_settings.current().row_id is None


def test_the_cold_start_defaults_read_the_environment_as_ticks(settings):
    settings.CANOPY_AGENT_SLUG = "site-bot"
    settings.CANOPY_DATA_AGENT_SLUG = "data-bot"
    settings.CANOPY_PHI_AGENTS = ["deep-bot"]
    settings.CANOPY_OFFERED_AGENTS = ["coach-bot"]
    env = canopy_settings.from_environment()
    assert env.offered == ("site-bot", "data-bot", "coach-bot", "deep-bot")
    assert env.real_data_agents == ("deep-bot",) and env.default_agent == "site-bot"


def test_a_saved_change_is_what_labs_reads_everywhere(env, staff):
    assert not canopy_panel.availability("data-bot", REAL).ok
    saved = _allow_data_bot(staff)
    assert saved.site_agents_version == "v1"
    here = canopy_panel.availability("data-bot", REAL)
    assert here.ok and here.read_only
    assert (here.page_agent.agent, here.page_agent.runner_requirements) == ("data-bot", ("zdr",))
    # The Data page offers it, read-only on ZDR.
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
        # Real data needs canopy's PHI-safe declaration and a ready ZDR runner.
        ([Tick("site-bot", real_data=True)], "site-bot", [], "not declared PHI-safe"),
        ([Tick("site-bot"), Tick("coach-bot", real_data=True)], "site-bot", [], "not declared PHI-safe"),
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


def test_real_data_cannot_be_newly_ticked_while_canopy_says_nothing(env, staff):
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
        runner_requirements=["zdr"],
        listing=canopy_settings.site_agents(),
    )
    restored = canopy_settings.restore(staff, first.row_id)
    assert restored.row_id != first.row_id
    assert restored.as_dict() == first.as_dict()
    assert CanopyAgentSettings.objects.count() == 3
    event = AuditEvent.objects.filter(resource_type="canopy_agent_settings").latest("id")
    assert {"slug": "data-bot", "offered": True, "real_data": True} in event.metadata["after"]["agents"]
    assert event.metadata["after"]["site_agents_version"] == "v1"
    assert event.metadata["before"]["runner_requirements"] == ["zdr"]


def test_drift_in_canopy_withdraws_the_agent_at_once(env, staff):
    _allow_data_bot(staff)
    assert canopy_panel.availability("data-bot", REAL).ok
    # Its author withdraws the PHI-safe declaration in canopy: labs stops offering it
    # over real data, and says why.
    env.set(listing(agent("site-bot"), agent("data-bot", phi_safe=False)))
    held = canopy_panel.availability("data-bot", REAL)
    assert not held.ok and "not declared PHI-safe" in held.reasons[0]
    # canopy drops it from labs' site: not shown anywhere.
    env.set(listing(agent("site-bot")))
    held = canopy_panel.availability("data-bot", canopy_panel.NO_VISIT_DATA)
    assert not held.ok and "canopy does not allow data-bot" in held.reasons[0]


def test_canopy_saying_nothing_shows_no_agent(env, staff):
    env.set(None)
    held = canopy_panel.availability("site-bot", canopy_panel.NO_VISIT_DATA)
    assert not held.ok and "canopy has not said" in held.reasons[0]


@override_settings(**LABS_SETTINGS)
def test_the_page_is_a_table_of_canopys_agents_with_labs_ticks(env, client, staff):
    client.force_login(staff)
    page = client.get(reverse(URL))
    assert page.status_code == 200
    rows = {r["slug"]: r for r in page.context["rows"]}
    assert list(rows) == ["site-bot", "data-bot", "coach-bot"]
    assert rows["data-bot"]["phi_safe"] and rows["data-bot"]["eligible_for_real_data"]
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
    assert needs["real_data"] == {"requires": {"phi_safe": True, "runner_flag": "zdr"}}
    assert needs["uses"] == {"agents": ["site-bot", "data-bot"], "real_data_agents": ["data-bot"], "version": "v1"}
    # Every page labs registers has a title for people reading it in canopy.
    assert set(canopy.PAGE_TITLES) == set(canopy.PAGE_SCOPES)
