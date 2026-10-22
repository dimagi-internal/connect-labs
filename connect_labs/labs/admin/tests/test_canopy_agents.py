"""Labs Admin › Canopy agents: the settings that decide which canopy agent may read real
data are changed in the UI, by staff only, with history, and labs reads them everywhere.

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


def test_only_staff_can_change_it(env, dimagi_user):
    with pytest.raises(PermissionError):
        _allow_data_bot(dimagi_user)
    assert not CanopyAgentSettings.objects.exists()


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
def test_a_dimagi_user_who_is_not_staff_sees_it_but_cannot_save(env, client, dimagi_user):
    client.force_login(dimagi_user)
    page = client.get(reverse(URL))
    assert page.status_code == 200 and b"Only Django staff" in page.content
    client.post(reverse(URL), {"site_agent": "site-bot", "phi_agents": "data-bot"})
    assert not CanopyAgentSettings.objects.exists()
