"""As-of requests: any program page, as it stood at the end of a past day.

THIS REPOSITORY IS PUBLIC. Every id, name and figure below is invented.

History is built through `call_operation` dated with `seed_overrides` on a
synthetic program, the way test_history_rewind.py builds it, and the pages are
driven through the Django test client. The point of every "live afterwards"
assertion is design doc §3.5: the as-of page is rendered inside a transaction
that is always rolled back, so nothing it rewinds may survive the request.
"""

import datetime
import re
from unittest import mock

import pytest
from django.urls import reverse

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.history.as_of import end_of_day, parse_as_of
from connect_labs.supply_chain.history.context import seed_overrides
from connect_labs.supply_chain.history.models import Revision
from connect_labs.supply_chain.models import Contract, Tender
from connect_labs.supply_chain.operations import call_operation

PROGRAM = 20996


def _at(month, day=1):
    return datetime.datetime(2026, month, day, 9, 0, tzinfo=datetime.UTC)


T0, T1, T2 = _at(1), _at(2), _at(3)
BEFORE_T0 = "2025-12-20"
BETWEEN_T0_T1 = "2026-01-15"


@pytest.fixture
def registered_synthetic():
    from connect_labs.labs.synthetic.models import SyntheticOpportunity

    return SyntheticOpportunity.objects.create(
        opportunity_id=PROGRAM,
        program_id=PROGRAM,
        labs_only=True,
        enabled=True,
        label="history as-of tests",
        allowed_domains=["dimagi.com"],
    )


@pytest.fixture
def da(registered_synthetic):
    return SupplyDataAccess(program_id=PROGRAM, caller=SYSTEM)


def op(da, name, when, **payload):
    with seed_overrides(PROGRAM, channel="command", recorded_at=when):
        return call_operation(name, da, payload)


@pytest.fixture
def world(da):
    """At T0 a tender and an order referenced PO-OLD; at T1 the reference became PO-NEW."""
    op(
        da,
        "commodity_upsert",
        T0,
        data={"slug": "rutf", "name": "RUTF", "base_unit": "sachet", "pack_unit": "carton"},
    )
    supplier = op(da, "supplier_create", T0, data={"name": "Northwind Foods"})
    us = op(da, "org_upsert", T0, data={"slug": "asof-us", "name": "The program"})
    tender = op(
        da,
        "tender_create",
        T0,
        data={
            "label": "Tender Harmattan",
            "delivery_point": {"city": "Kano"},
            "response_deadline": "2026-03-31",
            "lines": [{"commodity_slug": "rutf", "quantity": "600", "quantity_unit": "carton"}],
        },
    )
    order = op(
        da,
        "contract_create",
        T0,
        data={
            "supplier_id": supplier["id"],
            "commodity_slug": "rutf",
            "buyer_of_record": "programme_org",
            "buyer_org_id": us["id"],
            "reference": "PO-OLD",
            "quantity": "600",
            "quantity_unit": "carton",
            "status": "placed",
            "source": "we_recorded",
        },
    )
    op(da, "contract_update", T1, contract_id=order["id"], data={"reference": "PO-NEW"})
    return {"tender": tender, "order": order}


