"""An update link that follows its organisation: everything involving it, now and later.

THIS REPOSITORY IS PUBLIC. Every organisation, product, reference and figure
here is invented.

The walkthrough that asked for this: a partner received goods on a cover order
created AFTER its link was issued. A listed link names its orders one by one,
so it could not record that receipt, and the programme officer recorded it for
them. A link issued with `coverage="organisation"` is resolved afresh at every
request, so the cover order is covered the moment it exists.

A rule is exactly what widens silently, so most of this file is about what it
must NOT reach: another organisation's order, another programme, an order the
organisation is not party to, a dispatch by an organisation that does not
supply the order, and anything at all once the link has expired or been
revoked.
"""

import re
from datetime import date, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.models import AwardApproval, Contract, Receipt, Shipment, Supplier, SupplyPoint
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.update_links import service, tokens
from connect_labs.supply_chain.update_links.models import UpdateLink

pytestmark = pytest.mark.django_db

PROGRAM = 10661
OTHER_PROGRAM = 10662


def op(da, name, **payload):
    return call_operation(name, da, payload)


@pytest.fixture
def da():
    return SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)


@pytest.fixture
def other_da():
    return SupplyDataAccess(access_token="unused", program_id=OTHER_PROGRAM, caller=SYSTEM)


def _catalogue(da):
    op(
        da,
        "commodity_upsert",
        data={"slug": "rutf", "name": "RUTF sachet", "base_unit": "sachet", "pack_unit": "carton"},
    )
    return op(
        da,
        "item_upsert",
        data={
            "sku": "rutf-92g",
            "name": "RUTF 92 g sachet",
            "commodity_slug": "rutf",
            "base_unit": "sachet",
            "pack_unit": "carton",
            "base_per_pack": 150,
        },
    )


def _store(da, slug, name, manager=None):
    data = {"slug": slug, "name": name, "kind": "regional_store", "source": "we_recorded"}
    if manager is not None:
        data["managed_by_org_id"] = manager["id"]
    return op(da, "supply_point_upsert", data=data)


def _order(da, item, supplier, buyer, store, reference, buyer_of_record="programme_org", status="placed"):
    return op(
        da,
        "contract_create",
        data={
            "commodity_slug": "rutf",
            "supplier_id": supplier["id"],
            "item_id": item["id"],
            "buyer_of_record": buyer_of_record,
            "buyer_org_id": buyer["id"],
            "source": "we_recorded",
            "reference": reference,
            "status": status,
            "quantity": "40",
            "quantity_unit": "carton",
            "delivery_supply_point_id": store["id"],
        },
    )


@pytest.fixture
def world(da):
    item = _catalogue(da)
    us = op(da, "org_upsert", data={"slug": "baobab-programme", "name": "Baobab programme"})
    distributor = op(da, "org_upsert", data={"slug": "sahelian-dist", "name": "Sahelian Distributors"})
    partner = op(da, "org_upsert", data={"slug": "kanem-care", "name": "Kanem Community Care"})
    neighbour = op(da, "org_upsert", data={"slug": "lake-outreach", "name": "Lake Outreach"})
    sahelian = op(da, "supplier_create", data={"name": "Sahelian Distributors", "type": "distributor"})
    Supplier.objects.filter(pk=sahelian["id"]).update(org_id=distributor["id"])
    corner = op(da, "supplier_create", data={"name": "Corner Pharmacy Wholesale", "type": "trader"})
    partner_store = _store(da, "kanem-store", "Kanem district store", manager=partner)
    neighbour_store = _store(da, "lake-store", "Lake district store", manager=neighbour)
    central = _store(da, "central", "Central warehouse")
    world = {
        "item": item,
        "us": us,
        "distributor": distributor,
        "partner": partner,
        "neighbour": neighbour,
        "sahelian": sahelian,
        "corner": corner,
        "partner_store": partner_store,
        "neighbour_store": neighbour_store,
        "central": central,
        # The distributor's order, delivered to the partner's store.
        "order": _order(da, item, sahelian, us, partner_store, "BAO-PO-0801"),
        # The same distributor's order for the NEIGHBOUR's store.
        "neighbours_order": _order(da, item, sahelian, us, neighbour_store, "BAO-PO-0802"),
        # An order nobody in this file is party to but the programme.
        "unrelated": _order(da, item, corner, us, central, "BAO-PO-0803"),
    }
    return world


