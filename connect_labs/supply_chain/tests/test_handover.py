"""Handouts above the field worker: planned, confirmed from a message, confirmed from both sides.

THIS REPOSITORY IS PUBLIC. Every place, person and figure here is invented.
"""

from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from connect_labs.supply_chain.distribution import messages as reader
from connect_labs.supply_chain.models import (
    Commodity,
    Distribution,
    DistributionLine,
    Document,
    Movement,
    SupplyPoint,
)
from connect_labs.supply_chain.stock.services import ledger

pytestmark = pytest.mark.django_db

PROGRAM = 10507
SCOPE = f"prog:{PROGRAM}"
OPPORTUNITY = 4243


@pytest.fixture
def user(client, django_user_model):
    account = django_user_model.objects.create_user(username="ada", password="x", email="a@dimagi.com")
    client.force_login(account)
    return account


@pytest.fixture
def scoped(client, user, monkeypatch):
    from connect_labs.supply_chain import form_views, views  # noqa: F401
    from connect_labs.supply_chain.api_views import _access as real_access
    from connect_labs.supply_chain.distribution import views as distribution_views  # noqa: F401
    from connect_labs.supply_chain.procurement import views as procurement_views  # noqa: F401

    def _scoped(request):
        access = real_access(request)
        access.program_id = PROGRAM
        return access

    for module in ("form_views", "views", "procurement.views", "distribution.views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}.has_program_context", lambda request: True)
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}._access", _scoped)
    return client


@pytest.fixture
def rutf():
    return Commodity.objects.create(scope_key=SCOPE, slug="rutf", name="RUTF", base_unit="sachet", pack_unit="carton")


def point(slug, name, kind="regional_store", **extra):
    return SupplyPoint.objects.create(
        program_id=PROGRAM, slug=slug, name=name, kind=kind, source="we_recorded", **extra
    )


@pytest.fixture
def warehouse():
    return point("warehouse", "Central warehouse", "central_store")


@pytest.fixture
def places():
    return [point(slug, name, "facility") for slug, name in (("wuse", "Wuse PHC"), ("garki", "Garki PHC"))]


def planned_run(scoped, warehouse, rutf, places, quantities=(12, 10)):
    """A planned run made through the operation, as a person or an AI would."""
    response = scoped.get(reverse("supply_chain:distribution"))
    assert response.status_code == 200
    from connect_labs.supply_chain.form_views import SupplyWriteMixin

    class Shim(SupplyWriteMixin):
        request = response.wsgi_request

    return Shim().op(
        "distribution_plan",
        data={
            "supply_point_id": warehouse.pk,
            "opportunity_id": OPPORTUNITY,
            "commodity_slug": "rutf",
            "distributed_on": "2026-11-09",
            "reference": "Monday handout",
            "source": "we_recorded",
            "lines": [
                {"to_supply_point_id": p.pk, "quantity": str(q), "quantity_unit": "carton"}
                for p, q in zip(places, quantities)
            ],
        },
    )


