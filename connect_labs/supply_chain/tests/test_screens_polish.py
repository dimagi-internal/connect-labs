"""Cross-cutting fixes from five filmed procurements (CHC co-packs, an IPTSc
shortfall, a chlorine stop-gap, a dispenser import, test kits).

Each class is one thing more than one walkthrough's judge found, fixed once for
every screen rather than once per walkthrough.

THIS REPOSITORY IS PUBLIC. Every organisation, reference and figure here is
invented.
"""

import re
import sys
from datetime import date

import pytest
from django.urls import reverse

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.templatetags.supply_chain_extras import buyer_comparison

pytestmark = pytest.mark.django_db

PROGRAM = 10632
TODAY = date.today()


def op(da, name, **payload):
    return call_operation(name, da, payload)


@pytest.fixture
def da():
    return SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)


@pytest.fixture
def account(django_user_model):
    return django_user_model.objects.create_user(
        username="amina", password="x", email="amina@dimagi.com", name="Amina Bello"
    )


@pytest.fixture
def client_in_programme(client, account, monkeypatch):
    from connect_labs.supply_chain import (  # noqa: F401  -- import every screen module before patching
        distribution_views,
        form_views,
        fulfilment_views,
        network_views,
        reference_views,
        stock_views,
        views,
    )
    from connect_labs.supply_chain.alerts import views as alert_views  # noqa: F401
    from connect_labs.supply_chain.api_views import _access as real_access
    from connect_labs.supply_chain.procurement import views as procurement_views  # noqa: F401
    from connect_labs.supply_chain.update_links import views as link_views  # noqa: F401

    client.force_login(account)

    def _scoped(request):
        access = real_access(request)
        access.program_id = PROGRAM
        return access

    for name, module in list(sys.modules.items()):
        if not name.startswith("connect_labs.supply_chain") or name.endswith("api_views"):
            continue
        if hasattr(module, "_access"):
            monkeypatch.setattr(module, "_access", _scoped)
        if hasattr(module, "has_program_context"):
            monkeypatch.setattr(module, "has_program_context", lambda request: True)
    return client


@pytest.fixture
def world(da):
    op(
        da,
        "commodity_upsert",
        data={"slug": "chlorine", "name": "Chlorine", "base_unit": "L", "pack_unit": "jerry_can", "base_per_pack": 20},
    )
    supplier = op(da, "supplier_create", data={"name": "Sahel Chemicals", "type": "distributor"})
    us = op(da, "org_upsert", data={"slug": "us", "name": "The programme"})
    regulator = op(da, "org_upsert", data={"slug": "regulator", "name": "The regulator"})
    round_ = op(
        da,
        "round_create",
        data={
            "label": "Stop-gap chlorine",
            "delivery_point": {"city": "Kano"},
            "lines": [{"commodity_slug": "chlorine", "quantity": "600", "quantity_unit": "jerry_can"}],
        },
    )
    quote = op(
        da,
        "quote_record",
        data={
            "round_id": round_["id"],
            "commodity_slug": "chlorine",
            "supplier_id": supplier["id"],
            "as_quoted_amount": "4.00",
            "as_quoted_unit": "per_pack",
            "quantity_basis": "600",
            "quantity_basis_unit": "jerry_can",
        },
    )
    award = op(da, "award_create", round_id=round_["id"], quote_id=quote["id"], rationale="registered locally")
    return {"supplier": supplier, "us": us, "regulator": regulator, "round": round_, "quote": quote, "award": award}


class TestDonorSuppliers:
    def test_a_supplier_can_be_a_donor_and_the_pages_say_so(self, client_in_programme, da):
        donor = op(da, "supplier_create", data={"name": "Water For All", "type": "donor"})
        assert donor["type"] == "donor"
        body = client_in_programme.get(reverse("supply_chain:supplier_detail", args=[donor["id"]])).content.decode()
        assert "Donor" in body and "supplies in kind" in body
        listing = client_in_programme.get(reverse("supply_chain:suppliers")).content.decode()
        assert "Donor — supplies in kind" in listing

    def test_the_supplier_form_offers_donor(self, client_in_programme):
        body = client_in_programme.get(reverse("supply_chain:supplier_create")).content.decode()
        assert 'value="donor"' in body

    def test_a_picker_names_a_donor_as_one(self, client_in_programme, da, world):
        op(da, "supplier_create", data={"name": "Water For All", "type": "donor"})
        body = client_in_programme.get(reverse("supply_chain:contract_create")).content.decode()
        assert "Water For All (donor)" in body