class _ProgramContextMiddleware:
    """Stands in for LabsContextMiddleware, which the test settings leave out:
    hands a signed-in request PROGRAM as its validated labs context."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.labs_context = {"program_id": PROGRAM} if request.user.is_authenticated else {}
        return self.get_response(request)


@pytest.fixture
def client_in_program(client, django_user_model, monkeypatch, settings, da):
    """A signed-in caller whose labs context names PROGRAM.

    `_access` is stubbed in the modules that call it (see the `scoped` fixture
    in test_write_screens.py for why those, and why imported first): the
    caller is not a real member of the synthetic program. `api_views` too:
    `as_of.authorise` builds the pages' access from there before rewinding.
    """
    from connect_labs.supply_chain import api_views, form_views, views  # noqa: F401  -- bind before patching
    from connect_labs.supply_chain.procurement import views as procurement_views  # noqa: F401

    settings.MIDDLEWARE = [*settings.MIDDLEWARE, f"{__name__}._ProgramContextMiddleware"]
    for module in ("api_views", "form_views", "views", "procurement.views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}._access", lambda request: da)

    client.force_login(django_user_model.objects.create_user(username="sophie", password="x"))
    return client


BANNER = "— read-only."


class TestParsing:
    def test_a_date(self):
        assert parse_as_of("2026-08-20") == datetime.date(2026, 8, 20)

    def test_empty_is_no_as_of(self):
        assert parse_as_of("") is None
        assert parse_as_of(None) is None

    @pytest.mark.parametrize("value", ["garbage", "2026-13-01", "20260820", "2026-08-20T00:00"])
    def test_malformed_raises(self, value):
        with pytest.raises(ValueError):
            parse_as_of(value)

    def test_end_of_day_is_the_last_instant_of_that_day_in_server_time(self):
        from django.utils import timezone

        end = end_of_day(datetime.date(2026, 8, 20))
        assert timezone.is_aware(end)
        local = timezone.localtime(end)
        assert (local.date(), local.hour, local.minute, local.second) == (datetime.date(2026, 8, 20), 23, 59, 59)
        next_midnight = timezone.localtime(end + datetime.timedelta(microseconds=1))
        assert (next_midnight.date(), next_midnight.hour, next_midnight.minute) == (datetime.date(2026, 8, 21), 0, 0)


@pytest.mark.django_db
class TestAsOfPages:
    def test_order_page_shows_the_old_reference_and_the_database_keeps_the_new(self, client_in_program, world):
        order_id = world["order"]["id"]
        revisions = Revision.objects.count()
        url = reverse("supply_chain:order_detail", args=[order_id])

        response = client_in_program.get(url, {"as_of": BETWEEN_T0_T1})

        assert response.status_code == 200
        body = response.content.decode()
        assert "PO-OLD" in body
        assert "PO-NEW" not in body
        # Rolled back: the live row and the revision log are exactly as before.
        assert Contract.objects.get(pk=order_id).reference == "PO-NEW"
        assert Revision.objects.count() == revisions

    def test_live_order_page_shows_the_new_reference(self, client_in_program, world):
        response = client_in_program.get(reverse("supply_chain:order_detail", args=[world["order"]["id"]]))
        body = response.content.decode()
        assert "PO-NEW" in body
        assert BANNER not in body

    def test_home_before_the_tender_existed_does_not_list_it(self, client_in_program, world):
        live = client_in_program.get(reverse("supply_chain:home")).content.decode()
        assert "Tender Harmattan" in live

        past = client_in_program.get(reverse("supply_chain:home"), {"as_of": BEFORE_T0})
        assert past.status_code == 200
        assert "Tender Harmattan" not in past.content.decode()
        assert Tender.objects.filter(pk=world["tender"]["id"]).exists()

    def test_banner_is_shown_under_as_of_and_write_controls_are_hidden(self, client_in_program, world):
        url = reverse("supply_chain:order_detail", args=[world["order"]["id"]])
        live = client_in_program.get(url).content.decode()
        past = client_in_program.get(url, {"as_of": BETWEEN_T0_T1}).content.decode()

        assert "Viewing as of 15 Jan 2026 — read-only." in past
        assert "Back to today" in past
        assert BANNER not in live
        edit = reverse("supply_chain:contract_edit", args=[world["order"]["id"]])
        assert edit in live
        assert edit not in past

    def test_the_date_control_is_on_every_wrapped_page(self, client_in_program, world):
        body = client_in_program.get(reverse("supply_chain:home")).content.decode()
        assert re.search(r'<input[^>]*name="as_of"', body)

    def test_the_date_control_speaks_the_pages_date_format(self, client_in_program, world):
        """A day typed as "15 Jan 2026" works like 2026-01-15, and the field shows it that way."""
        from connect_labs.supply_chain.history.as_of import parse_as_of

        assert parse_as_of("15 Jan 2026") == parse_as_of("2026-01-15")
        body = client_in_program.get(reverse("supply_chain:home") + "?as_of=15 Jan 2026").content.decode()
        assert "Viewing as of 15 Jan 2026" in body
        assert 'value="15 Jan 2026"' in body
        assert "mm/dd/yyyy" not in body

    def test_a_write_under_as_of_is_refused_and_changes_nothing(self, client_in_program, world):
        tender_id = world["tender"]["id"]
        status = Tender.objects.get(pk=tender_id).status
        revisions = Revision.objects.count()
        url = reverse("supply_chain:procurement_tender_close", args=[tender_id])

        response = client_in_program.post(f"{url}?as_of={BETWEEN_T0_T1}")

        assert response.status_code == 405
        assert "Viewing a past date is read-only" in response.content.decode()
        assert Tender.objects.get(pk=tender_id).status == status
        assert Revision.objects.count() == revisions

    @pytest.mark.parametrize("value", ["garbage", "2026-02-30"])
    def test_a_malformed_date_is_a_400(self, client_in_program, world, value):
        response = client_in_program.get(reverse("supply_chain:home"), {"as_of": value})
        assert response.status_code == 400

    def test_a_future_date_is_a_400(self, client_in_program, world):
        from django.utils import timezone

        tomorrow = timezone.localdate() + datetime.timedelta(days=1)
        response = client_in_program.get(reverse("supply_chain:home"), {"as_of": tomorrow.isoformat()})
        assert response.status_code == 400

    def test_today_is_allowed(self, client_in_program, world):
        from django.utils import timezone

        response = client_in_program.get(reverse("supply_chain:home"), {"as_of": timezone.localdate().isoformat()})
        assert response.status_code == 200
        assert "Tender Harmattan" in response.content.decode()

    def test_an_error_inside_the_view_still_rolls_back(self, client_in_program, world, monkeypatch):
        """set_rollback sits in a finally: a view that raises must not leave its rewind behind."""
        from connect_labs.supply_chain import views

        def boom(self, **kwargs):
            raise RuntimeError("view failed")

        monkeypatch.setattr(views.OrderDetailView, "get_context_data", boom)
        client_in_program.raise_request_exception = True
        with pytest.raises(RuntimeError):
            client_in_program.get(
                reverse("supply_chain:order_detail", args=[world["order"]["id"]]), {"as_of": BETWEEN_T0_T1}
            )
        assert Contract.objects.get(pk=world["order"]["id"]).reference == "PO-NEW"


@pytest.mark.django_db
class TestTheDecoratorDirectly:
    @pytest.fixture(autouse=True)
    def _signed_in(self, django_user_model, monkeypatch):
        """A signed-in caller whom `authorise` lets through (not a real member of PROGRAM)."""
        self.user = django_user_model.objects.create_user(username="sophie-direct", password="x")
        monkeypatch.setattr("connect_labs.supply_chain.history.as_of.authorise", lambda request: None)

    def _request(self, as_of, program_id=PROGRAM, user=None):
        from django.contrib.sessions.backends.db import SessionStore
        from django.test import RequestFactory

        request = RequestFactory().get("/supply/", {"as_of": as_of})
        request.labs_context = {"program_id": program_id}
        request.user, request.session = user or self.user, SessionStore()  # the context processors read both
        return request

    def test_a_lazy_template_response_is_rendered_before_the_rollback(self, world):
        """A TemplateResponse left to Django renders after the view returns --
        after the rollback, against live rows. The shipped pages mostly hand
        templates plain dicts, so only a lazy queryset shows the difference."""
        from django.template import engines
        from django.template.response import TemplateResponse

        from connect_labs.supply_chain.history.as_of import as_of_view

        template = engines["django"].from_string("{% for t in tenders %}[{{ t.label }}]{% endfor %}")

        @as_of_view
        def view(request):
            return TemplateResponse(request, template, {"tenders": Tender.objects.filter(program_id=PROGRAM)})

        response = view(self._request(BEFORE_T0))
        response.render()  # a no-op once rendered; what Django's handler would do
        assert "[Tender Harmattan]" not in response.content.decode()
        assert Tender.objects.filter(pk=world["tender"]["id"]).exists()

    def test_a_streaming_response_is_refused_under_as_of(self, world):
        """A stream's body runs after the view returns -- after the rollback --
        so it would read live rows under a past date's banner. Refused instead."""
        from django.http import StreamingHttpResponse

        from connect_labs.supply_chain.history.as_of import NO_PAST_DOWNLOAD, as_of_view

        revisions = Revision.objects.count()
        order_id = world["order"]["id"]

        @as_of_view
        def view(request):
            return StreamingHttpResponse(
                f"[{c.reference}]" for c in Contract.objects.filter(pk=order_id)  # read lazily, at iteration
            )

        response = view(self._request(BETWEEN_T0_T1))

        assert response.status_code == 400
        assert not getattr(response, "streaming", False)
        body = response.content.decode()
        assert NO_PAST_DOWNLOAD in body
        assert "PO-OLD" not in body and "PO-NEW" not in body
        assert Contract.objects.get(pk=order_id).reference == "PO-NEW"
        assert Revision.objects.count() == revisions

    def test_a_streaming_response_is_served_live_without_as_of(self):
        from django.http import StreamingHttpResponse
        from django.test import RequestFactory

        from connect_labs.supply_chain.history.as_of import as_of_view

        @as_of_view
        def view(request):
            return StreamingHttpResponse(iter(["live"]))

        response = view(RequestFactory().get("/supply/"))
        assert response.streaming and b"".join(response.streaming_content) == b"live"

    def test_supply_as_of_is_set_on_every_wrapped_request(self):
        from django.http import HttpResponse
        from django.test import RequestFactory

        from connect_labs.supply_chain.history.as_of import as_of_view

        seen = []

        @as_of_view
        def view(request):
            seen.append(request.supply_as_of)
            return HttpResponse("ok")

        view(RequestFactory().get("/supply/"))
        no_program = RequestFactory().get("/supply/", {"as_of": BETWEEN_T0_T1})
        no_program.labs_context = {}
        with mock.patch("connect_labs.supply_chain.history.as_of.rewind") as rewind:
            view(no_program)
        assert seen == [None, datetime.date(2026, 1, 15)]
        assert rewind.call_count == 0  # no program: nothing to rewind, the view says "choose one"