class TestReadingAMessage:
    def test_common_whatsapp_shapes_are_read(self):
        text = (
            "[04/11/2026, 09:41:02] Ada: Weekly handout:\n"
            "1. Wuse PHC - 12 cartons\n"
            "- Garki Primary Health Centre: 8 ctn\n"
            "10 cartons to Maitama\n"
            "Bwari = 3\n"
            "see you Monday\n"
        )
        read = reader.read_message(text)
        names = [(e["name"], e["quantity"], e["unit"]) for e in read["entries"]]
        assert names == [
            ("Wuse PHC", Decimal("12"), "carton"),
            ("Garki Primary Health Centre", Decimal("8"), "carton"),
            ("Maitama", Decimal("10"), "carton"),
            ("Bwari", Decimal("3"), ""),
        ]
        assert "see you Monday" in read["unread"], "a line with no amount is handed back, not guessed at"

    def test_a_place_is_matched_and_a_difference_is_flagged(self):
        planned = [
            {"line_id": 1, "name": "Wuse PHC", "quantity": 12, "unit": "carton"},
            {"line_id": 2, "name": "Garki PHC", "quantity": 10, "unit": "carton"},
            {"line_id": 3, "name": "Gwarimpa PHC", "quantity": 4, "unit": "carton"},
        ]
        read = reader.read_message("Wuse PHC - 12 cartons\nGarki Primary Health Centre: 8 ctn\nKubwa clinic 5 cartons")
        matched = reader.match_to_plan(read["entries"], planned)
        by_id = {row["line_id"]: row for row in matched["lines"]}
        assert by_id[1]["status"] == "matches"
        assert by_id[2]["status"] == "differs" and by_id[2]["difference"] == Decimal("-2")
        assert by_id[3]["status"] == "not_mentioned"
        assert [e["name"] for e in matched["extras"]] == ["Kubwa clinic"], "an unknown place is left for a person"

    def test_a_different_unit_is_not_silently_taken_as_the_planned_one(self):
        planned = [{"line_id": 1, "name": "Wuse PHC", "quantity": 12, "unit": "carton"}]
        matched = reader.match_to_plan(reader.read_message("Wuse PHC - 300 sachets")["entries"], planned)
        assert matched["lines"][0]["status"] == "unit_differs"

    PACK = {"base": "sachet", "pack": "carton", "per_pack": 150}

    def test_sachets_are_converted_to_cartons_when_the_carton_size_is_known(self):
        planned = [{"line_id": 1, "name": "Wuse PHC", "quantity": 12, "unit": "carton", "pack": self.PACK}]
        matched = reader.match_to_plan(reader.read_message("Wuse PHC - 1800 sachets")["entries"], planned)
        row = matched["lines"][0]
        assert row["status"] == "matches" and row["given"] == Decimal("12")
        assert row["converted_from"]["unit"] == "sachet" and row["converted_from"]["per_pack"] == 150

    def test_a_converted_amount_that_differs_is_shown_as_a_difference_in_the_plans_unit(self):
        planned = [{"line_id": 1, "name": "Wuse PHC", "quantity": 12, "unit": "carton", "pack": self.PACK}]
        row = reader.match_to_plan(reader.read_message("Wuse PHC - 1650 sachets")["entries"], planned)["lines"][0]
        assert row["status"] == "differs" and row["difference"] == Decimal("-1")

    def test_cartons_against_a_plan_in_sachets_are_converted_too(self):
        planned = [{"line_id": 1, "name": "Wuse PHC", "quantity": 300, "unit": "sachet", "pack": self.PACK}]
        row = reader.match_to_plan(reader.read_message("Wuse PHC - 2 cartons")["entries"], planned)["lines"][0]
        assert row["status"] == "matches" and row["given"] == Decimal("300")

    def test_a_part_carton_or_an_unknown_size_is_still_flagged_not_guessed(self):
        planned = [{"line_id": 1, "name": "Wuse PHC", "quantity": 12, "unit": "carton", "pack": self.PACK}]
        row = reader.match_to_plan(reader.read_message("Wuse PHC - 1000 sachets")["entries"], planned)["lines"][0]
        assert row["status"] == "unit_differs"
        no_size = [{"line_id": 1, "name": "Wuse PHC", "quantity": 12, "unit": "carton", "pack": None}]
        assert (
            reader.match_to_plan(reader.read_message("Wuse PHC - 1800 sachets")["entries"], no_size)["lines"][0][
                "status"
            ]
            == "unit_differs"
        )

    def test_two_places_that_could_be_either_are_not_guessed(self):
        planned = [
            {"line_id": 1, "name": "Garki Ward A", "quantity": 5, "unit": "carton"},
            {"line_id": 2, "name": "Garki Ward B", "quantity": 5, "unit": "carton"},
        ]
        matched = reader.match_to_plan(reader.read_message("Garki 5 cartons")["entries"], planned)
        assert [row["status"] for row in matched["lines"]] == ["not_mentioned", "not_mentioned"]
        assert len(matched["extras"]) == 1