class TestThePerBuyerPanelSaysWhatIsKnown:
    """CodeRabbit on #1975: the panel claimed different amounts from totals that
    were equal, and "none are payable" from totals that were unconfirmed."""

    def test_equal_confirmed_totals_are_said_to_be_the_same_and_nothing_more(self):
        cell = {"amount": "2400", "currency": "USD"}
        assert buyer_comparison({"programme_org": cell, "partner_org": dict(cell), "agency": dict(cell)}) == {
            "state": "same",
            "unconfirmed": [],
        }

    def test_the_same_amount_written_two_ways_is_the_same(self):
        compared = buyer_comparison(
            {
                "programme_org": {"amount": "2400", "currency": "USD"},
                "agency": {"amount": "2400.00", "currency": "USD"},
            }
        )
        assert compared["state"] == "same"

    def test_any_unconfirmed_total_means_it_cannot_be_said(self):
        cell = {"amount": "2400", "currency": "USD"}
        compared = buyer_comparison(
            {"programme_org": cell, "partner_org": {"unconfirmed": ["duty rate not known"]}, "agency": cell}
        )
        assert compared == {"state": "unknown", "unconfirmed": ["partner_org"]}

    def test_confirmed_totals_that_differ_are_said_to_differ(self):
        compared = buyer_comparison(
            {
                "programme_org": {"amount": "2400", "currency": "USD"},
                "partner_org": {"amount": "2760", "currency": "USD"},
                "agency": {"unconfirmed": ["x"]},
            }
        )
        assert compared == {"state": "differ", "unconfirmed": ["agency"]}

    def test_the_order_page_never_infers_none_payable_from_unconfirmed_totals(self, client_in_programme, da, world):
        order = op(
            da,
            "contract_create",
            data={
                "supplier_id": world["supplier"]["id"],
                "commodity_slug": "chlorine",
                "buyer_of_record": "partner_org",
                "buyer_org_id": world["us"]["id"],
                "reference": "CL-9",
                "quantity": "600",
                "quantity_unit": "jerry_can",
                "unit_price": "4.00",
                "unit_price_unit": "per_pack",
                "currency": "USD",
                "freight_basis": "included",
                "duties_basis": "not_specified",
                "vat_basis": "not_specified",
                "source": "we_recorded",
            },
        )
        body = client_in_programme.get(reverse("supply_chain:order_detail", args=[order["id"]])).content.decode()
        panel = body.split("The same order, per buyer", 1)[1][:4000]
        assert "none are payable" not in panel
        assert "costing different amounts" not in panel
        assert "cannot be said yet" in panel


def _registration(da, world):
    return op(
        da,
        "document_attach",
        data={
            "kind": "product_registration",
            "supplier_id": world["supplier"]["id"],
            "title": "Registration NG-0001",
            "external_url": "https://example.org/registration.pdf",
            "source": "document",
        },
    )


def _ask(da, world, **extra):
    return op(
        da,
        "approval_request",
        data={
            "award_id": world["award"]["id"],
            "approver_org_id": world["regulator"]["id"],
            "role": "regulatory",
            "requested_on": "2026-09-01",
            **extra,
        },
    )


class TestAttachADocumentFromTheBrowser:
    def test_a_quote_page_offers_attach_and_the_screen_links_to_the_quote(self, client_in_programme, world):
        quote_id = world["quote"]["id"]
        page = client_in_programme.get(reverse("supply_chain:procurement_quote_detail", args=[quote_id])).content
        attach = reverse("supply_chain:quote_document_attach", args=[quote_id])
        assert attach in page.decode()
        form = client_in_programme.get(attach).content.decode()
        assert re.search(r'value="quotation"\s+selected', form)
        response = client_in_programme.post(
            attach,
            {
                "kind": "quotation",
                "title": "Quotation Q-7",
                "external_url": "https://example.org/q7.pdf",
                "source": "document",
            },
        )
        assert response.status_code == 302
        from connect_labs.supply_chain.models import Document

        assert Document.objects.get(title="Quotation Q-7").quote_id == quote_id
        page = client_in_programme.get(reverse("supply_chain:procurement_quote_detail", args=[quote_id])).content
        assert "Quotation Q-7" in page.decode()

    def test_an_approval_takes_its_evidence_and_the_award_page_shows_it(self, client_in_programme, da, world):
        approval = _ask(da, world)
        award_url = reverse("supply_chain:award_detail", args=[world["award"]["id"]])
        attach = reverse("supply_chain:approval_document_attach", args=[approval["id"]])
        assert attach in client_in_programme.get(award_url).content.decode()
        form = client_in_programme.get(attach).content.decode()
        assert "The regulator" in form
        assert re.search(r'value="product_registration"\s+selected', form)
        response = client_in_programme.post(
            attach,
            {
                "kind": "other",
                "title": "Regulator's letter",
                "external_url": "https://example.org/letter.pdf",
                "source": "document",
            },
        )
        assert response.status_code == 302
        body = client_in_programme.get(award_url).content.decode()
        assert "Regulator&#x27;s letter" in body or "Regulator's letter" in body

    def test_a_checklist_line_preselects_its_kind_and_says_which_line_it_fills(self, client_in_programme, da, world):
        contract = op(
            da,
            "contract_create",
            data={
                "supplier_id": world["supplier"]["id"],
                "commodity_slug": "chlorine",
                "buyer_of_record": "programme_org",
                "buyer_org_id": world["us"]["id"],
                "reference": "CL-2",
                "quantity": "10",
                "quantity_unit": "jerry_can",
                "consideration": "in_kind",
                "source": "we_recorded",
            },
        )
        shipment = op(
            da,
            "shipment_record",
            data={
                "contract_id": contract["id"],
                "reference": "AWB-5",
                "status": "at_customs",
                "source": "we_recorded",
                "required_documents": [{"kind": "airway_bill", "owed_by_org_id": world["regulator"]["id"]}],
            },
        )
        page = client_in_programme.get(reverse("supply_chain:shipment_detail", args=[shipment["id"]])).content
        attach = reverse("supply_chain:shipment_document_attach", args=[shipment["id"]]) + "?kind=airway_bill"
        assert attach in page.decode()
        form = client_in_programme.get(attach).content.decode()
        assert re.search(r'value="airway_bill"\s+selected', form)
        assert "This fills the “Airway bill” line on the checklist for shipment AWB-5, owed by The regulator" in form

    def test_a_stored_document_opens_through_a_signed_redirect_and_is_scoped(self, client_in_programme, da, world):
        document = _registration(da, world)
        response = client_in_programme.get(reverse("supply_chain:document_open", args=[document["id"]]))
        assert response.status_code == 302
        assert response["Location"] == "https://example.org/registration.pdf"
        assert client_in_programme.get(reverse("supply_chain:document_open", args=[999999])).status_code == 404

    def test_a_refused_upload_is_a_message_not_a_server_error(self, da, world, monkeypatch):
        import base64

        from connect_labs.supply_chain.fulfilment import repository

        def refuse(*args, **kwargs):
            raise PermissionError("403 Forbidden")

        monkeypatch.setattr(repository.default_storage, "save", refuse)
        with pytest.raises(ValueError, match="refused this upload"):
            op(
                da,
                "document_attach",
                data={
                    "kind": "quotation",
                    "quote_id": world["quote"]["id"],
                    "filename": "q.pdf",
                    "content_base64": base64.b64encode(b"%PDF").decode(),
                    "source": "document",
                },
            )


