"""Access control, the pages at 200 (service mocked), and POST actions redirecting with messages."""

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse

from connect_labs.connect_web import service, views
from connect_labs.connect_web.errors import LEARNING_INCOMPLETE, ConnectAPIError, GuardRefused
from connect_labs.connect_web.models import WorkerSession

from . import fakes

pytestmark = pytest.mark.django_db

OPP = fakes.OPP_UUID


def _detail(claimed=False, can_claim=False):
    opp = fakes.opportunity(claim={"max_payments": 20, "date_claimed": "2026-11-05"} if claimed else None)
    learn = service.learn_status(opp, {"completed_modules": [{"module": 1}], "assessments": []})
    delivery = fakes.FakeServers().delivery_progress if claimed else None
    return {
        "opp": opp,
        **service.summary_of(opp),
        "claimed": claimed,
        "learn": learn,
        "can_claim": can_claim,
        "delivery": delivery,
        "deliveries": (delivery or {}).get("deliveries") or [],
        "payments": service._payments(delivery or {}),
    }


@pytest.fixture
def mocked(monkeypatch):
    """The service's Connect-facing calls, replaced: these tests are about the pages."""
    calls = []
    monkeypatch.setattr(
        service,
        "list_opportunities",
        lambda s: [{"opp": fakes.opportunity(), **service.summary_of(fakes.opportunity())}],
    )
    monkeypatch.setattr(service, "opportunity_detail", lambda s, oid: _detail())
    monkeypatch.setattr(service, "get_opportunity", lambda s, oid: fakes.opportunity())
    monkeypatch.setattr(service, "start_learning", lambda s, oid: calls.append(("start", oid)))
    monkeypatch.setattr(service, "claim", lambda s, oid: calls.append(("claim", oid)) or True)
    monkeypatch.setattr(service, "confirm_payment", lambda s, pid, ok, **kw: calls.append(("confirm", pid, ok)))
    return calls


@pytest.fixture
def acting(client, staff, pool_worker):
    client.force_login(staff)
    service.check_out(staff, pool_worker)
    return client


def _messages(response):
    return [str(m) for m in get_messages(response.wsgi_request)]


# -- access ---------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["connect_web:home", "connect_web:workers", "connect_web:opportunities"])
def test_anonymous_is_sent_to_the_labs_login(client, name):
    response = client.get(reverse(name))
    assert response.status_code == 302 and response["Location"].startswith("/labs/login/")


def test_non_staff_is_refused(client, outsider):
    client.force_login(outsider)
    assert client.get(reverse("connect_web:workers")).status_code == 403
    assert client.post(reverse("connect_web:workers"), {"pool_worker": 1}).status_code == 403


def test_django_is_staff_is_allowed(client, outsider):
    outsider.is_staff = True
    outsider.save()
    client.force_login(outsider)
    assert client.get(reverse("connect_web:workers")).status_code == 200


def test_allow_listed_email_is_allowed(client, outsider, settings):
    settings.CONNECT_WEB_ALLOWED_EMAILS = ["Outsider@Example.com"]
    client.force_login(outsider)
    assert client.get(reverse("connect_web:workers")).status_code == 200


# -- picker ---------------------------------------------------------------------------------


def test_picker_lists_workers_and_who_holds_them(client, staff, other_staff, pool_worker):
    service.check_out(other_staff, pool_worker)
    client.force_login(staff)
    response = client.get(reverse("connect_web:workers"))
    assert response.status_code == 200
    body = response.content.decode()
    assert "Test Worker 1" in body and "Checked out by Col League" in body and "In use" in body
    assert "123456" not in body  # the backup code never reaches a page


def test_check_out_from_the_picker(client, staff, pool_worker):
    client.force_login(staff)
    response = client.post(reverse("connect_web:workers"), {"pool_worker": pool_worker.pk})
    assert response.status_code == 302 and response["Location"] == reverse("connect_web:opportunities")
    assert service.current_session(staff).pool_worker == pool_worker


def test_check_out_someone_elses_worker_shows_why(client, staff, other_staff, pool_worker):
    service.check_out(other_staff, pool_worker)
    client.force_login(staff)
    response = client.post(reverse("connect_web:workers"), {"pool_worker": pool_worker.pk})
    assert response["Location"] == reverse("connect_web:workers")
    assert any("checked out by Col League" in m for m in _messages(response))


def test_home_routes_by_session(client, staff, pool_worker):
    client.force_login(staff)
    assert client.get(reverse("connect_web:home"))["Location"] == reverse("connect_web:workers")
    service.check_out(staff, pool_worker)
    assert client.get(reverse("connect_web:home"))["Location"] == reverse("connect_web:opportunities")


def test_release(acting, staff):
    response = acting.post(reverse("connect_web:release"))
    assert response.status_code == 302 and service.current_session(staff) is None