class TestPlanThenConfirm:
    def test_a_plan_moves_no_stock_but_is_committed(self, scoped, warehouse, rutf, places):
        run = planned_run(scoped, warehouse, rutf, places)
        assert run["status"] == "planned"
        assert not Movement.objects.filter(distribution_id=run["id"]).exists()
        promised = ledger.committed(PROGRAM, warehouse)
        assert promised.amount == Decimal("22")

    def test_confirming_as_planned_posts_the_movements(self, scoped, warehouse, rutf, places):
        run = planned_run(scoped, warehouse, rutf, places)
        response = scoped.post(reverse("supply_chain:distribution_confirm_as_planned", args=[run["id"]]))
        assert response.status_code == 302
        assert Distribution.objects.get(pk=run["id"]).status == "confirmed"
        assert Movement.objects.filter(distribution_id=run["id"]).count() == 2
        assert ledger.committed(PROGRAM, warehouse).amount == 0, "once given it is no longer only promised"

    def test_a_message_in_sachets_is_converted_with_the_products_carton_size(self, scoped, warehouse, rutf, places):
        rutf.base_per_pack = 150
        rutf.save()
        run = planned_run(scoped, warehouse, rutf, places)
        page = scoped.post(
            reverse("supply_chain:distribution_from_message", args=[run["id"]]),
            {"step": "read", "text": "Wuse PHC - 1800 sachets"},
        )
        body = page.content.decode()
        assert "matches the plan" in body and "1,800 sachets at 150 to the carton is 12 cartons" in body

    def test_a_message_in_sachets_is_flagged_when_the_carton_size_is_not_known(self, scoped, warehouse, rutf, places):
        run = planned_run(scoped, warehouse, rutf, places)
        page = scoped.post(
            reverse("supply_chain:distribution_from_message", args=[run["id"]]),
            {"step": "read", "text": "Wuse PHC - 1800 sachets"},
        )
        assert "is not known" in page.content.decode()

    def test_a_message_confirms_part_of_the_run_and_is_kept(self, scoped, warehouse, rutf, places):
        run = planned_run(scoped, warehouse, rutf, places)
        text = "Wuse PHC - 12 cartons\nGarki PHC - 8 cartons"
        page = scoped.post(
            reverse("supply_chain:distribution_from_message", args=[run["id"]]), {"step": "read", "text": text}
        )
        body = page.content.decode()
        assert page.status_code == 200
        assert "matches the plan" in body and "2 fewer than planned" in body

        wuse = DistributionLine.objects.get(distribution_id=run["id"], to_supply_point=places[0])
        garki = DistributionLine.objects.get(distribution_id=run["id"], to_supply_point=places[1])
        scoped.post(
            reverse("supply_chain:distribution_from_message", args=[run["id"]]),
            {
                "step": "confirm",
                "text": text,
                "source": "partner_reported",
                f"given_{wuse.pk}": "12",
                f"given_{garki.pk}": "",
            },
        )
        wuse.refresh_from_db()
        garki.refresh_from_db()
        assert wuse.quantity_given == Decimal("12") and wuse.movement_id is not None
        assert garki.quantity_given is None, "left blank stays planned"
        distribution = Distribution.objects.get(pk=run["id"])
        assert distribution.status == "partly_confirmed"
        assert distribution.message_text == text
        assert wuse.movement.source == "partner_reported"

    def test_the_given_quantity_not_the_planned_one_reaches_the_ledger(self, scoped, warehouse, rutf, places):
        run = planned_run(scoped, warehouse, rutf, places)
        garki = DistributionLine.objects.get(distribution_id=run["id"], to_supply_point=places[1])
        scoped.post(
            reverse("supply_chain:distribution_from_message", args=[run["id"]]),
            {"step": "confirm", "text": "Garki 8", "source": "partner_reported", f"given_{garki.pk}": "8"},
        )
        garki.refresh_from_db()
        assert garki.movement.quantity == Decimal("8")
        # the two cartons not given are no longer promised either
        assert ledger.committed(PROGRAM, warehouse).amount == Decimal("12")

    def test_a_photo_is_kept_as_evidence_even_before_anything_is_confirmed(self, scoped, warehouse, rutf, places):
        run = planned_run(scoped, warehouse, rutf, places)
        photo = SimpleUploadedFile("list.jpg", b"\xff\xd8\xff\xe0 not really a jpeg", content_type="image/jpeg")
        page = scoped.post(
            reverse("supply_chain:distribution_from_message", args=[run["id"]]), {"step": "read", "photo": photo}
        )
        assert page.status_code == 200
        assert Document.objects.filter(distribution_id=run["id"], kind="photo").count() == 1
        assert Distribution.objects.get(pk=run["id"]).status == "planned"

    def test_a_line_already_confirmed_cannot_be_confirmed_twice(self, scoped, warehouse, rutf, places):
        run = planned_run(scoped, warehouse, rutf, places)
        scoped.post(reverse("supply_chain:distribution_confirm_as_planned", args=[run["id"]]))
        again = scoped.post(reverse("supply_chain:distribution_confirm_as_planned", args=[run["id"]]))
        assert again.status_code == 302
        assert Movement.objects.filter(distribution_id=run["id"]).count() == 2, "no second posting"

    def test_the_page_lists_waiting_handouts_and_deliveries_to_facilities(self, scoped, warehouse, rutf, places):
        planned_run(scoped, warehouse, rutf, places)
        Movement.objects.create(
            program_id=PROGRAM,
            kind="transfer",
            occurred_on="2026-11-02",
            from_supply_point=warehouse,
            to_supply_point=places[0],
            commodity=rutf,
            quantity=Decimal("40"),
            quantity_unit="carton",
            source="we_recorded",
        )
        body = scoped.get(reverse("supply_chain:distribution")).content.decode()
        assert "Handouts waiting to be confirmed" in body and "Monday handout" in body
        assert "Deliveries to stores and facilities" in body
        assert "Central warehouse" in body and "Wuse PHC" in body