class TestARegulatoryApprovalRestsOnARegistration:
    def test_it_references_the_registration_and_the_award_page_shows_it(self, client_in_programme, da, world):
        registration = _registration(da, world)
        approval = _ask(da, world, rests_on_document_id=registration["id"])
        assert approval["rests_on_document_id"] == registration["id"]
        body = client_in_programme.get(reverse("supply_chain:award_detail", args=[world["award"]["id"]])).content
        assert "Rests on" in body.decode() and "Registration NG-0001" in body.decode()

    def test_a_document_from_another_programme_is_refused(self, da, world):
        elsewhere = SupplyDataAccess(access_token="unused", program_id=PROGRAM + 1, caller=SYSTEM)
        op(elsewhere, "supplier_create", data={"name": "Elsewhere"})
        foreign = op(
            elsewhere,
            "document_attach",
            data={"kind": "product_registration", "external_url": "https://example.org/x.pdf", "source": "document"},
        )
        with pytest.raises(ValueError, match="does not exist in this programme"):
            _ask(da, world, rests_on_document_id=foreign["id"])

    def test_the_answer_can_name_it_too(self, client_in_programme, da, world):
        registration = _registration(da, world)
        approval = _ask(da, world)
        form = client_in_programme.get(reverse("supply_chain:approval_decide", args=[approval["id"]])).content
        assert "Registration NG-0001" in form.decode()
        decided = op(
            da,
            "approval_decide",
            approval_id=approval["id"],
            status="approved",
            rests_on_document_id=registration["id"],
        )
        assert decided["rests_on_document_id"] == registration["id"]


class TestPlaceOrderWhileAnApprovalIsPending:
    def test_the_button_is_shown_disabled_with_whose_answer_and_since_when(self, client_in_programme, da, world):
        _ask(da, world)
        body = client_in_programme.get(reverse("supply_chain:award_detail", args=[world["award"]["id"]])).content
        body = body.decode()
        assert "Place order" in body
        button = body.split("Place order</button>", 1)[0].rsplit("<button", 1)[1]
        assert "disabled" in button
        assert "regulatory approval from <b>The regulator</b>" in body
        assert "asked 1 Sep 2026 and not yet answered" in body
        assert reverse("supply_chain:contract_create") + "?award=" not in body

    def test_once_approved_it_is_a_link(self, client_in_programme, da, world):
        approval = _ask(da, world)
        op(da, "approval_decide", approval_id=approval["id"], status="approved")
        body = client_in_programme.get(reverse("supply_chain:award_detail", args=[world["award"]["id"]])).content
        assert reverse("supply_chain:contract_create") + f"?award={world['award']['id']}" in body.decode()


class TestAnAwardRecordsThePersonWhoDecided:
    def _compare(self, world):
        return reverse("supply_chain:procurement_comparison", args=[world["round"]["id"]]) + "?commodity=chlorine"

    def test_the_form_starts_with_the_signed_in_persons_name(self, client_in_programme, da, world):
        op(da, "quote_void", quote_id=world["quote"]["id"], reason="re-quoted")
        supplier = op(da, "supplier_create", data={"name": "Second chemicals"})
        op(
            da,
            "quote_record",
            data={
                "round_id": world["round"]["id"],
                "commodity_slug": "chlorine",
                "supplier_id": supplier["id"],
                "as_quoted_amount": "4.20",
                "as_quoted_unit": "per_pack",
                "quantity_basis": "600",
                "quantity_basis_unit": "jerry_can",
                "pack_spec_source": "stated_on_quote",
                "base_per_pack_stated": 20,
                "freight_basis": "included",
                "duties_basis": "included",
            },
        )
        body = client_in_programme.get(self._compare(world)).content.decode()
        assert 'name="decided_by" value="Amina Bello"' in body

    def test_what_is_typed_is_what_is_recorded(self, client_in_programme, da, world):
        from connect_labs.supply_chain.models import Award

        supplier = op(da, "supplier_create", data={"name": "Third chemicals"})
        quote = op(
            da,
            "quote_record",
            data={
                "round_id": world["round"]["id"],
                "commodity_slug": "chlorine",
                "supplier_id": supplier["id"],
                "as_quoted_amount": "3.90",
                "as_quoted_unit": "per_pack",
            },
        )
        client_in_programme.post(
            self._compare(world),
            {"quote_id": quote["id"], "rationale": "cheaper", "decided_by": "Ngozi Eze"},
        )
        assert Award.objects.get(quote_id=quote["id"]).decided_by == "Ngozi Eze"

    def test_left_blank_it_is_the_signed_in_persons_name_not_their_login(self, client_in_programme, da, world):
        from connect_labs.supply_chain.models import Award

        supplier = op(da, "supplier_create", data={"name": "Fourth chemicals"})
        quote = op(
            da,
            "quote_record",
            data={
                "round_id": world["round"]["id"],
                "commodity_slug": "chlorine",
                "supplier_id": supplier["id"],
                "as_quoted_amount": "3.80",
                "as_quoted_unit": "per_pack",
            },
        )
        client_in_programme.post(self._compare(world), {"quote_id": quote["id"], "rationale": "cheaper"})
        assert Award.objects.get(quote_id=quote["id"]).decided_by == "Amina Bello"


