"""Supplier update links: a door for an organisation with no labs login.

THIS REPOSITORY IS PUBLIC. Every organisation, product and number is invented.

Most of this file is adversarial, because the page behind a link is the one
place in the domain that authenticates nobody. What must hold:

  - every write goes through the ordinary operation, marked
    `supplier_reported` and attributed to the link's organisation;
  - the link reaches exactly the contracts and supply points it was issued
    for -- not another contract of the same supplier, not another programme,
    not after it expires, not after it is revoked;
  - an unknown, expired and revoked token are indistinguishable from outside;
  - the raw token is never stored.
"""

import re
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.models import Contract, Movement, Receipt, Shipment, StockCount
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.update_links import service, tokens
from connect_labs.supply_chain.update_links.models import UpdateLink, UpdateLinkSubmission

pytestmark = pytest.mark.django_db

PROGRAM = 10501
OTHER_PROGRAM = 10502


def op(da, name, **payload):
    return call_operation(name, da, payload)


@pytest.fixture
def da():
    return SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)


@pytest.fixture
def other_da():
    return SupplyDataAccess(access_token="unused", program_id=OTHER_PROGRAM, caller=SYSTEM)


def _world(da, suffix=""):
    op(
        da,
        "commodity_upsert",
        data={"slug": "ors-zinc", "name": "ORS/zinc co-pack", "base_unit": "copack", "pack_unit": "carton"},
    )
    item = op(
        da,
        "item_upsert",
        data={
            "sku": f"eha-copack{suffix}",
            "name": "EHA co-pack",
            "commodity_slug": "ors-zinc",
            "base_unit": "copack",
            "pack_unit": "carton",
            "base_per_pack": 50,
        },
    )
    supplier = op(da, "supplier_create", data={"name": f"EHA Clinics{suffix}", "type": "distributor"})
    eha = op(da, "org_upsert", data={"slug": f"eha{suffix}", "name": f"EHA Clinics{suffix}"})
    warehouse = op(
        da,
        "supply_point_upsert",
        data={
            "slug": f"eha-warehouse{suffix}",
            "name": "EHA warehouse",
            "kind": "central_store",
            "source": "we_recorded",
        },
    )
    llo = op(
        da,
        "supply_point_upsert",
        data={"slug": f"llo-store{suffix}", "name": "LLO store", "kind": "facility", "source": "we_recorded"},
    )
    ours = op(da, "org_upsert", data={"slug": f"programme{suffix}", "name": "Programme team"})

    def contract(reference):
        return op(
            da,
            "contract_create",
            data={
                "commodity_slug": "ors-zinc",
                "supplier_id": supplier["id"],
                "item_id": item["id"],
                "buyer_of_record": "programme_org",
                "buyer_org_id": ours["id"],
                "source": "we_recorded",
                "reference": reference,
                "status": "placed",
                "quantity": "100",
                "quantity_unit": "carton",
                "delivery_supply_point_id": warehouse["id"],
            },
        )

    return {
        "item": item,
        "eha": eha,
        "warehouse": warehouse,
        "llo": llo,
        "contract": contract(f"PO-1{suffix}"),
        "other_contract": contract(f"PO-2{suffix}"),
    }


@pytest.fixture
def world(da):
    return _world(da)


@pytest.fixture
def issued(da, world):
    """A link for EHA covering ONE of its two contracts and the two stores."""
    return op(
        da,
        "update_link_issue",
        data={
            "org_id": world["eha"]["id"],
            "contract_ids": [world["contract"]["id"]],
            "supply_point_ids": [world["warehouse"]["id"], world["llo"]["id"]],
            "label": "EHA — CHC co-packs",
        },
    )


def _link(issued):
    return UpdateLink.objects.get(pk=issued["id"])