def test_post_without_csrf_token_is_refused(staff, pool_worker):
    from django.test import Client

    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(staff)
    assert csrf_client.post(reverse("connect_web:workers"), {"pool_worker": pool_worker.pk}).status_code == 403


# -- pages ----------------------------------------------------------------------------------


def test_pages_need_a_checked_out_worker(client, staff, mocked):
    client.force_login(staff)
    response = client.get(reverse("connect_web:opportunities"))
    assert response["Location"] == reverse("connect_web:workers")


def test_opportunity_list_renders(acting, mocked):
    response = acting.get(reverse("connect_web:opportunities"))
    assert response.status_code == 200
    body = response.content.decode()
    assert "Malaria nets" in body and "Learn" in body and "1 of 2 modules" in body
    assert 'name="viewport"' in body


def test_opportunity_list_shows_a_plain_error(acting, monkeypatch):
    def fail(session):
        raise ConnectAPIError(status=502)

    monkeypatch.setattr(service, "list_opportunities", fail)
    response = acting.get(reverse("connect_web:opportunities"))
    assert response.status_code == 200
    assert "Connect had a problem" in response.content.decode()


@pytest.mark.parametrize("tab", [key for key, _ in views.TABS])
def test_opportunity_detail_tabs_render(acting, mocked, monkeypatch, tab):
    monkeypatch.setattr(service, "opportunity_detail", lambda s, oid: _detail(claimed=True))
    response = acting.get(reverse("connect_web:opportunity", args=[OPP]) + f"?tab={tab}")
    assert response.status_code == 200
    assert f'data-testid="cw-tab-{tab}"' in response.content.decode()


def test_learn_tab_continues_in_the_player_once_started(acting, mocked):
    response = acting.get(reverse("connect_web:opportunity", args=[OPP]) + "?tab=learn")
    body = response.content.decode()
    assert 'data-testid="cw-continue-learning"' in body and "Continue learning" in body
    assert reverse("connect_web:form_entry", args=[OPP, "learn"]) in body
    assert "Intro" in body and "Using nets" in body


def test_learn_tab_starts_learning_then_opens_the_player(acting, mocked, monkeypatch):
    detail = _detail()
    detail["learn"] = service.learn_status(detail["opp"], {"completed_modules": [], "assessments": []})
    monkeypatch.setattr(service, "opportunity_detail", lambda s, oid: detail)
    body = acting.get(reverse("connect_web:opportunity", args=[OPP]) + "?tab=learn").content.decode()
    assert reverse("connect_web:start_learning", args=[OPP]) in body
    assert 'name="open_player" value="1"' in body
    response = acting.post(reverse("connect_web:start_learning", args=[OPP]), {"open_player": "1"})
    assert response.status_code == 302
    assert response["Location"] == reverse("connect_web:form_entry", args=[OPP, "learn"])
    assert mocked == [("start", OPP)]


def test_start_learning_failure_stays_on_the_tab_even_with_open_player(acting, mocked, monkeypatch):
    def refuse(s, oid):
        raise ConnectAPIError(status=500)

    monkeypatch.setattr(service, "start_learning", refuse)
    response = acting.post(reverse("connect_web:start_learning", args=[OPP]), {"open_player": "1"})
    assert response["Location"].endswith("?tab=learn")


def test_claim_button_is_disabled_until_learning_complete(acting, mocked, monkeypatch):
    body = acting.get(reverse("connect_web:opportunity", args=[OPP]) + "?tab=claim").content.decode()
    assert "disabled" in body
    monkeypatch.setattr(service, "opportunity_detail", lambda s, oid: _detail(can_claim=True))
    body = acting.get(reverse("connect_web:opportunity", args=[OPP]) + "?tab=claim").content.decode()
    assert 'aria-disabled="true"' not in body


def test_deliver_and_payments_tabs_show_progress_and_buttons(acting, mocked, monkeypatch):
    monkeypatch.setattr(service, "opportunity_detail", lambda s, oid: _detail(claimed=True))
    body = acting.get(reverse("connect_web:opportunity", args=[OPP]) + "?tab=deliver").content.decode()
    assert "House 2" in body and "Too far from the last visit" in body
    assert reverse("connect_web:form_entry", args=[OPP, "deliver"]) in body
    body = acting.get(reverse("connect_web:opportunity", args=[OPP]) + "?tab=payments").content.decode()
    assert 'value="9b0c-pay-uuid"' in body and 'value="confirm"' in body and 'value="dispute"' in body


def test_form_entry_mounts_the_player_with_labs_urls_only(acting, mocked):
    response = acting.get(reverse("connect_web:form_entry", args=[OPP, "learn"]))
    assert response.status_code == 200
    body = response.content.decode()
    assert 'id="cw-form-player-root"' in body and "connect-web-bundle.js" in body
    config = response.context["player_config"]
    assert config["appKind"] == "learn" and config["opportunityId"] == OPP
    assert config["api"]["navigate"] == reverse("connect_web:api_navigate", args=[OPP, "learn"])
    # No navigation targets in the page: the player builds them (api.ts opportunityUrl).
    assert "backUrl" not in config and "doneUrl" not in config
    # The browser is told nothing about where the app runs, or as whom.
    for secret in ("nets-learn", "learnapp123", "commcarehq.org", "PoolUserOne"):
        assert secret not in body
    assert acting.get(reverse("connect_web:form_entry", args=[OPP, "bogus"])).status_code == 404