class TestCheckAlertsNow:
    """A new alert could only be seen working five minutes later, at the next
    beat. "Check now" runs the same pass for this programme, on demand."""

    def _subscribe(self, da):
        # A commodity with no ration table: `commodity_course_undefined` is true of it.
        op(da, "commodity_upsert", data={"slug": "rutf", "name": "RUTF", "base_unit": "sachet"})
        return op(
            da,
            "alert_subscription_create",
            data={"check_kinds": ["commodity_course_undefined"], "recipient_email": "stores@example.org"},
        )

    def test_the_alerts_page_offers_it(self, client_in_programme):
        body = client_in_programme.get(reverse("supply_chain:alerts")).content.decode()
        assert reverse("supply_chain:alert_check_now") in body
        assert "Check now" in body

    def test_it_finds_what_is_new_logs_it_and_says_so(self, client_in_programme, da):
        from connect_labs.supply_chain.alerts.models import AlertNotice

        sub = self._subscribe(da)
        response = client_in_programme.post(reverse("supply_chain:alert_check_now"), follow=True)
        body = response.content.decode()
        assert AlertNotice.objects.filter(
            subscription_id=sub["id"], subject_kind="commodity_course_undefined"
        ).exists()
        assert "Checked now:" in body and "new notice" in body
        assert "No ration table" in body  # in the log, as words

    def test_asking_twice_reports_nothing_new_the_second_time(self, client_in_programme, da):
        from connect_labs.supply_chain.alerts.models import AlertNotice

        self._subscribe(da)
        client_in_programme.post(reverse("supply_chain:alert_check_now"))
        body = client_in_programme.post(reverse("supply_chain:alert_check_now"), follow=True).content.decode()
        assert "Checked now: nothing new" in body
        assert AlertNotice.objects.filter(program_id=PROGRAM).count() == 1

    def test_it_runs_only_this_programmes_subscriptions(self, client_in_programme, da):
        from connect_labs.supply_chain.alerts.models import AlertNotice

        elsewhere = SupplyDataAccess(access_token="unused", program_id=PROGRAM + 7, caller=SYSTEM)
        other = self._subscribe(elsewhere)
        self._subscribe(da)
        client_in_programme.post(reverse("supply_chain:alert_check_now"))
        assert not AlertNotice.objects.filter(subscription_id=other["id"]).exists()

    def test_it_is_not_a_get(self, client_in_programme):
        assert client_in_programme.get(reverse("supply_chain:alert_check_now")).status_code == 405


def _durable(da):
    op(da, "commodity_upsert", data={"slug": "dispenser", "name": "Chlorine dispenser", "category": "equipment"})
    return op(
        da,
        "item_upsert",
        data={"sku": "disp", "name": "Wall dispenser", "commodity_slug": "dispenser", "stock_class": "durable"},
    )


class TestDurableMovementsAskForNoBatch:
    def test_the_item_picker_marks_durable_items_and_the_form_hides_batch_and_expiry(self, client_in_programme, da):
        dispenser = _durable(da)
        body = client_in_programme.get(reverse("supply_chain:movement_record")).content.decode()
        assert 'data-durable-hides="batch expiry"' in body
        option = re.search(rf'<option value="{dispenser["id"]}"[^>]*>', body).group(0)
        assert 'data-durable="1"' in option
        assert 'id="div_id_batch"' in body and 'id="div_id_expiry"' in body

    def test_a_batch_typed_for_a_durable_item_is_not_recorded(self, client_in_programme, da):
        from connect_labs.supply_chain.models import Movement

        dispenser = _durable(da)
        store = op(
            da,
            "supply_point_upsert",
            data={"slug": "wh", "name": "Warehouse", "kind": "central_store", "source": "we_recorded"},
        )
        response = client_in_programme.post(
            reverse("supply_chain:movement_record"),
            {
                "kind": "receipt",
                "occurred_on": "2026-09-01",
                "to_supply_point": store["id"],
                "commodity": _commodity_pk(),
                "item": dispenser["id"],
                "batch": "B-1",
                "expiry": "2027-01-01",
                "quantity": "4",
                "quantity_unit": "dispenser",
                "source": "we_recorded",
            },
        )
        assert response.status_code == 302, response.content.decode()[-2000:]
        movement = Movement.objects.get(item_id=dispenser["id"])
        assert movement.batch == "" and movement.expiry is None


def _commodity_pk():
    from connect_labs.supply_chain.models import Commodity

    return Commodity.objects.get(slug="dispenser").pk


