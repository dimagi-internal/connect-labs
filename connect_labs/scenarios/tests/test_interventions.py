import pytest
from django.urls import reverse

from connect_labs.impact import engine
from connect_labs.scenarios import interventions


def test_a_preset_that_follows_a_model_is_priced_by_that_model():
    followed = [i for i in interventions.all_interventions() if i.model]
    assert {i.slug for i in followed} == {"kmc", "ors", "ors_annual"}
    for i in followed:
        assert i.unit_cost_usd == engine.defaults(i.model)["cost"], i.slug


def test_the_ors_presets_no_longer_disagree_with_the_chc_model():
    assert interventions.get("ors").unit_cost_usd == interventions.get("ors_annual").unit_cost_usd == 1.8


@pytest.mark.django_db
class TestAddresses:
    @pytest.fixture
    def client_in(self, client, django_user_model):
        client.force_login(django_user_model.objects.create_user(username="sizing", password="x"))
        return client

    def test_sizing_is_served_under_scenarios(self, client_in):
        assert reverse("scenarios:scenario") == "/labs/scenarios/api/scenario/"
        r = client_in.get(reverse("scenarios:interventions"))
        assert r.status_code == 200 and {i["slug"] for i in r.json()["interventions"]} >= {"kmc", "ors"}

    def test_the_old_addresses_still_work(self, client_in):
        r = client_in.get("/labs/targeting/api/scenario/?threshold=50&basis=birth")
        assert r.status_code == 307 and r["Location"].startswith("/labs/scenarios/api/scenario/?")
        r = client_in.get("/labs/targeting/api/interventions/")
        assert r.status_code == 307 and r["Location"] == "/labs/scenarios/api/interventions/"