def _issue(da, org, **extra):
    issued = op(da, "update_link_issue", data={"org_id": org["id"], "coverage": "organisation", **extra})
    return issued, UpdateLink.objects.get(pk=issued["id"])


@pytest.fixture
def partner_link(da, world):
    return _issue(da, world["partner"], label="Kanem — everything")


@pytest.fixture
def distributor_link(da, world):
    return _issue(da, world["distributor"], label="Sahelian — everything")


def _cover(da, world):
    """The partner buys the shortfall locally -- AFTER its link was issued."""
    return _order(
        da,
        world["item"],
        world["corner"],
        world["partner"],
        world["partner_store"],
        "KCC-LP-0921",
        buyer_of_record="partner_org",
    )


def _receipt(link, contract_id, store_id, quantity="10"):
    return service.submit(
        link,
        "record_receipt",
        {
            "contract": Contract.objects.get(pk=contract_id),
            "supply_point": SupplyPoint.objects.get(pk=store_id),
            "received_on": date(2026, 9, 22),
            "quantity_accepted": quantity,
            "unit_basis": "pack",
        },
    )


def _dispatch(link, contract_id):
    return service.submit(
        link,
        "record_shipment",
        {
            "contract": Contract.objects.get(pk=contract_id),
            "status": "dispatched",
            "quantity": "40",
            "unit_basis": "pack",
        },
    )


def _ids(queryset):
    return set(queryset.values_list("pk", flat=True))


# ---- issuing ---------------------------------------------------------------


class TestIssuing:
    def test_the_link_says_it_follows_the_organisation_and_what_it_covers_today(self, partner_link, world):
        issued, link = partner_link
        assert issued["coverage"] == "organisation" and issued["follows_org"] is True
        assert issued["contract_ids"] == [world["order"]["id"]]
        assert issued["supply_point_ids"] == [world["partner_store"]["id"]]
        # No join rows: the scope is a rule, not a list.
        assert not link.contracts.exists() and not link.supply_points.exists()

    def test_it_cannot_also_name_rows(self, da, world):
        with pytest.raises(ValueError, match="names no orders"):
            op(
                da,
                "update_link_issue",
                data={
                    "org_id": world["partner"]["id"],
                    "coverage": "organisation",
                    "contract_ids": [world["unrelated"]["id"]],
                },
            )

    def test_it_can_be_issued_before_anything_involves_the_organisation(self, da, world):
        newcomer = op(da, "org_upsert", data={"slug": "new-llo", "name": "Newcomer LLO"})
        issued, link = _issue(da, newcomer)
        assert issued["contract_ids"] == [] and issued["supply_point_ids"] == []
        assert link.is_usable

    def test_a_listed_link_is_still_the_default_and_still_does_not_widen(self, da, world):
        issued = op(
            da,
            "update_link_issue",
            data={
                "org_id": world["partner"]["id"],
                "contract_ids": [world["order"]["id"]],
                "supply_point_ids": [world["partner_store"]["id"]],
            },
        )
        assert issued["coverage"] == "listed"
        cover = _cover(da, world)
        link = UpdateLink.objects.get(pk=issued["id"])
        assert cover["id"] not in _ids(service.scope_for(link).contracts)
        with pytest.raises(service.OutOfScope):
            _receipt(link, cover["id"], world["partner_store"]["id"])


# ---- it follows the organisation --------------------------------------------