class TestPickers:
    def test_currency_is_a_select_with_the_programmes_own_first(self, client_in_programme, da, world):
        op(
            da,
            "contract_create",
            data={
                "supplier_id": world["supplier"]["id"],
                "commodity_slug": "chlorine",
                "buyer_of_record": "programme_org",
                "buyer_org_id": world["us"]["id"],
                "reference": "NG-1",
                "quantity": "1",
                "quantity_unit": "jerry_can",
                "unit_price": "6000",
                "unit_price_unit": "per_pack",
                "currency": "NGN",
                "source": "we_recorded",
            },
        )
        body = client_in_programme.get(reverse("supply_chain:contract_create")).content.decode()
        select = re.search(r'<select[^>]*name="currency".*?</select>', body, re.S).group(0)
        assert '<optgroup label="Used in this programme">' in select
        used = select.split('<optgroup label="Used in this programme">', 1)[1].split("</optgroup>", 1)[0]
        assert 'value="NGN"' in used
        assert 'value="EUR"' in select

    def test_the_paid_to_picker_puts_this_programmes_payees_first(self, client_in_programme, da, world):
        contract = op(
            da,
            "contract_create",
            data={
                "supplier_id": world["supplier"]["id"],
                "commodity_slug": "chlorine",
                "buyer_of_record": "programme_org",
                "buyer_org_id": world["us"]["id"],
                "reference": "CL-3",
                "quantity": "1",
                "quantity_unit": "jerry_can",
                "consideration": "in_kind",
                "source": "we_recorded",
            },
        )
        shipment = op(
            da,
            "shipment_record",
            data={"contract_id": contract["id"], "status": "at_customs", "source": "we_recorded"},
        )
        agent = op(da, "org_upsert", data={"slug": "agent", "name": "Zenith Clearing"})
        op(
            da,
            "charge_record",
            data={
                "shipment_id": shipment["id"],
                "kind": "clearing",
                "payee_org_id": agent["id"],
                "amount": "100",
                "currency": "USD",
                "source": "we_recorded",
            },
        )
        body = client_in_programme.get(reverse("supply_chain:charge_record", args=[shipment["id"]])).content.decode()
        select = re.search(r'<select[^>]*name="payee_org".*?</select>', body, re.S).group(0)
        first = select.split('<optgroup label="Paid before in this programme">', 1)[1].split("</optgroup>", 1)[0]
        assert "Zenith Clearing" in first
        assert "The regulator" in select.split('<optgroup label="Every organisation">', 1)[1]
        assert "data-tomselect" in select


class TestTheQuotePageAsksNoCourseOfAConsumable:
    """The comparison stopped offering per-course figures, and stopped asking
    for a treatment protocol, for a category that has no course. The page for
    one quote kept doing both -- "USD per course: Unconfirmed" against
    water-treatment chlorine, and an internal question nobody could close. One
    rule, from the same place, on both screens."""

    def _quote_on(self, da, world, slug, name, category):
        op(
            da,
            "commodity_upsert",
            data={
                "slug": slug,
                "name": name,
                "category": category,
                "base_unit": "L",
                "pack_unit": "jerry_can",
                "base_per_pack": 20,
            },
        )
        round_ = op(
            da,
            "round_create",
            data={
                "label": f"{name} round",
                "delivery_point": {"city": "Kano"},
                "lines": [{"commodity_slug": slug, "quantity": "600", "quantity_unit": "jerry_can"}],
            },
        )
        return op(
            da,
            "quote_record",
            data={
                "round_id": round_["id"],
                "commodity_slug": slug,
                "supplier_id": world["supplier"]["id"],
                "as_quoted_amount": "4.00",
                "as_quoted_unit": "per_pack",
                "quantity_basis": "600",
                "quantity_basis_unit": "jerry_can",
            },
        )

    def _page(self, client, quote):
        return client.get(reverse("supply_chain:procurement_quote_detail", args=[quote["id"]])).content.decode()

    def test_a_consumable_quote_shows_no_course_figure_and_asks_no_protocol(self, client_in_programme, da, world):
        quote = self._quote_on(da, world, "dispenser-chlorine", "Dispenser chlorine solution", "consumable")
        body = self._page(client_in_programme, quote)
        assert "USD per course" not in body
        assert "USD per child treated" not in body
        assert "course definition" not in body
        assert "treatment protocol" not in body
        # The rest of the derivation is still there, named in the product's unit.
        assert "USD per jerry can" in body

    def test_the_operation_agrees_with_the_page(self, da, world):
        quote = self._quote_on(da, world, "dispenser-chlorine", "Dispenser chlorine solution", "consumable")
        detail = op(da, "quote_get", quote_id=quote["id"])
        assert not {"usd_per_course", "usd_per_child_treated"} & set(detail["figures"])
        assert "course_definition" not in {q["key"] for q in detail["missing"]}

    def test_a_therapeutic_food_quote_still_shows_and_asks_them(self, client_in_programme, da, world):
        quote = self._quote_on(da, world, "rutf-paste", "RUTF paste", "therapeutic_food")
        body = self._page(client_in_programme, quote)
        assert "USD per course" in body
        assert "USD per child treated" in body
        assert "treatment protocol" in body


class TestApprovalDatesDoNotWrap:
    def test_the_asked_and_answered_dates_are_nowrap(self, client_in_programme, da, world):
        approval = _ask(da, world)
        op(da, "approval_decide", approval_id=approval["id"], status="approved", decided_on="2026-09-24")
        body = client_in_programme.get(reverse("supply_chain:award_detail", args=[world["award"]["id"]])).content
        body = body.decode()
        asked = re.search(r"<td[^>]*>\s*1 Sep 2026\s*</td>", body).group(0)
        assert "whitespace-nowrap" in asked
        answered = re.search(r"<span[^>]*>\s*24 Sep 2026\s*</span>", body).group(0)
        assert "whitespace-nowrap" in answered