@pytest.mark.django_db
class TestTheRealPermissionPath:
    def test_an_as_of_read_of_a_program_the_caller_cannot_use_is_refused_and_changes_nothing(
        self, client, django_user_model, settings, monkeypatch, world
    ):
        """Nothing stubbed but the labs context: the request names PROGRAM, and
        the caller is no member of it. The refusal comes BEFORE any rewind --
        a rewind is writes and row locks, not something an outsider may cause
        -- and it is the same 403 the page gives without as_of."""
        # No session org list and no token: `holdings` would ask Connect. Answer
        # "belongs to nothing" rather than touching the network.
        monkeypatch.setattr(
            "connect_labs.labs.access.scopes.fetch_user_organization_data", lambda token, owner=None: {}
        )
        settings.MIDDLEWARE = [*settings.MIDDLEWARE, f"{__name__}._ProgramContextMiddleware"]
        client.force_login(django_user_model.objects.create_user(username="outsider", password="x"))
        order_id = world["order"]["id"]
        url = reverse("supply_chain:order_detail", args=[order_id])
        revisions = Revision.objects.count()

        live = client.get(url)
        with mock.patch("connect_labs.supply_chain.history.as_of.rewind") as rewind:
            past = client.get(url, {"as_of": BETWEEN_T0_T1})

        assert rewind.call_count == 0
        assert live.status_code == 403
        assert past.status_code == live.status_code
        assert "PO-OLD" not in past.content.decode()
        assert Contract.objects.get(pk=order_id).reference == "PO-NEW"
        assert Revision.objects.count() == revisions