class TestBothSidesOfAHandover:
    def test_the_receiver_confirms_and_a_gap_is_shown(self, scoped, warehouse, rutf, places):
        run = planned_run(scoped, warehouse, rutf, places)
        scoped.post(reverse("supply_chain:distribution_confirm_as_planned", args=[run["id"]]))
        line = DistributionLine.objects.get(distribution_id=run["id"], to_supply_point=places[0])
        scoped.post(reverse("supply_chain:distribution_received", args=[line.pk]), {"quantity_received": "10"})
        line.refresh_from_db()
        assert line.quantity_received == Decimal("10")
        body = scoped.get(reverse("supply_chain:distribution")).content.decode()
        assert "less than given" in body

    def test_it_cannot_be_confirmed_received_twice(self, scoped, warehouse, rutf, places):
        run = planned_run(scoped, warehouse, rutf, places)
        scoped.post(reverse("supply_chain:distribution_confirm_as_planned", args=[run["id"]]))
        line = DistributionLine.objects.get(distribution_id=run["id"], to_supply_point=places[0])
        scoped.post(reverse("supply_chain:distribution_received", args=[line.pk]), {"quantity_received": "10"})
        scoped.post(reverse("supply_chain:distribution_received", args=[line.pk]), {"quantity_received": "12"})
        line.refresh_from_db()
        assert line.quantity_received == Decimal("10")