# ---- the chlorine stop-gap walkthrough, second pass -------------------------


def _late_delivery(**facts):
    """A `contract_delivery_overdue` check as `checks_list` returns it."""
    check = {
        "kind": "contract_delivery_overdue",
        "category": "threshold",
        "subject": {"type": "contract", "id": 128, "label": "A donor — Chlorine"},
        "audience": "supplier",
        "since": "2026-06-26",
        "days_open": 90,
        "facts": {
            "days_late": 90,
            "expected_on": "2026-06-26",
            "signed_on": "2026-04-27",
            "promised_lead_time_days": 60,
            "supplier": {"id": 4, "name": "A donor"},
            "status": "placed",
            "outstanding": "400",
            "unit": "jerry_can",
        },
    }
    check["facts"].update(facts)
    return check


class TestCheckFactsReadAsWords:
    """Each check card dumped its facts as raw keys -- "promised lead time days",
    "contract id 128", "unit jerry can" on a row of its own -- and repeated the
    header's "90 days since 2026-06-26" as "days late 90" and "expected on"."""

    def test_a_quantity_reads_with_its_unit_and_a_lead_time_in_days(self):
        from connect_labs.supply_chain.templatetags.supply_chain_extras import check_facts

        rows = dict(check_facts(_late_delivery()))
        assert rows["Promised lead time"] == "60 days"
        assert rows["Outstanding"] == "400 jerry cans"
        assert rows["Signed"] == "2026-04-27"
        assert rows["Status"] == "placed"

    def test_what_the_header_already_says_is_not_said_again(self):
        from connect_labs.supply_chain.templatetags.supply_chain_extras import check_facts

        labels = {label for label, _ in check_facts(_late_delivery())}
        # The age and its date are the header's; the supplier is in the label.
        assert not {"Days late", "Expected on", "Supplier", "Unit"} & labels
        assert all("_" not in label and label[0].isupper() for label in labels)

    def test_a_supplier_the_label_does_not_name_is_kept(self):
        from connect_labs.supply_chain.templatetags.supply_chain_extras import check_facts

        check = _late_delivery()
        check["subject"]["label"] = "DON-1 — Chlorine"
        assert dict(check_facts(check))["Supplier"] == "A donor"

    def test_ids_the_card_already_links_are_dropped(self):
        from connect_labs.supply_chain.templatetags.supply_chain_extras import check_facts

        check = {
            "kind": "shipment_overdue",
            "subject": {"type": "shipment", "id": 9, "label": "SHIP-1 — Chlorine — A donor"},
            "since": "2026-06-26",
            "days_open": 90,
            "facts": {
                "days_late": 90,
                "expected_on": "2026-06-26",
                "supplier": {"id": 4, "name": "A donor"},
                "status": "in_transit",
                "contract_id": 128,
            },
        }
        assert check_facts(check) == [("Status", "in transit")]

    def test_money_reads_with_its_currency_and_the_payment_date_is_the_header(self):
        from connect_labs.supply_chain.templatetags.supply_chain_extras import check_facts

        check = {
            "kind": "payment_unconfirmed",
            "subject": {"type": "payment", "id": 3, "label": "A donor — PAY-1"},
            "since": "2026-08-01",
            "days_open": 54,
            "facts": {
                "amount": "1000",
                "currency": "USD",
                "paid_on": "2026-08-01",
                "invoice_id": 7,
                "contract_id": 128,
                "supplier": {"id": 4, "name": "A donor"},
            },
        }
        assert check_facts(check) == [("Amount", "USD 1,000.00")]

    def test_a_count_and_a_ledger_each_carry_their_unit_and_a_yes_or_no_reads_as_one(self):
        from connect_labs.supply_chain.templatetags.supply_chain_extras import check_facts

        variance = {
            "kind": "stock_variance",
            "subject": {"type": "supply_point", "id": 2, "label": "Kano store"},
            "since": "2026-09-20",
            "days_open": 4,
            "facts": {
                "ledger": "120",
                "reported": "100",
                "variance": "20",
                "unit": "carton",
                "reconcilable": True,
                "reported_kind": "self_reported",
            },
        }
        rows = dict(check_facts(variance))
        assert rows["Ledger"] == "120 cartons"
        assert rows["Counted"] == "100 cartons"
        assert rows["Difference"] == "20 cartons"
        assert rows["Can be reconciled"] == "yes"
        assert rows["How it was counted"] == "self-reported"


class TestTheChecksPageReadsAsSentences:
    def _page(self, client_in_programme, da):
        op(da, "commodity_upsert", data={"slug": "chlorine", "name": "Chlorine", "base_unit": "L"})
        donor = op(da, "supplier_create", data={"name": "A donor"})
        us = op(da, "org_upsert", data={"slug": "us", "name": "The programme"})
        op(
            da,
            "contract_create",
            data={
                "commodity_slug": "chlorine",
                "supplier_id": donor["id"],
                "buyer_of_record": "programme_org",
                "buyer_org_id": us["id"],
                "consideration": "in_kind",
                "source": "we_recorded",
                "reference": "DON-1",
                "status": "placed",
                "quantity": "400",
                "quantity_unit": "jerry_can",
                "signed_on": "2026-01-01",
                "promised_lead_time_days": 60,
            },
        )
        return client_in_programme.get(reverse("supply_chain:checks")).content.decode()

    def test_a_late_delivery_reads_as_labelled_facts(self, client_in_programme, da):
        body = self._page(client_in_programme, da)
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body))
        assert "Promised lead time: 60 days" in text
        assert "Outstanding: 400 jerry cans" in text
        for raw in ("promised lead time days", "days late", "expected on", "contract id"):
            assert raw not in text

    def test_the_category_definition_is_dark_enough_to_read(self, client_in_programme, da):
        body = self._page(client_in_programme, da)
        chip = re.search(r'<span class="([^"]*)">— a figure past a limit you set</span>', body)
        assert chip, "the threshold chip carries its definition"
        assert "text-gray-600" in chip.group(1)
        assert "text-gray-400" not in chip.group(1)