class TestAnOrderCreatedAfterIssueIsCovered:
    def test_the_cover_order_is_in_scope_the_moment_it_exists(self, da, world, partner_link):
        _, link = partner_link
        cover = _cover(da, world)
        assert cover["id"] in _ids(service.scope_for(link).contracts)
        assert cover["id"] in _ids(service.scope_for(link).received)

    def test_the_partner_records_its_own_receipt_on_it(self, da, world, partner_link):
        _, link = partner_link
        cover = _cover(da, world)
        result = _receipt(link, cover["id"], world["partner_store"]["id"], quantity="12")
        receipt = Receipt.objects.get(pk=result["id"])
        assert receipt.contract_id == cover["id"]
        assert (receipt.source, receipt.recorded_by_org_id) == ("partner_reported", world["partner"]["id"])

    def test_the_page_lists_it_without_a_new_link(self, client, da, world, partner_link):
        issued, _ = partner_link
        url = reverse("supply_chain:update_link_public", args=[issued["token"]])
        assert "KCC-LP-0921" not in client.get(url).content.decode()
        _cover(da, world)
        body = client.get(url).content.decode()
        assert "KCC-LP-0921" in body
        assert "BAO-PO-0801" in body
        assert "data-follows-org" in body
        assert "Kanem district store" in body

    def test_a_store_it_starts_running_later_is_covered(self, da, world, partner_link):
        _, link = partner_link
        later = _store(da, "kanem-annex", "Kanem annex", manager=world["partner"])
        assert later["id"] in _ids(service.scope_for(link).supply_points)

    def test_an_approval_asked_of_it_later_is_covered_and_only_that_one(self, da, world, partner_link):
        _, link = partner_link
        tender = op(
            da,
            "tender_create",
            data={
                "label": "RUTF top-up",
                "delivery_point": {"city": "Mao"},
                "lines": [{"commodity_slug": "rutf", "quantity": "10", "quantity_unit": "carton"}],
            },
        )
        quote = op(
            da,
            "quote_record",
            data={
                "tender_id": tender["id"],
                "commodity_slug": "rutf",
                "supplier_id": world["corner"]["id"],
                "item_id": world["item"]["id"],
                "as_quoted_amount": "52.00",
                "as_quoted_unit": "per_pack",
            },
        )
        award = op(da, "award_create", tender_id=tender["id"], quote_id=quote["id"], rationale="Closest stock")
        ours = op(
            da,
            "approval_request",
            data={"award_id": award["id"], "approver_org_id": world["partner"]["id"], "role": "technical"},
        )
        theirs = op(
            da,
            "approval_request",
            data={"award_id": award["id"], "approver_org_id": world["neighbour"]["id"], "role": "funder"},
        )
        assert _ids(service.scope_for(link).approvals) == {ours["id"]}
        service.submit(
            link, "record_answer", {"approval": AwardApproval.objects.get(pk=ours["id"]), "status": "approved"}
        )
        assert AwardApproval.objects.get(pk=ours["id"]).status == "approved"
        with pytest.raises(service.OutOfScope):
            service.submit(
                link, "record_answer", {"approval": AwardApproval.objects.get(pk=theirs["id"]), "status": "approved"}
            )
        assert AwardApproval.objects.get(pk=theirs["id"]).is_pending


# ---- what it must not reach ---------------------------------------------------