class TestAResetTakesItsLinksWithIt:
    """A seeder's --reset purges the programme and seeds it again.

    The link rows are not reached by purge's cascade: an update link only
    points at its contracts through a join table, so deleting the contracts
    left the link standing -- listed on /supply/links/ as a working link that
    covers nothing, and still accepted at its URL. The same for an alert
    watching the whole programme. A reset has to take both with it.
    """

    def test_purge_removes_the_programmes_links_and_alerts_and_no_one_elses(self, da, other_da, issued):
        from connect_labs.labs.synthetic.models import SyntheticOpportunity
        from connect_labs.supply_chain.alerts.models import AlertSubscription

        SyntheticOpportunity.objects.create(
            opportunity_id=PROGRAM, program_id=PROGRAM, labs_only=True, enabled=True, label="links", allowed_domains=[]
        )
        op(
            da,
            "alert_subscription_create",
            data={"check_kinds": ["stock_below_minimum"], "recipient_email": "a@x.org"},
        )
        theirs = _world(other_da, suffix="-other")
        op(
            other_da,
            "update_link_issue",
            data={"org_id": theirs["eha"]["id"], "contract_ids": [theirs["contract"]["id"]]},
        )

        da.purge()

        assert not UpdateLink.objects.filter(program_id=PROGRAM).exists()
        assert not AlertSubscription.objects.filter(program_id=PROGRAM).exists()
        assert UpdateLink.objects.filter(program_id=OTHER_PROGRAM).count() == 1


# ---- issuing ---------------------------------------------------------------


class TestIssuing:
    def test_the_raw_token_is_returned_once_and_only_a_keyed_hash_is_stored(self, issued):
        raw = issued["token"]
        assert len(raw) >= 40
        assert issued["url"].endswith(f"/supply/u/{raw}/")
        link = _link(issued)
        assert raw not in {link.token_hash, link.token_hint, link.label}
        assert link.token_hash == tokens.hash_token(raw)
        # A bare sha256 would let a leaked table test guesses offline.
        import hashlib

        assert link.token_hash != hashlib.sha256(raw.encode()).hexdigest()

    def test_the_list_never_carries_the_token(self, da, issued):
        listed = op(da, "update_link_list")
        assert [row["id"] for row in listed] == [issued["id"]]
        assert issued["token"] not in str(listed)
        assert listed[0]["state"] == "active"
        assert listed[0]["org_name"] == "EHA Clinics"

    def test_a_link_must_cover_something(self, da, world):
        with pytest.raises(ValueError, match="at least one"):
            op(da, "update_link_issue", data={"org_id": world["eha"]["id"]})

    def test_a_link_cannot_cover_another_programmes_rows(self, da, other_da, world):
        theirs = _world(other_da, suffix="-x")
        with pytest.raises(ValueError, match="contract"):
            op(
                da,
                "update_link_issue",
                data={"org_id": world["eha"]["id"], "contract_ids": [theirs["contract"]["id"]]},
            )
        with pytest.raises(ValueError, match="supply point"):
            op(
                da,
                "update_link_issue",
                data={"org_id": world["eha"]["id"], "supply_point_ids": [theirs["warehouse"]["id"]]},
            )

    def test_expiry_is_bounded(self, da, world):
        import jsonschema

        with pytest.raises(jsonschema.ValidationError):
            op(
                da,
                "update_link_issue",
                data={"org_id": world["eha"]["id"], "contract_ids": [world["contract"]["id"]], "expires_in_days": 400},
            )

    def test_revoke_is_scoped_to_the_programme(self, da, other_da, issued):
        with pytest.raises(ValueError, match="not found"):
            op(other_da, "update_link_revoke", link_id=issued["id"])
        assert _link(issued).revoked_at is None
        op(da, "update_link_revoke", link_id=issued["id"])
        assert _link(issued).revoked_at is not None
        assert op(da, "update_link_list")[0]["state"] == "revoked"


# ---- finding a link from its token --------------------------------------


