"""The last two screens: a resupply run, and correcting a quote.

THIS REPOSITORY IS PUBLIC. Every supplier, worker and figure here is invented.

Two rules carry this file:

  * a distribution emits one movement per line, so a worker's stock on hand is
    a ledger balance and not a parallel figure — if a run wrote no movements it
    would look like it had worked while every balance stayed wrong;
  * correcting a quote writes a NEW VERSION. The original has to stay readable,
    because a comparison run last month must still reproduce the numbers it
    showed then.
"""

import re
from decimal import Decimal

import pytest
from django.urls import reverse

from connect_labs.supply_chain.models import Commodity, Distribution, Movement, Quote, Supplier, SupplyPoint, Tender

pytestmark = pytest.mark.django_db

PROGRAM = 10506
SCOPE = f"prog:{PROGRAM}"
OPPORTUNITY = 4242


@pytest.fixture
def user(client, django_user_model):
    account = django_user_model.objects.create_user(username="katherine", password="x", email="k@dimagi.com")
    client.force_login(account)
    return account


@pytest.fixture
def scoped(client, user, monkeypatch):
    from connect_labs.supply_chain import form_views, views  # noqa: F401  -- bind before patching
    from connect_labs.supply_chain.api_views import _access as real_access
    from connect_labs.supply_chain.distribution import views as distribution_views  # noqa: F401
    from connect_labs.supply_chain.procurement import views as procurement_views  # noqa: F401

    def _scoped(request):
        access = real_access(request)
        access.program_id = PROGRAM
        return access

    for module in ("form_views", "views", "procurement.views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}.has_program_context", lambda request: True)
    for module in ("form_views", "views", "procurement.views", "distribution.views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}._access", _scoped)
    return client


@pytest.fixture
def rutf():
    return Commodity.objects.create(scope_key=SCOPE, slug="rutf", name="RUTF", base_unit="sachet", pack_unit="carton")


@pytest.fixture
def store():
    return SupplyPoint.objects.create(
        program_id=PROGRAM, slug="central-store", name="Central store", kind="central_store", source="we_recorded"
    )


@pytest.fixture
def worker():
    return SupplyPoint.objects.create(
        program_id=PROGRAM,
        opportunity_id=OPPORTUNITY,
        slug="worker-01",
        name="A field worker",
        kind="user_held",
        connect_username="worker-01",
        source="we_recorded",
    )


@pytest.fixture
def a_tender():
    return Tender.objects.create(
        program_id=PROGRAM,
        label="Tender 1",
        status="open",
        lines=[{"commodity_slug": "rutf", "quantity": "500", "quantity_unit": "carton"}],
        delivery_point={"name": "Central store"},
    )


@pytest.fixture
def quote(a_tender, rutf):
    return Quote.objects.create(
        tender=a_tender,
        supplier=Supplier.objects.enrol(scope_key=SCOPE, name="Northwind Foods"),
        commodity=rutf,
        as_quoted_amount=Decimal("52.42"),
        as_quoted_unit="per_pack",
        as_quoted_currency="USD",
        quantity_basis=Decimal("500"),
        quantity_basis_unit="carton",
    )


def run_post(store, rutf, worker, **overrides):
    payload = {
        "supply_point": store.pk,
        "commodity": rutf.pk,
        "opportunity_id": str(OPPORTUNITY),
        "distributed_on": "2026-05-04",
        "reference": "RUN-1",
        "source": "partner_reported",
        "lines-TOTAL_FORMS": "1",
        "lines-INITIAL_FORMS": "0",
        "lines-MIN_NUM_FORMS": "1",
        "lines-MAX_NUM_FORMS": "1000",
        "lines-0-to_supply_point": worker.pk,
        "lines-0-item": "",
        "lines-0-batch": "",
        "lines-0-quantity": "12",
        "lines-0-quantity_unit": "carton",
    }
    payload.update(overrides)
    return payload


def correction_post(**overrides):
    payload = {
        "as_quoted_amount": "52.42",
        "as_quoted_unit": "per_pack",
        "as_quoted_currency": "usd",
        "quantity_basis": "500",
        "quantity_basis_unit": "carton",
        "pack_spec_source": "stated_on_quote",
        "base_per_pack_stated": "150",
        "base_unit_grams_stated": "92",
        "freight_basis": "not_specified",
        "freight_amount": "",
        "duties_basis": "not_specified",
        "duties_amount": "",
        "shelf_life_months_stated": "",
        "lead_time_days": "",
        "incoterm": "",
        "received_on": "",
        "reason": "transcribed the per-carton price as per-sachet",
    }
    payload.update(overrides)
    return payload