@pytest.mark.django_db
class TestNothingRewindsForTheWrongCaller:
    def test_an_anonymous_as_of_request_never_rewinds(self, client, world):
        url = reverse("supply_chain:order_detail", args=[world["order"]["id"]])
        with mock.patch("connect_labs.supply_chain.history.as_of.rewind") as rewind:
            response = client.get(url, {"as_of": BETWEEN_T0_T1})
        assert rewind.call_count == 0
        assert response.status_code == 302  # the page's own login redirect

    def test_an_anonymous_request_through_the_decorator_runs_live_with_no_past_date(self, world):
        from django.contrib.auth.models import AnonymousUser
        from django.http import HttpResponse
        from django.test import RequestFactory

        from connect_labs.supply_chain.history.as_of import as_of_view

        seen = []

        @as_of_view
        def view(request):
            seen.append((request.supply_as_of, Contract.objects.get(pk=world["order"]["id"]).reference))
            return HttpResponse("ok")

        request = RequestFactory().get("/supply/", {"as_of": BETWEEN_T0_T1})
        request.labs_context, request.user = {"program_id": PROGRAM}, AnonymousUser()
        with mock.patch("connect_labs.supply_chain.history.as_of.rewind") as rewind:
            view(request)
        assert rewind.call_count == 0
        assert seen == [(None, "PO-NEW")]