class TestWhatItMustNotReach:
    def test_another_organisations_order_and_store_are_refused(self, client, world, partner_link):
        issued, link = partner_link
        scope = service.scope_for(link)
        assert world["neighbours_order"]["id"] not in _ids(scope.contracts)
        assert world["neighbour_store"]["id"] not in _ids(scope.supply_points)
        with pytest.raises(service.OutOfScope):
            _receipt(link, world["neighbours_order"]["id"], world["partner_store"]["id"])
        with pytest.raises(service.OutOfScope):
            _receipt(link, world["order"]["id"], world["neighbour_store"]["id"])
        body = client.get(reverse("supply_chain:update_link_public", args=[issued["token"]])).content.decode()
        assert "BAO-PO-0802" not in body and "Lake district store" not in body
        assert not Receipt.objects.exists()

    def test_an_order_it_is_not_party_to_is_refused(self, world, partner_link, distributor_link):
        for _, link in (partner_link, distributor_link):
            assert world["unrelated"]["id"] not in _ids(service.scope_for(link).contracts)
        with pytest.raises(service.OutOfScope):
            _receipt(partner_link[1], world["unrelated"]["id"], world["partner_store"]["id"])
        with pytest.raises(service.OutOfScope):
            service.submit(
                distributor_link[1], "confirm_order", {"contract": Contract.objects.get(pk=world["unrelated"]["id"])}
            )
        assert Contract.objects.get(pk=world["unrelated"]["id"]).status == "placed"

    def test_another_programme_is_refused_though_the_organisation_is_the_same(self, other_da, world, partner_link):
        # Organisations are labs-wide; the same partner runs a store and buys
        # in another programme. The link belongs to THIS programme only.
        item = _catalogue(other_da)
        store = _store(other_da, "kanem-store-2", "Kanem store (other programme)", manager=world["partner"])
        supplier = op(other_da, "supplier_create", data={"name": "Corner Pharmacy Wholesale", "type": "trader"})
        elsewhere = _order(other_da, item, supplier, world["partner"], store, "OTH-PO-1", "partner_org")
        _, link = partner_link
        scope = service.scope_for(link)
        assert elsewhere["id"] not in _ids(scope.contracts)
        assert store["id"] not in _ids(scope.supply_points)
        with pytest.raises(service.OutOfScope):
            _receipt(link, elsewhere["id"], store["id"])
        assert not Receipt.objects.exists()

    def test_a_draft_or_cancelled_order_is_not_reached(self, da, world, partner_link):
        _, link = partner_link
        draft = _order(
            da,
            world["item"],
            world["corner"],
            world["partner"],
            world["partner_store"],
            "KCC-D",
            "partner_org",
            "draft",
        )
        assert draft["id"] not in _ids(service.scope_for(link).contracts)


class TestActionsStayRoleAppropriate:
    def test_the_receiving_partner_cannot_dispatch_or_confirm(self, client, world, partner_link):
        issued, link = partner_link
        with pytest.raises(service.OutOfScope):
            _dispatch(link, world["order"]["id"])
        with pytest.raises(service.OutOfScope):
            service.submit(link, "confirm_order", {"contract": Contract.objects.get(pk=world["order"]["id"])})
        assert not Shipment.objects.exists()
        assert Contract.objects.get(pk=world["order"]["id"]).status == "placed"
        body = client.get(reverse("supply_chain:update_link_public", args=[issued["token"]])).content.decode()
        titles = re.findall(r'<span class="text-base font-semibold text-gray-900">([^<]+)</span>', body)
        assert "Record a dispatch" not in titles and "Confirm an order" not in titles
        assert "Record goods received" in titles

    def test_the_partner_cannot_dispatch_its_own_cover_order_either(self, da, world, partner_link):
        # It BUYS the cover order; the pharmacy supplies it.
        _, link = partner_link
        cover = _cover(da, world)
        with pytest.raises(service.OutOfScope):
            _dispatch(link, cover["id"])

    def test_the_supplier_dispatches_every_order_it_supplies_including_new_ones(self, da, world, distributor_link):
        _, link = distributor_link
        later = _order(da, world["item"], world["sahelian"], world["us"], world["central"], "BAO-PO-0901")
        for contract_id in (world["order"]["id"], world["neighbours_order"]["id"], later["id"]):
            result = _dispatch(link, contract_id)
            shipment = Shipment.objects.get(pk=result["id"])
            assert (shipment.source, shipment.recorded_by_org_id) == (
                "supplier_reported",
                world["distributor"]["id"],
            )

    def test_the_supplier_is_not_offered_or_allowed_a_receipt(self, client, world, distributor_link):
        issued, link = distributor_link
        assert not service.scope_for(link).received.exists()
        with pytest.raises(service.OutOfScope):
            _receipt(link, world["order"]["id"], world["partner_store"]["id"])
        body = client.get(reverse("supply_chain:update_link_public", args=[issued["token"]])).content.decode()
        assert "Record goods received" not in body
        assert "Record a dispatch" in body

    def test_only_the_supplier_confirms_a_payment(self, da, world, partner_link, distributor_link):
        invoice = op(
            da,
            "invoice_record",
            data={
                "contract_id": world["order"]["id"],
                "number": "SD-INV-44",
                "amount": "2000.00",
                "currency": "USD",
                "issued_on": "2026-09-01",
                "source": "we_recorded",
            },
        )
        payment = op(
            da,
            "payment_record",
            data={
                "invoice_id": invoice["id"],
                "amount": "2000.00",
                "currency": "USD",
                "paid_on": "2026-09-10",
                "source": "we_recorded",
            },
        )
        assert payment["id"] in _ids(service.scope_for(distributor_link[1]).payments)
        assert payment["id"] not in _ids(service.scope_for(partner_link[1]).payments)