class TestTheToken:
    def test_a_good_token_finds_its_link(self, issued):
        assert tokens.find_usable_link(issued["token"]).pk == issued["id"]

    def test_unknown_expired_and_revoked_all_find_nothing(self, da, issued):
        assert tokens.find_usable_link("not-a-token") is None
        assert tokens.find_usable_link("") is None
        assert tokens.find_usable_link("x" * 5000) is None

        UpdateLink.objects.filter(pk=issued["id"]).update(expires_at=timezone.now() - timedelta(seconds=1))
        assert tokens.find_usable_link(issued["token"]) is None

        UpdateLink.objects.filter(pk=issued["id"]).update(expires_at=timezone.now() + timedelta(days=1))
        op(da, "update_link_revoke", link_id=issued["id"])
        assert tokens.find_usable_link(issued["token"]) is None


# ---- what a link can do ---------------------------------------------------


class TestWritesGoThroughTheOrdinaryOperations:
    def test_confirming_the_order_moves_the_contract_status(self, issued, world):
        link = _link(issued)
        with patch.object(service, "call_operation", wraps=service.call_operation) as called:
            service.submit(link, "confirm_order", {"contract": Contract.objects.get(pk=world["contract"]["id"])})
        assert Contract.objects.get(pk=world["contract"]["id"]).status == "confirmed"
        assert called.call_args.args[0] == "contract_update"

    def test_confirming_an_order_does_not_change_who_recorded_it(self, issued, world):
        before = Contract.objects.get(pk=world["contract"]["id"])
        service.submit(_link(issued), "confirm_order", {"contract": before})
        after = Contract.objects.get(pk=world["contract"]["id"])
        assert (after.source, after.recorded_by_org_id) == (before.source, before.recorded_by_org_id)
        assert after.source == "we_recorded"

    def test_moving_a_dispatch_does_not_change_who_recorded_it(self, da, issued, world):
        recorded = op(
            da,
            "shipment_record",
            data={"contract_id": world["contract"]["id"], "status": "dispatched", "source": "we_recorded"},
        )
        before = Shipment.objects.get(pk=recorded["id"])
        service.submit(_link(issued), "update_shipment", {"shipment": before, "status": "in_transit"})
        after = Shipment.objects.get(pk=recorded["id"])
        assert after.status == "in_transit"
        assert (after.source, after.recorded_by_org_id) == ("we_recorded", before.recorded_by_org_id)

    def test_a_shipment_is_supplier_reported_by_the_links_organisation(self, issued, world):
        link = _link(issued)
        result = service.submit(
            link,
            "record_shipment",
            {
                "contract": Contract.objects.get(pk=world["contract"]["id"]),
                "reference": "AWB-778",
                "status": "dispatched",
                "dispatched_on": timezone.now().date(),
                "quantity": "40",
                "unit_basis": "pack",
                "batch": "B-12",
            },
        )
        shipment = Shipment.objects.get(pk=result["id"])
        assert shipment.source == "supplier_reported"
        assert shipment.recorded_by_org_id == world["eha"]["id"]
        assert shipment.lines.get().quantity_unit == "carton"

    def test_a_receipt_puts_stock_in_the_ledger_attributed_to_the_supplier(self, issued, world):
        link = _link(issued)
        result = service.submit(
            link,
            "record_receipt",
            {
                "contract": Contract.objects.get(pk=world["contract"]["id"]),
                "supply_point": _point(world["warehouse"]),
                "received_on": timezone.now().date(),
                "quantity_accepted": "38",
                "quantity_rejected": "2",
                "rejection_reason": "crushed",
                "unit_basis": "pack",
            },
        )
        receipt = Receipt.objects.get(pk=result["id"])
        assert (receipt.source, receipt.recorded_by_org_id) == ("supplier_reported", world["eha"]["id"])
        movement = Movement.objects.get(receipt=receipt)
        assert movement.to_supply_point_id == world["warehouse"]["id"]

    def test_a_stock_count_is_a_physical_count_reported_by_the_supplier(self, issued, world):
        link = _link(issued)
        result = service.submit(
            link,
            "record_stock_count",
            {
                "supply_point": _point(world["warehouse"]),
                "item": _item(world),
                "counted_on": timezone.now().date(),
                "quantity": "120",
                "unit_basis": "pack",
            },
        )
        count = StockCount.objects.get(pk=result["id"])
        assert (count.kind, count.source, count.recorded_by_org_id) == (
            "physical_count",
            "supplier_reported",
            world["eha"]["id"],
        )

    def test_a_release_is_a_transfer_between_two_points_in_scope(self, issued, world):
        link = _link(issued)
        result = service.submit(
            link,
            "record_release",
            {
                "from_supply_point": _point(world["warehouse"]),
                "to_supply_point": _point(world["llo"]),
                "item": _item(world),
                "occurred_on": timezone.now().date(),
                "quantity": "10",
                "unit_basis": "pack",
            },
        )
        movement = Movement.objects.get(pk=result["id"])
        assert movement.kind == "transfer"
        assert (movement.source, movement.recorded_by_org_id) == ("supplier_reported", world["eha"]["id"])

    def test_every_write_is_logged_against_the_link_and_audited(self, issued, world):
        link = _link(issued)
        with patch.object(service, "audit_record") as audited:
            service.submit(link, "confirm_order", {"contract": Contract.objects.get(pk=world["contract"]["id"])})
        submission = UpdateLinkSubmission.objects.get(link=link)
        assert (submission.operation, submission.result_id) == ("contract_update", world["contract"]["id"])
        assert audited.called
        metadata = audited.call_args.kwargs["metadata"]
        assert metadata["update_link_id"] == link.pk
        assert metadata["org_id"] == world["eha"]["id"]
        assert _link(issued).last_used_at is not None

    def test_confirming_a_payment_sets_the_payees_confirmation_date(self, da, issued, world):
        invoice = op(
            da,
            "invoice_record",
            data={"contract_id": world["contract"]["id"], "amount": "500.00", "source": "we_recorded"},
        )
        payment = op(
            da,
            "payment_record",
            data={"invoice_id": invoice["id"], "paid_on": "2026-09-01", "amount": "500.00", "source": "we_recorded"},
        )
        from connect_labs.supply_chain.models import Payment

        link = _link(issued)
        service.submit(
            link,
            "confirm_payment",
            {
                "payment": Payment.objects.get(pk=payment["id"]),
                "received_on": timezone.now().date(),
            },
        )
        assert Payment.objects.get(pk=payment["id"]).confirmed_by_payee_on == timezone.now().date()