@pytest.mark.django_db
class TestTheRewindIsBounded:
    def test_the_block_runs_under_a_lock_and_statement_timeout_that_do_not_outlive_it(
        self, client_in_program, world, monkeypatch
    ):
        from django.db import connection

        from connect_labs.supply_chain import views

        seen = {}
        real = views.OrderDetailView.get_context_data

        def spy(self, **kwargs):
            with connection.cursor() as cursor:
                cursor.execute("SHOW lock_timeout")
                seen["lock"] = cursor.fetchone()[0]
                cursor.execute("SHOW statement_timeout")
                seen["statement"] = cursor.fetchone()[0]
            return real(self, **kwargs)

        monkeypatch.setattr(views.OrderDetailView, "get_context_data", spy)
        client_in_program.get(
            reverse("supply_chain:order_detail", args=[world["order"]["id"]]), {"as_of": BETWEEN_T0_T1}
        )
        assert seen == {"lock": "2s", "statement": "15s"}
        with connection.cursor() as cursor:
            cursor.execute("SHOW lock_timeout")
            assert cursor.fetchone()[0] != "2s"

    @pytest.mark.parametrize("code", ["55P03", "57014"])
    def test_a_lock_or_statement_timeout_is_a_503_busy(self, client_in_program, world, code):
        from django.db import OperationalError

        from connect_labs.supply_chain.history.as_of import BUSY

        class _Cause(Exception):
            sqlstate = code

        def times_out(program_id, until):
            error = OperationalError("canceling statement due to lock timeout")
            error.__cause__ = _Cause()
            raise error

        url = reverse("supply_chain:order_detail", args=[world["order"]["id"]])
        with mock.patch("connect_labs.supply_chain.history.as_of.rewind", side_effect=times_out):
            response = client_in_program.get(url, {"as_of": BETWEEN_T0_T1})
        assert response.status_code == 503
        assert BUSY in response.content.decode()
        assert Contract.objects.get(pk=world["order"]["id"]).reference == "PO-NEW"

    def test_any_other_operational_error_still_raises(self, client_in_program, world):
        from django.db import OperationalError

        client_in_program.raise_request_exception = True
        url = reverse("supply_chain:order_detail", args=[world["order"]["id"]])
        with mock.patch(
            "connect_labs.supply_chain.history.as_of.rewind", side_effect=OperationalError("server closed")
        ):
            with pytest.raises(OperationalError):
                client_in_program.get(url, {"as_of": BETWEEN_T0_T1})


def _strip_tokens(html):
    """CSRF tokens are masked afresh on every response; nothing else may differ."""
    html = re.sub(r'name="csrfmiddlewaretoken" value="[^"]*"', "", html)
    return re.sub(r'"X-CSRFToken": "[^"]*"', "", html)