class TestRecordingAResupplyRun:
    def test_a_run_posts_one_movement_per_line(self, scoped, store, rutf, worker):
        """The whole point of a worker being a supply point: no special case in
        the ledger, and no parallel arithmetic for a worker's balance."""
        response = scoped.post(reverse("supply_chain:distribution_record"), run_post(store, rutf, worker))
        assert response.status_code == 302

        made = Distribution.objects.get(reference="RUN-1")
        assert made.program_id == PROGRAM
        line = made.lines.get()
        assert line.to_supply_point_id == worker.pk
        assert line.quantity == Decimal("12")
        assert line.movement_id is not None, "a line with no movement moves no balance"

        movement = Movement.objects.get(pk=line.movement_id)
        assert movement.from_supply_point_id == store.pk
        assert movement.to_supply_point_id == worker.pk

    def test_a_run_with_no_lines_is_refused(self, scoped, store, rutf, worker):
        response = scoped.post(
            reverse("supply_chain:distribution_record"),
            run_post(store, rutf, worker, **{"lines-0-quantity": "", "lines-0-quantity_unit": ""}),
        )
        assert response.status_code == 200
        assert not Distribution.objects.filter(reference="RUN-1").exists()

    def test_the_source_picker_offers_stores_and_not_workers(self, scoped, store, rutf, worker):
        """A run goes OUT of a store. Offering a worker as the origin invites a
        worker-to-worker transfer, which is a movement, not a resupply run.

        Scoped to the `supply_point` select rather than searched for across the
        whole page: the worker IS on this page, as a line's destination, so a
        document-wide `not in` would pass whatever the origin picker offered.
        """
        body = scoped.get(reverse("supply_chain:distribution_record")).content.decode()
        origin = re.search(r'<select[^>]*name="supply_point".*?</select>', body, re.S).group(0)
        assert f'value="{store.pk}"' in origin
        assert f'value="{worker.pk}"' not in origin

        line = re.search(r'<select[^>]*name="lines-0-to_supply_point".*?</select>', body, re.S).group(0)
        assert f'value="{worker.pk}"' in line, "the worker belongs on the line, not as the origin"

    def test_a_programme_with_no_workers_says_so_rather_than_showing_an_empty_picker(self, scoped, store, rutf):
        body = scoped.get(reverse("supply_chain:distribution_record")).content.decode()
        assert "no field workers on file" in body
        assert reverse("supply_chain:network") in body, "and says where to fix it"

    def test_the_worker_picker_offers_only_this_programmes_workers(self, scoped, store, rutf, worker):
        SupplyPoint.objects.create(
            program_id=99999,
            slug="theirs",
            name="A worker in another programme",
            kind="user_held",
            connect_username="theirs",
            source="we_recorded",
        )
        body = scoped.get(reverse("supply_chain:distribution_record")).content.decode()
        assert "A field worker" in body
        assert "A worker in another programme" not in body


class TestCorrectingAQuote:
    def test_a_correction_writes_a_new_version_and_leaves_the_original(self, scoped, quote):
        response = scoped.post(
            reverse("supply_chain:procurement_quote_correct", args=[quote.pk]),
            correction_post(as_quoted_amount="52.42", as_quoted_unit="per_base_unit"),
        )
        assert response.status_code == 302

        quote.refresh_from_db()
        assert quote.superseded_by_id is not None, "the original stays, marked as superseded"
        assert quote.as_quoted_unit == "per_pack", "and is not itself edited"

        corrected = Quote.objects.get(pk=quote.superseded_by_id)
        assert corrected.as_quoted_unit == "per_base_unit"
        assert corrected.version == quote.version + 1

    def test_the_reason_is_kept(self, scoped, quote):
        scoped.post(
            reverse("supply_chain:procurement_quote_correct", args=[quote.pk]),
            correction_post(as_quoted_unit="per_base_unit"),
        )
        quote.refresh_from_db()
        corrected = Quote.objects.get(pk=quote.superseded_by_id)
        assert "per-carton price as per-sachet" in (corrected.correction_reason or quote.correction_reason)

    def test_a_correction_needs_a_reason(self, scoped, quote):
        """A version chain is only worth having if each link says why it exists."""
        response = scoped.post(
            reverse("supply_chain:procurement_quote_correct", args=[quote.pk]),
            correction_post(reason=""),
        )
        assert response.status_code == 200
        quote.refresh_from_db()
        assert quote.superseded_by_id is None

    def test_the_form_opens_on_the_existing_figures(self, scoped, quote):
        """A correction is usually one wrong number among fifteen right ones,
        and retyping the other fourteen is how a second mistake gets in."""
        body = scoped.get(reverse("supply_chain:procurement_quote_correct", args=[quote.pk])).content.decode()
        # Django renders a Decimal at the field's own precision, so this is
        # "52.4200" rather than "52.42" -- matched loosely on purpose, because
        # the assertion is "the existing figure is there", not "it is formatted
        # exactly this way".
        assert re.search(r'name="as_quoted_amount" value="52\.420*"', body)
        assert re.search(r'name="quantity_basis" value="500(\.0*)?"', body)

    def test_a_quote_from_another_programme_is_not_found(self, scoped, rutf):
        theirs = Tender.objects.create(program_id=99999, label="Theirs", lines=[], delivery_point={})
        their_quote = Quote.objects.create(
            tender=theirs,
            supplier=Supplier.objects.enrol(scope_key="prog:99999", name="Theirs"),
            commodity=rutf,
        )
        assert scoped.get(reverse("supply_chain:procurement_quote_correct", args=[their_quote.pk])).status_code == 404


