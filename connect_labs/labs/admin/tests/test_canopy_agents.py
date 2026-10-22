"""Labs Admin › Canopy agents: the settings that decide which canopy agent may read real
data are changed in the UI, by Dimagi staff only, with history, and labs reads them everywhere.

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

pytestmark = pytest.mark.django_db

URL = "labs_admin:canopy_agents"


@pytest.fixture
def env(settings):
    settings.CANOPY_AGENT_SLUG = "site-bot"
    settings.CANOPY_DATA_AGENT_SLUG = ""
    settings.CANOPY_PHI_AGENTS = []
    settings.CANOPY_RUNNER_REQUIREMENTS = []


@pytest.fixture
def staff():
    return get_user_model().objects.create_user(username="s", email="s@dimagi.com", password="pw", is_staff=True)


@pytest.fixture
def dimagi_user():
    return get_user_model().objects.create_user(username="d", email="d@dimagi.com", password="pw")


@pytest.fixture
def outsider():
    return get_user_model().objects.create_user(username="o", email="o@partner.example", password="pw")


def _allow_data_bot(user):
    return canopy_settings.save(
        user, site_agent="site-bot", data_agent="data-bot", phi_agents=["data-bot"], runner_requirements=[]
    )


def test_with_nothing_saved_the_environment_is_in_force(env):
    assert canopy_settings.current() == canopy_settings.from_environment()
    assert canopy_settings.current().row_id is None


def test_a_saved_change_is_what_labs_reads_everywhere(env, staff, monkeypatch):
    monkeypatch.setattr(canopy_panel, "CANOPY_EXECUTION_STATEMENTS", True)
    assert not canopy_panel.data_agent_clearance().granted
    _allow_data_bot(staff)
    page = canopy_panel.data_agent()
    assert (page.agent, page.runner_requirements) == ("data-bot", ("zdr",))
    assert canopy_panel.data_agent_clearance().granted
    # The site agent alone still needs the site-wide ZDR requirement.
    assert not canopy_panel.real_data_clearance("site-bot").granted
    host = canopy.host_settings()
    assert host["AGENT_SLUG"] == "site-bot" and host["RUNNER_REQUIREMENTS"] == []


def test_only_dimagi_staff_can_change_it(env, dimagi_user, outsider):
    with pytest.raises(PermissionError):
        _allow_data_bot(outsider)
    assert not CanopyAgentSettings.objects.exists()
    # A Dimagi sign-in is enough: an OAuth-created labs user never gets Django's
    # is_staff, and no labs page grants it, so is_staff alone left nobody able to.
    assert _allow_data_bot(dimagi_user).data_agent == "data-bot"


@pytest.mark.parametrize(
    "values, message",
    [
        ({"site_agent": ""}, "site agent"),
        ({"data_agent": "Data Bot"}, "not a canopy agent slug"),
        ({"phi_agents": ["ok", "no way"]}, "not a canopy agent slug"),
        ({"runner_requirements": ["gpu"]}, "Unknown runner flag"),
    ],
)
def test_bad_values_are_refused(env, staff, values, message):
    base = {"site_agent": "site-bot", "data_agent": "", "phi_agents": [], "runner_requirements": []}
    with pytest.raises(canopy_settings.InvalidSettings, match=message):
        canopy_settings.save(staff, **{**base, **values})


def test_every_change_is_kept_audited_and_can_be_restored(env, staff):
    first = _allow_data_bot(staff)
    canopy_settings.save(staff, site_agent="site-bot", data_agent="", phi_agents=[], runner_requirements=["zdr"])
    restored = canopy_settings.restore(staff, first.row_id)
    assert restored.row_id != first.row_id
    assert restored.as_dict() == first.as_dict()
    assert CanopyAgentSettings.objects.count() == 3
    event = AuditEvent.objects.filter(resource_type="canopy_agent_settings").latest("id")
    assert event.metadata["after"]["phi_agents"] == ["data-bot"]
    assert event.metadata["before"]["runner_requirements"] == ["zdr"]


@override_settings(**LABS_SETTINGS)
def test_the_page_shows_the_settings_and_lets_staff_save(env, client, staff):
    client.force_login(staff)
    assert client.get(reverse(URL)).status_code == 200
    resp = client.post(
        reverse(URL),
        {"site_agent": "site-bot", "data_agent": "data-bot", "phi_agents": "data-bot, other-bot", "note": "testing"},
    )
    assert resp.status_code == 302
    saved = canopy_settings.current()
    assert saved.phi_agents == ("data-bot", "other-bot") and saved.runner_requirements == ()
    assert CanopyAgentSettings.objects.get().note == "testing"


@override_settings(**LABS_SETTINGS)
def test_a_dimagi_user_without_the_staff_bit_can_save(env, client, dimagi_user):
    client.force_login(dimagi_user)
    page = client.get(reverse(URL))
    assert page.status_code == 200 and b"Only Dimagi staff" not in page.content
    client.post(reverse(URL), {"site_agent": "site-bot", "phi_agents": "data-bot"})
    assert CanopyAgentSettings.objects.get().phi_agents == ["data-bot"]


def test_workflows_choose_from_the_site_agent_the_data_agent_and_the_others_listed(env, staff):
    canopy_settings.save(
        staff,
        site_agent="site-bot",
        data_agent="data-bot",
        phi_agents=["data-bot", "deep-bot"],
        runner_requirements=[],
        offered_agents=["coach-bot", "deep-bot", "site-bot"],
    )
    offered = {o.value: o for o in canopy_settings.choices()}
    assert list(offered) == ["site", "data", "coach-bot", "deep-bot"]  # the site's first, no repeats
    assert not offered["site"].read_only and not offered["coach-bot"].read_only
    # The data agent, and any agent allowed over real data, is offered read-only.
    assert offered["data"].read_only and offered["data"].real_data
    assert offered["deep-bot"].read_only and offered["deep-bot"].real_data
    # A slug that is a role's agent resolves to that role; one not offered, to nothing.
    assert canopy_settings.resolve("data-bot").value == "data"
    assert canopy_settings.resolve("other-bot") is None


def test_the_page_saves_the_agents_workflows_may_choose(env, client, staff):
    from django.test import override_settings as _override

    with _override(**LABS_SETTINGS):
        client.force_login(staff)
        client.post(reverse(URL), {"site_agent": "site-bot", "offered_agents": "coach-bot, deep-bot"})
    assert CanopyAgentSettings.objects.get().offered_agents == ["coach-bot", "deep-bot"]