class TestExpiredOrRevoked:
    def test_an_expired_link_writes_nothing_and_its_page_is_gone(self, client, da, world, partner_link):
        issued, link = partner_link
        cover = _cover(da, world)
        UpdateLink.objects.filter(pk=link.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        with pytest.raises(service.OutOfScope):
            _receipt(link, cover["id"], world["partner_store"]["id"])
        assert tokens.find_usable_link(issued["token"]) is None
        assert client.get(reverse("supply_chain:update_link_public", args=[issued["token"]])).status_code == 404
        assert not Receipt.objects.exists()

    def test_a_revoked_link_writes_nothing_and_its_page_is_gone(self, client, da, world, partner_link):
        issued, link = partner_link
        op(da, "update_link_revoke", link_id=link.pk)
        cover = _cover(da, world)
        with pytest.raises(service.OutOfScope):
            _receipt(link, cover["id"], world["partner_store"]["id"])
        assert client.get(reverse("supply_chain:update_link_public", args=[issued["token"]])).status_code == 404
        assert not Receipt.objects.exists()


# ---- the programme's screens --------------------------------------------------


@pytest.fixture
def scoped(client, django_user_model, monkeypatch):
    from connect_labs.supply_chain import form_views, views  # noqa: F401  -- bind before patching
    from connect_labs.supply_chain.api_views import _access as real_access

    account = django_user_model.objects.create_user(username="halima", password="x", email="halima@example.org")
    client.force_login(account)

    def _scoped(request):
        access = real_access(request)
        access.program_id = PROGRAM
        return access

    for module in ("form_views", "views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}.has_program_context", lambda request: True)
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}._access", _scoped)
    monkeypatch.setattr("connect_labs.supply_chain.update_links.views.has_program_context", lambda request: True)
    return client


class TestTheScreens:
    def test_the_issue_form_offers_the_choice(self, scoped, world):
        body = scoped.get(reverse("supply_chain:update_link_issue")).content.decode()
        assert "Only the orders, supply points and approvals ticked below" in body
        assert "Everything involving this organisation in this programme, including new orders" in body

    def test_issuing_an_organisation_link_from_the_form(self, scoped, world):
        response = scoped.post(
            reverse("supply_chain:update_link_issue"),
            {"org": world["partner"]["id"], "coverage": "organisation", "expires_in_days": 30},
        )
        assert response.status_code == 200, response.content.decode()[:2000]
        body = response.content.decode()
        assert "Everything involving Kanem Community Care in this programme" in body
        link = UpdateLink.objects.get()
        assert link.follows_org

    def test_the_form_refuses_both_at_once(self, scoped, world):
        response = scoped.post(
            reverse("supply_chain:update_link_issue"),
            {
                "org": world["partner"]["id"],
                "coverage": "organisation",
                "contracts": [world["order"]["id"]],
                "expires_in_days": 30,
            },
        )
        assert not UpdateLink.objects.exists()
        assert "names nothing itself" in response.content.decode()

    def test_the_list_says_which_kind_each_link_is(self, scoped, da, world, partner_link):
        op(
            da,
            "update_link_issue",
            data={"org_id": world["neighbour"]["id"], "contract_ids": [world["unrelated"]["id"]]},
        )
        _cover(da, world)
        body = scoped.get(reverse("supply_chain:update_links")).content.decode()
        assert 'data-coverage="organisation"' in body and 'data-coverage="listed"' in body
        assert "Everything involving Kanem Community Care" in body
        # What it reaches today, including the order created after it was issued.
        cell = body.split('data-coverage="organisation"', 1)[1].split("</td>", 1)[0]
        assert "KCC-LP-0921" in cell and "BAO-PO-0801" in cell
        assert "BAO-PO-0802" not in cell and "BAO-PO-0803" not in cell