class TestPlanningAHandout:
    def post(self, warehouse, **extra):
        data = {
            "supply_point": warehouse.pk,
            "commodity": Commodity.objects.get(slug="rutf").pk,
            "opportunity_id": str(OPPORTUNITY),
            "distributed_on": "2026-11-09",
            "reference": "Monday",
            "source": "we_recorded",
            "lines-TOTAL_FORMS": "0",
            "lines-INITIAL_FORMS": "0",
            "lines-MIN_NUM_FORMS": "0",
            "lines-MAX_NUM_FORMS": "1000",
        }
        data.update(extra)
        return data

    def test_a_typed_list_is_saved_as_a_plan(self, scoped, warehouse, rutf, places):
        data = self.post(
            warehouse,
            **{
                "lines-TOTAL_FORMS": "1",
                "lines-0-to_supply_point": places[0].pk,
                "lines-0-quantity": "12",
                "lines-0-quantity_unit": "carton",
            },
        )
        response = scoped.post(reverse("supply_chain:distribution_plan"), data)
        assert response.status_code == 302
        run = Distribution.objects.get(reference="Monday")
        assert run.status == "planned" and run.commodity_id == rutf.pk
        assert not Movement.objects.filter(distribution=run).exists()

    def test_the_page_narrows_to_one_partner_with_partner_in_the_address(self, scoped, warehouse, rutf, places):
        from connect_labs.labs.models import LabsOrg

        first, second = LabsOrg.objects.create(slug="org-a", name="Org A"), LabsOrg.objects.create(
            slug="org-b", name="Org B"
        )
        for place, org in zip(places, (first, second)):
            place.managed_by_org = org
            place.save()
        one = planned_run(scoped, warehouse, rutf, places[:1], quantities=(12,))
        Distribution.objects.filter(pk=one["id"]).update(reference="Handout for A")
        planned_run(scoped, warehouse, rutf, places[1:], quantities=(10,))
        everyone = scoped.get(reverse("supply_chain:distribution")).content.decode()
        assert "Handout for A" in everyone and "Monday handout" in everyone
        narrowed = scoped.get(reverse("supply_chain:distribution"), {"partner": first.pk}).content.decode()
        assert "Handout for A" in narrowed and "Monday handout" not in narrowed
        assert "Org B" in narrowed, "the switcher still lists every partner"

    def test_the_page_offers_to_generate_the_handout_from_the_plan(self, scoped, warehouse, rutf, places):
        body = scoped.get(reverse("supply_chain:distribution")).content.decode()
        assert "Fill in this week" in body and "distribution/plan/?from_plan=1" in body
        form = scoped.get(reverse("supply_chain:distribution_plan") + "?from_plan=1").content.decode()
        assert 'name="from_forecast"' in form and "checked" in form.split('name="from_forecast"')[1][:200]

    def test_a_plan_with_no_list_and_no_forecast_is_refused(self, scoped, warehouse, rutf, places):
        response = scoped.post(reverse("supply_chain:distribution_plan"), self.post(warehouse))
        assert response.status_code == 200
        assert not Distribution.objects.filter(reference="Monday").exists()

    def test_asking_for_the_forecast_with_nothing_to_forecast_says_so(self, scoped, warehouse, rutf, places):
        response = scoped.post(reverse("supply_chain:distribution_plan"), self.post(warehouse, from_forecast="on"))
        assert response.status_code == 200
        assert "the plan has nothing to issue" in response.content.decode()
        assert not Distribution.objects.filter(reference="Monday").exists()


class TestReceiptForAWholeRun:
    def _confirmed(self, scoped, warehouse, rutf, places):
        run = planned_run(scoped, warehouse, rutf, places)
        scoped.post(reverse("supply_chain:distribution_confirm_as_planned", args=[run["id"]]))
        return run

    def test_all_received_as_given_in_one_step(self, scoped, warehouse, rutf, places):
        run = self._confirmed(scoped, warehouse, rutf, places)
        page = scoped.get(reverse("supply_chain:distribution")).content.decode()
        assert "All received as given" in page and "They received" not in page
        response = scoped.post(reverse("supply_chain:distribution_received_all", args=[run["id"]]))
        assert response.status_code == 302
        quantities = {line.quantity_received for line in DistributionLine.objects.filter(distribution_id=run["id"])}
        assert quantities == {Decimal("12"), Decimal("10")}
        assert "All received as given" not in scoped.get(reverse("supply_chain:distribution")).content.decode()

    def test_exceptions_override_only_the_lines_named(self, scoped, warehouse, rutf, places):
        run = self._confirmed(scoped, warehouse, rutf, places)
        short = DistributionLine.objects.get(distribution_id=run["id"], to_supply_point=places[1])
        scoped.post(reverse("supply_chain:distribution_received_all", args=[run["id"]]), {f"received_{short.pk}": "7"})
        short.refresh_from_db()
        other = DistributionLine.objects.get(distribution_id=run["id"], to_supply_point=places[0])
        assert short.quantity_received == Decimal("7") and other.quantity_received == Decimal("12")

    def test_the_run_names_its_partner_and_says_who_reported_it(self, scoped, warehouse, rutf, places):
        from connect_labs.labs.models import LabsOrg

        org = LabsOrg.objects.create(slug="org-z", name="Org Z")
        warehouse.managed_by_org = org
        warehouse.save()
        self._confirmed(scoped, warehouse, rutf, places)
        body = scoped.get(reverse("supply_chain:distribution")).content.decode()
        assert "· Org Z" in body and "Recorded by us" in body
        assert "told by" not in body and "Plan one by hand" in body