@pytest.mark.django_db
class TestUnwrappedRoutes:
    def test_market_ignores_as_of(self, client_in_program, world):
        with mock.patch("connect_labs.supply_chain.history.as_of.rewind") as rewind:
            live = client_in_program.get(reverse("supply_chain:market"))
            past = client_in_program.get(reverse("supply_chain:market"), {"as_of": BETWEEN_T0_T1})
        assert rewind.call_count == 0
        assert live.status_code == past.status_code == 200
        assert _strip_tokens(past.content.decode()) == _strip_tokens(live.content.decode())

    def test_which_routes_are_wrapped(self):
        from connect_labs.supply_chain.urls import urlpatterns

        by_name = {p.name: p for p in urlpatterns}
        wrapped = {name for name, p in by_name.items() if getattr(p.callback, "supply_as_of_wrapped", False)}
        for name in ("home", "order_detail", "procurement_tender_detail", "procurement_comparison", "stock"):
            assert name in wrapped, name
        for name in ("market", "market_tender", "market_bid", "update_link_public", "api_operation"):
            assert name in by_name and name not in wrapped, name

    def test_every_route_named_as_reading_what_happened_exists(self):
        from connect_labs.supply_chain.urls import _HAPPENED_ROUTES, urlpatterns

        assert _HAPPENED_ROUTES <= {str(p.pattern) for p in urlpatterns}
        assert "stock/dispensing/" not in _HAPPENED_ROUTES  # rules are records, and rewind


@pytest.fixture
def ledger(da, world):
    """A store with 100 sachets from 1 Jan, then two consumptions recorded on 1 Mar:
    30 that happened on 10 Jan (a late sync) and 20 that happened on 10 Feb."""
    store = op(
        da,
        "supply_point_upsert",
        T0,
        data={"slug": "asof-store", "name": "Asof store", "kind": "central_store", "source": "we_recorded"},
    )
    lines = (
        (T0, "adjustment", "2026-01-01", "to_supply_point_id", "100"),
        (T2, "consumption", "2026-01-10", "from_supply_point_id", "30"),
        (T2, "consumption", "2026-02-10", "from_supply_point_id", "20"),
    )
    for when, kind, occurred_on, side, quantity in lines:
        op(
            da,
            "movement_record",
            when,
            data={
                "kind": kind,
                "occurred_on": occurred_on,
                side: store["id"],
                "commodity_slug": "rutf",
                "quantity": quantity,
                "quantity_unit": "sachet",
                "source": "we_recorded",
            },
        )
    return store


@pytest.mark.django_db
class TestStockPagesCountWhatHappened:
    """#2343: a stock page on a past day shows what had HAPPENED by then, not what
    had been recorded -- a visit made before the day but synced after it counts."""

    @staticmethod
    def _on_hand(response, store):
        point = next(p for p in response.context["network"]["points"] if p["supply_point_id"] == store["id"])
        return float(point["on_hand"]["amount"])

    def test_stock_counts_a_movement_that_happened_before_the_day_but_was_recorded_after(
        self, client_in_program, ledger
    ):
        with mock.patch("connect_labs.supply_chain.history.as_of.rewind") as rewind:
            past = client_in_program.get(reverse("supply_chain:stock"), {"as_of": BETWEEN_T0_T1})
        assert past.status_code == 200
        assert rewind.call_count == 0  # read live, dated by when things happened
        assert self._on_hand(past, ledger) == 70  # 100 less the 10 Jan visit, not the 10 Feb one
        assert self._on_hand(client_in_program.get(reverse("supply_chain:stock")), ledger) == 50

    def test_movements_lists_only_what_had_happened_by_the_day(self, client_in_program, ledger):
        past = client_in_program.get(
            reverse("supply_chain:movements"), {"as_of": BETWEEN_T0_T1, "supply_point_id": ledger["id"]}
        )
        assert sorted(m["occurred_on"] for m in past.context["movements"]) == ["2026-01-01", "2026-01-10"]

    def test_each_page_says_which_past_it_shows(self, client_in_program, ledger, world):
        stock = client_in_program.get(reverse("supply_chain:stock"), {"as_of": BETWEEN_T0_T1})
        order = client_in_program.get(
            reverse("supply_chain:order_detail", args=[world["order"]["id"]]), {"as_of": BETWEEN_T0_T1}
        )
        assert "Stock is what had happened by that day" in stock.content.decode()
        assert "Records as they stood that evening" in order.content.decode()

    def test_a_write_to_a_stock_page_under_as_of_is_still_refused(self, client_in_program, ledger):
        response = client_in_program.post(f"{reverse('supply_chain:stock')}?as_of={BETWEEN_T0_T1}")
        assert response.status_code == 405