def test_form_entry_says_when_the_engine_is_not_configured(acting, mocked, settings):
    settings.CONNECT_WEB_FORMPLAYER_URL = ""
    body = acting.get(reverse("connect_web:form_entry", args=[OPP, "learn"])).content.decode()
    assert 'data-testid="cw-engine-not-configured"' in body and "cw-form-player-root" not in body


def test_form_entry_for_deliver_needs_a_claim(acting, mocked):
    body = acting.get(reverse("connect_web:form_entry", args=[OPP, "deliver"])).content.decode()
    assert 'data-testid="cw-error"' in body and "Claim the opportunity before delivering" in body
    assert "cw-form-player-root" not in body


def test_opportunity_page_after_a_submission_polls_progress(acting, mocked):
    body = acting.get(reverse("connect_web:opportunity", args=[OPP]) + "?tab=learn&submitted=learn").content.decode()
    assert 'data-testid="cw-submitted"' in body and "waiting for Connect to process" in body
    assert reverse("connect_web:api_progress", args=[OPP]) in body
    assert 'id="cw-progress-baseline"' in body
    plain = acting.get(reverse("connect_web:opportunity", args=[OPP]) + "?tab=learn&submitted=bogus").content
    assert b"cw-submitted" not in plain


# -- actions --------------------------------------------------------------------------------


def test_start_learning_posts_and_redirects(acting, mocked):
    response = acting.post(reverse("connect_web:start_learning", args=[OPP]))
    assert response["Location"] == reverse("connect_web:opportunity", args=[OPP]) + "?tab=learn"
    assert mocked == [("start", OPP)]
    assert any("Learning started" in m for m in _messages(response))


def test_start_learning_get_is_not_allowed(acting, mocked):
    assert acting.get(reverse("connect_web:start_learning", args=[OPP])).status_code == 405
    assert mocked == []


def test_claim_guard_message(acting, monkeypatch):
    def refuse(session, oid):
        raise GuardRefused(error_code=LEARNING_INCOMPLETE)

    monkeypatch.setattr(service, "claim", refuse)
    response = acting.post(reverse("connect_web:claim", args=[OPP]))
    assert response["Location"].endswith("?tab=claim")
    assert any("can't claim yet" in m for m in _messages(response))


def test_claim_server_error_message(acting, monkeypatch):
    def full(session, oid):
        raise ConnectAPIError(status=400, error_code="OPPORTUNITY_FULL")

    monkeypatch.setattr(service, "claim", full)
    response = acting.post(reverse("connect_web:claim", args=[OPP]))
    assert any("opportunity is full" in m for m in _messages(response))


@pytest.mark.parametrize("decision,expected", [("confirm", True), ("dispute", False)])
def test_confirm_and_dispute(acting, mocked, decision, expected):
    response = acting.post(
        reverse("connect_web:confirm_payment", args=[OPP]), {"payment_id": "9b0c-pay-uuid", "decision": decision}
    )
    assert response["Location"].endswith("?tab=payments")
    assert mocked == [("confirm", "9b0c-pay-uuid", expected)]


def test_confirm_needs_a_decision(acting, mocked):
    response = acting.post(reverse("connect_web:confirm_payment", args=[OPP]), {"payment_id": "x"})
    assert mocked == [] and response.status_code == 302


def test_tokens_never_reach_the_page(acting, staff, mocked):
    worker = WorkerSession.objects.get(user=staff, ended_at__isnull=True).pool_worker
    worker.access_token = "pid-secret-token"
    worker.personalid_password = "pid-secret-password"
    worker.save()
    for url in (
        reverse("connect_web:workers"),
        reverse("connect_web:opportunities"),
        reverse("connect_web:opportunity", args=[OPP]),
        reverse("connect_web:form_entry", args=[OPP, "learn"]),
    ):
        body = acting.get(url).content.decode()
        assert "pid-secret-token" not in body and "pid-secret-password" not in body


def test_check_out_with_a_bad_worker_id_is_a_404(client, staff):
    client.force_login(staff)
    assert client.post(reverse("connect_web:workers"), {"pool_worker": "abc"}).status_code == 404
    assert client.post(reverse("connect_web:workers"), {"pool_worker": "999"}).status_code == 404


def test_opportunity_route_matches_the_players_hardcoded_prefix():
    # static/connect_web/api.ts OPPORTUNITY_ROUTE builds the player's back/done links from this
    # route without asking the server; if the URL moves, move that constant too.
    assert reverse("connect_web:opportunity", args=["X"]) == "/connect-web/opportunities/X/"