class TestTheWidgetsDoNotRefuseRealFigures:
    def test_a_money_input_allows_four_decimal_places(self, scoped, quote):
        """Money is stored to four places, because a per-sachet price is
        routinely something like 0.3495 — and a browser enforcing a
        two-decimal step refuses exactly that figure, in the one domain built
        around not losing it. Precision is the model's and the schema's job.
        """
        body = scoped.get(reverse("supply_chain:procurement_quote_correct", args=[quote.pk])).content.decode()
        field = re.search(r'<input[^>]*id="id_as_quoted_amount"[^>]*>', body).group(0)
        assert 'step="any"' in field
        assert 'step="0.01"' not in field


class TestTheScreensAreReachable:
    def test_the_distribution_page_offers_recording_a_run(self, scoped):
        body = scoped.get(reverse("supply_chain:distribution")).content.decode()
        assert reverse("supply_chain:distribution_record") in body

    def test_a_standing_quote_offers_correcting_it(self, scoped, quote):
        body = scoped.get(reverse("supply_chain:procurement_quote_detail", args=[quote.pk])).content.decode()
        assert reverse("supply_chain:procurement_quote_correct", args=[quote.pk]) in body

    def test_a_superseded_quote_does_not(self, scoped, quote, a_tender, rutf):
        """Correcting a version that has already been corrected forks the
        chain, and then two versions both claim to be current."""
        newer = Quote.objects.create(tender=a_tender, supplier=quote.supplier, commodity=rutf, version=2)
        quote.superseded_by = newer
        quote.save()

        body = scoped.get(reverse("supply_chain:procurement_quote_detail", args=[quote.pk])).content.decode()
        assert reverse("supply_chain:procurement_quote_correct", args=[quote.pk]) not in body

    def test_a_voided_quote_offers_neither(self, scoped, quote):
        quote.voided = True
        quote.save()
        body = scoped.get(reverse("supply_chain:procurement_quote_detail", args=[quote.pk])).content.decode()
        assert reverse("supply_chain:procurement_quote_correct", args=[quote.pk]) not in body
        assert reverse("supply_chain:procurement_quote_void", args=[quote.pk]) not in body


class TestEveryWriteOperationHasAScreen:
    """The goal this whole run of work was for.

    A write operation with no screen is one somebody has to use curl for, and
    that was the state of twenty-nine of them at the start. This asserts the
    list is empty rather than trusting a count in a commit message — and it
    fails loudly when a new write operation is added without one, which is the
    moment to decide whether it needs one rather than a year later.
    """

    # Reached through the sourcing screens' own flow rather than a dedicated
    # form: recording a quote and awarding a tender are `quote_entry.html` and
    # the comparison page, which predate this work.
    ALREADY_HAD_SCREENS = {"quote_record", "award_create"}

    def test_no_write_operation_is_left_without_one(self):
        from django.urls import get_resolver

        from connect_labs.supply_chain.operations import all_operations

        writes = {name for name, op in all_operations().items() if op.is_write and not op.internal}

        # Every view class the supply URLconf points at, and the operation each
        # one drives. Read off the resolver rather than listed here, so a screen
        # that is added without a route does not count as covering anything.
        covered = set()
        for pattern in get_resolver().url_patterns:
            for entry in getattr(pattern, "url_patterns", []):
                callback = getattr(entry, "callback", None)
                view_class = getattr(callback, "view_class", None)
                operation = getattr(view_class, "operation", "")
                if operation:
                    covered.add(operation)

        missing = writes - covered - self.ALREADY_HAD_SCREENS
        assert not missing, f"write operations with no screen: {sorted(missing)}"