def _point(row):
    from connect_labs.supply_chain.models import SupplyPoint

    return SupplyPoint.objects.get(pk=row["id"])


def _item(world):
    from connect_labs.supply_chain.models import Item

    return Item.objects.get(pk=world["item"]["id"])


class TestTheScopeHolds:
    """The service is the second line: the form's querysets are the first. A
    caller that reaches `submit` with a row the form would never have offered
    is still refused, and nothing is written."""

    def test_another_contract_of_the_same_supplier_is_refused(self, issued, world):
        link = _link(issued)
        other = Contract.objects.get(pk=world["other_contract"]["id"])
        with pytest.raises(service.OutOfScope):
            service.submit(link, "confirm_order", {"contract": other})
        assert Contract.objects.get(pk=other.pk).status == "placed"
        with pytest.raises(service.OutOfScope):
            service.submit(
                link,
                "record_shipment",
                {"contract": other, "status": "dispatched", "quantity": "1", "unit_basis": "pack"},
            )
        assert not Shipment.objects.exists()

    def test_another_programmes_contract_and_point_are_refused(self, other_da, issued, world):
        theirs = _world(other_da, suffix="-x")
        link = _link(issued)
        with pytest.raises(service.OutOfScope):
            service.submit(link, "confirm_order", {"contract": Contract.objects.get(pk=theirs["contract"]["id"])})
        with pytest.raises(service.OutOfScope):
            service.submit(
                link,
                "record_stock_count",
                {
                    "supply_point": _point(theirs["warehouse"]),
                    "item": _item(world),
                    "counted_on": timezone.now().date(),
                    "quantity": "1",
                    "unit_basis": "pack",
                },
            )
        assert not StockCount.objects.exists()

    def test_a_point_outside_the_link_is_refused_even_as_a_destination(self, da, issued, world):
        elsewhere = op(
            da,
            "supply_point_upsert",
            data={"slug": "elsewhere", "name": "Elsewhere", "kind": "facility", "source": "we_recorded"},
        )
        link = _link(issued)
        with pytest.raises(service.OutOfScope):
            service.submit(
                link,
                "record_release",
                {
                    "from_supply_point": _point(world["warehouse"]),
                    "to_supply_point": _point(elsewhere),
                    "item": _item(world),
                    "occurred_on": timezone.now().date(),
                    "quantity": "1",
                    "unit_basis": "pack",
                },
            )
        assert not Movement.objects.exists()

    def test_an_expired_or_revoked_link_writes_nothing(self, da, issued, world):
        link = _link(issued)
        UpdateLink.objects.filter(pk=link.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        with pytest.raises(service.OutOfScope):
            service.submit(
                _link(issued), "confirm_order", {"contract": Contract.objects.get(pk=world["contract"]["id"])}
            )
        UpdateLink.objects.filter(pk=link.pk).update(expires_at=timezone.now() + timedelta(days=1))
        op(da, "update_link_revoke", link_id=link.pk)
        with pytest.raises(service.OutOfScope):
            service.submit(
                _link(issued), "confirm_order", {"contract": Contract.objects.get(pk=world["contract"]["id"])}
            )
        assert Contract.objects.get(pk=world["contract"]["id"]).status == "placed"


# ---- the public page --------------------------------------------------------


def _url(token):
    return reverse("supply_chain:update_link_public", args=[token])


class TestThePublicPage:
    def test_it_needs_no_login_and_shows_only_what_is_in_scope(self, client, issued, world):
        response = client.get(_url(issued["token"]))
        assert response.status_code == 200
        body = response.content.decode()
        assert "EHA Clinics" in body
        assert "PO-1" in body
        assert "PO-2" not in body, "the page shows a contract outside the link's scope"
        assert response["X-Robots-Tag"] == "noindex, nofollow"
        assert response["Referrer-Policy"] == "same-origin"

    def test_unknown_expired_and_revoked_look_identical(self, client, da, issued):
        unknown = client.get(_url("definitely-not-a-token"))
        UpdateLink.objects.filter(pk=issued["id"]).update(expires_at=timezone.now() - timedelta(seconds=1))
        expired = client.get(_url(issued["token"]))
        UpdateLink.objects.filter(pk=issued["id"]).update(expires_at=timezone.now() + timedelta(days=1))
        op(da, "update_link_revoke", link_id=issued["id"])
        revoked = client.get(_url(issued["token"]))
        assert unknown.status_code == expired.status_code == revoked.status_code == 404
        assert unknown.content == expired.content == revoked.content

    def test_posting_an_action_writes_and_redirects_back(self, client, issued, world):
        response = client.post(
            _url(issued["token"]), {"action": "confirm_order", "confirm_order-contract": world["contract"]["id"]}
        )
        assert response.status_code == 302
        assert response["Location"] == _url(issued["token"]) + "?done=confirm_order"
        assert Contract.objects.get(pk=world["contract"]["id"]).status == "confirmed"

    def test_posting_another_contracts_id_is_refused_by_the_form(self, client, issued, world):
        response = client.post(
            _url(issued["token"]),
            {"action": "confirm_order", "confirm_order-contract": world["other_contract"]["id"]},
        )
        assert response.status_code == 200
        assert Contract.objects.get(pk=world["other_contract"]["id"]).status == "placed"

    def test_the_form_is_csrf_protected(self, issued, world):
        from django.test import Client

        strict = Client(enforce_csrf_checks=True)
        response = strict.post(
            _url(issued["token"]), {"action": "confirm_order", "confirm_order-contract": world["contract"]["id"]}
        )
        assert response.status_code == 403
        assert Contract.objects.get(pk=world["contract"]["id"]).status == "placed"

    def test_a_browser_can_submit_the_form_it_was_given(self, issued, world):
        """The page's own referrer policy must not break its own forms.

        Under `Referrer-Policy: no-referrer` a browser sends `Origin: null` on
        a form POST (Fetch spec, "serializing a request origin"), and Django's
        CSRF check refuses a null origin. The test client sends no Origin at
        all, so every other test here passed while every real submission on
        labs came back 403 -- found by filming the supplier using the link.
        This replays the POST with the Origin a browser would actually send.
        """
        import re

        from django.test import Client

        browser = Client(enforce_csrf_checks=True)
        page = browser.get(_url(issued["token"]))
        policy = page["Referrer-Policy"]
        assert re.search(
            r'<meta name="referrer" content="%s">' % re.escape(policy), page.content.decode()
        ), "the meta tag and the header disagree, and a browser obeys the stricter one"
        # What a browser serialises as the Origin of a same-origin POST.
        origin = "null" if policy == "no-referrer" else "http://testserver"
        token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page.content.decode()).group(1)
        response = browser.post(
            _url(issued["token"]),
            {
                "csrfmiddlewaretoken": token,
                "action": "confirm_order",
                "confirm_order-contract": world["contract"]["id"],
            },
            HTTP_ORIGIN=origin,
        )
        assert response.status_code == 302, "a supplier's own browser was refused by CSRF"
        assert Contract.objects.get(pk=world["contract"]["id"]).status == "confirmed"

    def test_the_token_never_leaves_for_another_site(self, client, issued):
        """No referrer to other origins: the URL is the credential."""
        response = client.get(_url(issued["token"]))
        assert response["Referrer-Policy"] in ("same-origin", "no-referrer")

    def test_repeated_bad_tokens_are_throttled(self, client):
        from django.core.cache import cache

        cache.clear()
        codes = [client.get(_url(f"guess-{n}")).status_code for n in range(40)]
        assert 429 in codes
        cache.clear()

    def test_rotating_a_forged_forwarded_address_does_not_escape_the_throttle(self, client):
        from django.core.cache import cache

        cache.clear()
        # The load balancer appends the address it saw; everything left of
        # that is whatever the caller sent.
        codes = [
            client.get(_url(f"guess-{n}"), HTTP_X_FORWARDED_FOR=f"10.0.0.{n}, 203.0.113.9").status_code
            for n in range(40)
        ]
        assert 429 in codes
        cache.clear()

    def test_the_token_never_reaches_the_audit_trail(self, client, issued, world):
        from connect_labs.audit_trail.models import AuditEvent

        client.post(
            _url(issued["token"]), {"action": "confirm_order", "confirm_order-contract": world["contract"]["id"]}
        )
        paths = list(AuditEvent.objects.values_list("path", flat=True))
        assert paths, "the write was not audited"
        assert not any(issued["token"] in path for path in paths)

    def test_the_token_never_reaches_analytics(self, client, issued):
        body = client.get(_url(issued["token"])).content.decode()
        assert "labs-analytics" not in body


class TestTheStaffScreens:
    @pytest.fixture
    def user(self, django_user_model):
        return django_user_model.objects.create(username="amina", email="amina@example.org")

    @pytest.fixture
    def scoped(self, client, user):
        client.force_login(user)
        session = client.session
        session["labs_oauth"] = {"access_token": "t", "expires_at": 9999999999}
        session.save()
        return client

    def test_issuing_shows_the_link_once(self, scoped, world):
        with (
            patch("connect_labs.supply_chain.form_views._access") as access,
            patch("connect_labs.supply_chain.form_views.has_program_context", return_value=True),
        ):
            access.return_value = SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)
            response = scoped.post(
                reverse("supply_chain:update_link_issue"),
                {
                    "org": world["eha"]["id"],
                    "contracts": [world["contract"]["id"]],
                    "supply_points": [world["warehouse"]["id"]],
                    "expires_in_days": 30,
                    "label": "EHA",
                },
            )
        assert response.status_code == 200
        body = response.content.decode()
        match = re.search(r"/supply/u/([A-Za-z0-9_\-]+)/", body)
        assert match, "the issued link is not shown"
        assert tokens.find_usable_link(match.group(1)) is not None
