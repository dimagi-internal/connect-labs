"""The screens a tender's partner split is entered on, sized, and explained.

Sophie Feintuch's RUTF feedback: "Split tenders by partner", "Help working out how much to buy", "A needed-by
date on the tender". THIS REPOSITORY IS PUBLIC. Every organisation, product and figure here is invented.
"""

from datetime import timedelta

import pytest
from django.urls import reverse

from connect_labs.supply_chain.procurement import split_screens  # noqa: F401  (patched by the client fixture)
from connect_labs.supply_chain.tests import chlorine_world
from connect_labs.supply_chain.tests.chlorine_world import TODAY, op

pytestmark = pytest.mark.django_db

da = chlorine_world.da
account = chlorine_world.account
client_in_programme = chlorine_world.client_in_programme


@pytest.fixture
def world(da):
    op(
        da,
        "commodity_upsert",
        data={
            "slug": "rutf",
            "name": "RUTF",
            "base_unit": "sachet",
            "pack_unit": "carton",
            "base_per_pack": 150,
            "course_definition": {"base_units_per_course": 120},
        },
    )
    orgs = [op(da, "org_upsert", data={"slug": f"p{i}", "name": f"Partner {i}"}) for i in (1, 2)]
    places = [
        op(
            da,
            "supply_point_upsert",
            data={
                "slug": f"wh{i}",
                "name": f"Partner {i} warehouse",
                "kind": "central_store",
                "managed_by_org_id": org["id"],
                "source": "we_recorded",
            },
        )
        for i, org in enumerate(orgs, start=1)
    ]
    tender = op(
        da,
        "tender_create",
        data={
            "label": "Tender 1",
            "delivery_point": {"name": "Abuja depot", "city": "Abuja"},
            "lines": [{"commodity_slug": "rutf", "quantity": "10", "quantity_unit": "carton"}],
        },
    )
    return {"orgs": orgs, "places": places, "tender": tender}


def _post(world, **extra):
    a, b = world["orgs"]
    data = {
        "splits-TOTAL_FORMS": "2",
        "splits-INITIAL_FORMS": "0",
        "splits-MIN_NUM_FORMS": "0",
        "splits-MAX_NUM_FORMS": "1000",
        "splits-0-org": str(a["id"]),
        "splits-0-quantity": "400",
        "splits-0-place": f"sp:{world['places'][0]['id']}",
        "splits-0-receiver_name": "Amina",
        "splits-0-buying_model": "partner_buys",
        "splits-1-org": str(b["id"]),
        "splits-1-quantity": "600",
        "splits-1-place": f"sp:{world['places'][1]['id']}",
        "splits-1-buying_model": "programme_buys",
    }
    data.update(extra)
    return data


class TestTheSplitPage:
    def test_it_saves_a_split_and_the_total_is_the_sum(self, client_in_programme, da, world):
        url = reverse("supply_chain:procurement_tender_splits", args=[world["tender"]["id"]])
        response = client_in_programme.post(url, _post(world))
        assert response.status_code == 302, response.content.decode()[:2000]
        tender = op(da, "tender_get", tender_id=world["tender"]["id"])
        assert [(s["org_name"], s["quantity"], s["receiver_name"]) for s in tender["partner_splits"]] == [
            ("Partner 1", "400", "Amina"),
            ("Partner 2", "600", ""),
        ]
        assert tender["lines"][0]["quantity"] == "1000"
        assert tender["partner_splits"][0]["supply_point_id"] == world["places"][0]["id"]
        assert tender["partner_splits"][1]["buying_model"] == "programme_buys"

    def test_the_page_offers_each_partner_the_places_it_runs(self, client_in_programme, world):
        body = client_in_programme.get(
            reverse("supply_chain:procurement_tender_splits", args=[world["tender"]["id"]])
        ).content.decode()
        assert "Partner 1 warehouse" in body and "Partner 2 warehouse" in body
        assert f'value="sp:{world["places"][0]["id"]}" data-org="{world["orgs"][0]["id"]}"' in body
        assert "Abuja depot" in body

    def test_it_refuses_a_place_another_partner_runs(self, client_in_programme, da, world):
        bad = _post(world, **{"splits-0-place": f"sp:{world['places'][1]['id']}"})
        response = client_in_programme.post(
            reverse("supply_chain:procurement_tender_splits", args=[world["tender"]["id"]]), bad
        )
        assert response.status_code == 200
        assert "run by another partner" in response.content.decode()
        assert op(da, "tender_get", tender_id=world["tender"]["id"])["partner_splits"] == []

    def test_the_operations_refusal_is_shown_on_the_page(self, client_in_programme, world):
        bad = _post(
            world,
            **{"splits-1-org": str(world["orgs"][0]["id"]), "splits-1-place": f"sp:{world['places'][0]['id']}"},
        )
        response = client_in_programme.post(
            reverse("supply_chain:procurement_tender_splits", args=[world["tender"]["id"]]), bad
        )
        assert response.status_code == 200
        assert "split twice" in response.content.decode()

    def test_it_reopens_with_the_saved_rows_and_clearing_them_removes_the_split(self, client_in_programme, da, world):
        url = reverse("supply_chain:procurement_tender_splits", args=[world["tender"]["id"]])
        client_in_programme.post(url, _post(world))
        body = client_in_programme.get(url).content.decode()
        assert 'value="400"' in body and "Amina" in body
        cleared = {
            "splits-TOTAL_FORMS": "2",
            "splits-INITIAL_FORMS": "2",
            "splits-MIN_NUM_FORMS": "0",
            "splits-MAX_NUM_FORMS": "1000",
            "splits-0-DELETE": "on",
            "splits-1-DELETE": "on",
            **{k: v for k, v in _post(world).items() if k.startswith("splits-0-") or k.startswith("splits-1-")},
        }
        assert client_in_programme.post(url, cleared).status_code == 302
        assert op(da, "tender_get", tender_id=world["tender"]["id"])["partner_splits"] == []

    def test_a_tender_that_has_left_draft_is_not_split(self, client_in_programme, world):
        from connect_labs.supply_chain.models import Tender

        Tender.objects.filter(pk=world["tender"]["id"]).update(status="open")
        response = client_in_programme.get(
            reverse("supply_chain:procurement_tender_splits", args=[world["tender"]["id"]])
        )
        assert response.status_code == 302

    def test_the_tender_links_to_the_page_while_a_draft(self, client_in_programme, world):
        body = client_in_programme.get(
            reverse("supply_chain:procurement_tender_detail", args=[world["tender"]["id"]])
        ).content.decode()
        assert "Split this tender between partners" in body
        assert reverse("supply_chain:procurement_tender_sizing", args=[world["tender"]["id"]]) in body