class TestCheckNowSaysWhatIsAlreadyTrue:
    """ "Checked now: nothing new since the last check." sat directly above
    the sent-log rows it had found, and read as a contradiction."""

    def _subscribe(self, da):
        op(da, "commodity_upsert", data={"slug": "rutf", "name": "RUTF", "base_unit": "sachet"})
        return op(
            da,
            "alert_subscription_create",
            data={"check_kinds": ["commodity_course_undefined"], "recipient_email": "stores@example.org"},
        )

    def _twice(self, client_in_programme):
        client_in_programme.post(reverse("supply_chain:alert_check_now"))
        response = client_in_programme.post(reverse("supply_chain:alert_check_now"), follow=True)
        # The first press's message is still queued; the second's is last.
        return [str(m) for m in response.context["messages"]][-1:]

    def test_nothing_new_names_the_notices_already_sent_and_when(self, client_in_programme, da):
        from unittest.mock import patch

        from connect_labs.supply_chain.alerts import service
        from connect_labs.supply_chain.alerts.models import AlertNotice

        self._subscribe(da)
        with patch.object(service, "send_labs_email"), patch.object(service, "email_enabled", return_value=True):
            (said,) = self._twice(client_in_programme)
        sent_at = AlertNotice.objects.get(program_id=PROGRAM).sent_at
        assert said.startswith("Checked now: nothing new — the notice these alerts found was already sent")
        assert sent_at.strftime("%-d %b %Y, %H:%M") in said
        assert "since the last check" not in said

    def test_it_does_not_claim_sent_what_was_not(self, client_in_programme, da):
        # Email is off in tests: the notice was logged and not sent.
        self._subscribe(da)
        (said,) = self._twice(client_in_programme)
        assert said.startswith("Checked now: nothing new")
        assert "1 not sent — email is off" in said
        assert "already sent" not in said

    def test_it_says_so_when_these_alerts_have_found_nothing_at_all(self, client_in_programme, da):
        op(da, "alert_subscription_create", data={"check_kinds": ["stock_stockout"], "recipient_email": "a@b.org"})
        (said,) = self._twice(client_in_programme)
        assert said == "Checked now: nothing new — these alerts have not found anything yet."


class TestTheOrderPageSaysWhichAwardItWasPlacedAgainst:
    def _order(self, da, world, **extra):
        return op(
            da,
            "contract_create",
            data={
                "commodity_slug": "chlorine",
                "supplier_id": world["supplier"]["id"],
                "buyer_of_record": "programme_org",
                "buyer_org_id": world["us"]["id"],
                "source": "we_recorded",
                **extra,
            },
        )

    def test_it_names_the_award_who_decided_it_and_who_approved_it(self, client_in_programme, da, world):
        from connect_labs.supply_chain.models import Award

        Award.objects.filter(pk=world["award"]["id"]).update(decided_by="Amina Bello", decided_on=date(2026, 9, 2))
        approval = _ask(da, world)
        op(da, "approval_decide", approval_id=approval["id"], status="approved", decided_on="2026-09-10")
        order = self._order(da, world, award_id=world["award"]["id"], round_id=world["round"]["id"])
        body = client_in_programme.get(reverse("supply_chain:order_detail", args=[order["id"]])).content.decode()
        text = re.sub(r"\s+([,.;)])", r"\1", re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)))
        assert "Against the award to Sahel Chemicals, decided 2 Sep 2026 by Amina Bello" in text
        assert reverse("supply_chain:award_detail", args=[world["award"]["id"]]) in body
        assert "The regulator (Regulatory) approved 10 Sep 2026" in text

    def test_an_order_placed_without_an_award_says_nothing_of_one(self, client_in_programme, da, world):
        order = self._order(da, world)
        body = client_in_programme.get(reverse("supply_chain:order_detail", args=[order["id"]])).content.decode()
        assert "Against the award" not in body
        assert reverse("supply_chain:award_detail", args=[world["award"]["id"]]) not in body


class TestTheQuotePanelsSayWhatLandsInEach:
    """The supplier panel claimed "a certification, a registration", while a
    product registration filed with a quote lands in the quote's own panel."""

    def test_each_panel_describes_what_is_actually_filed_there(self, client_in_programme, world):
        body = client_in_programme.get(
            reverse("supply_chain:procurement_quote_detail", args=[world["quote"]["id"]])
        ).content.decode()
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body))
        assert "a certification, a registration" not in text
        assert "Documents filed with this offer — the quotation, a pro-forma invoice, the product's" in text
        assert "registration or certificate" in text
        assert "The company's own standing documents — its trading licence, a GDP certificate" in text