class TestTheSizingPage:
    def _url(self, world, **query):
        base = reverse("supply_chain:procurement_tender_sizing", args=[world["tender"]["id"]])
        return f"{base}?" + "&".join(f"{k}={v}" for k, v in query.items()) if query else base

    def test_it_shows_the_working_in_plain_words(self, client_in_programme, world):
        pid = world["orgs"][0]["id"]
        body = client_in_programme.get(self._url(world, **{f"cases_{pid}": 1000})).content.decode()
        assert "1,000 children x 150 per child = 150,000" in body
        # 150000 x 1.10 = 165000 sachets = 1,100 cartons of 150: one carton a child by default
        assert "Partner 1: 1,100 carton" in body
        assert "one carton a child" in body

    def test_the_button_drops_the_figure_into_that_partners_split(self, client_in_programme, da, world):
        pid = world["orgs"][0]["id"]
        url = reverse("supply_chain:procurement_tender_sizing_apply", args=[world["tender"]["id"]])
        response = client_in_programme.post(
            url, {"org_id": pid, "commodity_slug": "rutf", "quantity": "880", "next": self._url(world)}
        )
        assert response.status_code == 302
        tender = op(da, "tender_get", tender_id=world["tender"]["id"])
        assert [(s["org_id"], s["quantity"]) for s in tender["partner_splits"]] == [(pid, "880")]
        assert tender["lines"][0]["quantity"] == "880"

    def test_it_changes_only_that_partners_quantity(self, client_in_programme, da, world):
        client_in_programme.post(
            reverse("supply_chain:procurement_tender_splits", args=[world["tender"]["id"]]), _post(world)
        )
        client_in_programme.post(
            reverse("supply_chain:procurement_tender_sizing_apply", args=[world["tender"]["id"]]),
            {"org_id": world["orgs"][0]["id"], "commodity_slug": "rutf", "quantity": "250"},
        )
        splits = op(da, "tender_get", tender_id=world["tender"]["id"])["partner_splits"]
        assert [s["quantity"] for s in splits] == ["250", "600"]
        assert splits[0]["receiver_name"] == "Amina"


class TestTheNeededByWorking:
    def test_the_delivery_time_behind_the_start_date_is_shown(self, client_in_programme, da, world):
        op(
            da,
            "tender_update",
            tender_id=world["tender"]["id"],
            data={"needed_by": (TODAY + timedelta(days=120)).isoformat(), "lead_time_days_expected": 60},
        )
        body = client_in_programme.get(
            reverse("supply_chain:procurement_tender_detail", args=[world["tender"]["id"]])
        ).content.decode()
        assert 'data-testid="start-by-working"' in body
        assert "60 days for suppliers to deliver (our own estimate" in body
        assert "14 days for them to reply" in body


class TestTheSizingPagePartners:
    def _body(self, client, world):
        return client.get(
            reverse("supply_chain:procurement_tender_sizing", args=[world["tender"]["id"]])
        ).content.decode()

    def test_a_partner_stated_on_the_partners_page_is_listed_without_a_place(self, client_in_programme, da, world):
        from connect_labs.supply_chain.models import PartnerPlan
        from connect_labs.supply_chain.partners.models import DISTRIBUTION_MODELS

        other = op(da, "org_upsert", data={"slug": "p3", "name": "Partner 3"})
        PartnerPlan.objects.create(
            program_id=chlorine_world.PROGRAM,
            org_id=other["id"],
            distribution_model=DISTRIBUTION_MODELS[0],
            starts_on=TODAY,
        )
        assert "Partner 3" in self._body(client_in_programme, world)

    def test_the_empty_state_points_at_the_partners_page_not_the_network(self, client_in_programme, da, world):
        from connect_labs.supply_chain.models import SupplyPoint

        SupplyPoint.objects.update(managed_by_org_id=None)
        body = self._body(client_in_programme, world)
        assert "Network page" not in body
        if "No partners are listed" in body:
            assert reverse("supply_chain:partners") in body

    def test_the_course_box_is_wide_enough_for_its_hint(self, client_in_programme, da, world):
        assert 'placeholder="one carton" class="ml-1 w-44' in self._body(client_in_programme, world)