def _comparable_pair(da, world):
    """Two comparable chlorine quotes on the world's round, neither awarded yet."""
    op(da, "quote_void", quote_id=world["quote"]["id"], reason="re-quoted")
    for name, price in (("Second chemicals", "4.20"), ("Third chemicals", "4.40")):
        supplier = op(da, "supplier_create", data={"name": name})
        op(
            da,
            "quote_record",
            data={
                "round_id": world["round"]["id"],
                "commodity_slug": "chlorine",
                "supplier_id": supplier["id"],
                "as_quoted_amount": price,
                "as_quoted_unit": "per_pack",
                "quantity_basis": "600",
                "quantity_basis_unit": "jerry_can",
                "pack_spec_source": "stated_on_quote",
                "base_per_pack_stated": 20,
                "freight_basis": "included",
                "duties_basis": "included",
            },
        )


def _compare_url(world):
    return reverse("supply_chain:procurement_comparison", args=[world["round"]["id"]]) + "?commodity=chlorine"


class TestTheAwardControlsFitTheColumn:
    """At 1440px the reason, date, decided-by and Award button sat side by side
    and pushed the table past the content column, clipping the button off the
    right edge of the sideways-scrolling region."""

    def test_they_stack_rather_than_sit_in_one_row(self, client_in_programme, da, world):
        _comparable_pair(da, world)
        body = client_in_programme.get(_compare_url(world)).content.decode()
        forms = re.findall(r'<form method="post" action="" class="([^"]*)">(.*?)</form>', body, re.S)
        award_forms = [(cls, inner) for cls, inner in forms if 'name="rationale"' in inner]
        assert len(award_forms) == 2
        for cls, inner in award_forms:
            assert "flex-col" in cls and "w-56" in cls
            # Same fields, posting to the same URL.
            for field in ("quote_id", "rationale", "decided_on", "decided_by"):
                assert f'name="{field}"' in inner
            assert 'type="submit"' in inner and "Award" in inner

    def test_the_stacked_form_still_awards(self, client_in_programme, da, world):
        from connect_labs.supply_chain.models import Award, Quote

        _comparable_pair(da, world)
        quote = Quote.objects.get(supplier__org__name="Second chemicals")
        client_in_programme.post(
            _compare_url(world),
            {"quote_id": quote.pk, "rationale": "cheaper", "decided_on": "2026-09-20", "decided_by": "Ngozi Eze"},
        )
        award = Award.objects.get(quote_id=quote.pk)
        assert (award.decided_by, award.decided_on.isoformat()) == ("Ngozi Eze", "2026-09-20")


class TestAFigureHeaderNamesItsUnitInWords:
    """The comparison's header read "USD per jerry_can", the stored code."""

    def test_the_comparison_header_reads_jerry_can(self, client_in_programme, da, world):
        _comparable_pair(da, world)
        body = client_in_programme.get(_compare_url(world)).content.decode()
        assert "<th>USD per jerry can</th>" in body
        assert "jerry_can" not in "".join(re.findall(r"<th>(.*?)</th>", body))

    def test_the_operation_labels_its_columns_the_same_way(self, da, world):
        _comparable_pair(da, world)
        comparison = op(da, "round_compare", round_id=world["round"]["id"], commodity_slug="chlorine")
        labels = {c["key"]: c["label"] for c in comparison["columns"]}
        assert labels["usd_per_pack_normalized"] == "USD per jerry can"

    def test_the_quote_page_names_the_same_unit(self, client_in_programme, da, world):
        body = client_in_programme.get(
            reverse("supply_chain:procurement_quote_detail", args=[world["quote"]["id"]])
        ).content.decode()
        assert "USD per jerry can" in body
        assert "USD per pack<" not in body


class TestACheckSaysWhatItsAgeCountsFrom:
    """The checks list read "40 days since 2026-08-15" and "19 days since
    2026-09-05": two ages, two dates, and nothing to say that one is the
    dispatch and the other the date the consignment was expected. And a
    documents check owed entirely by other organisations read "Ours to
    answer" -- which is true only in the sense that we are the ones chasing."""

    def _check(self, kind, audience="internal", days=19, since="2026-09-05"):
        return {"kind": kind, "audience": audience, "days_open": days, "since": since, "facts": {}}

    def test_a_late_shipment_is_aged_past_its_expected_date(self):
        from connect_labs.supply_chain.templatetags.supply_chain_extras import check_age

        assert check_age(self._check("shipment_overdue")) == "19 days past the expected date, 2026-09-05"

    def test_outstanding_documents_are_aged_from_the_dispatch(self):
        from connect_labs.supply_chain.templatetags.supply_chain_extras import check_age

        check = self._check("shipment_documents_outstanding", days=1, since="2026-08-15")
        assert check_age(check) == "1 day since dispatch, 2026-08-15"

    def test_a_kind_without_a_phrase_still_reads(self):
        from connect_labs.supply_chain.templatetags.supply_chain_extras import check_age

        assert check_age(self._check("stock_variance", days=3)) == "3 days since 2026-09-05"
        assert check_age({"kind": "stock_variance", "days_open": None}) == ""

    def test_documents_owed_by_others_are_ours_to_chase(self):
        from connect_labs.supply_chain.templatetags.supply_chain_extras import check_audience

        assert check_audience(self._check("shipment_documents_outstanding")) == "Ours to chase"
        assert check_audience(self._check("award_not_contracted")) == "Ours to answer"
        assert check_audience(self._check("shipment_overdue", audience="supplier")) == ("Only the supplier can answer")
