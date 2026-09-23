"""The alert and supplier-link screens, driven through the browser.

THIS REPOSITORY IS PUBLIC. Every name and address here is invented.
"""

import re

import pytest
from django.urls import reverse

from connect_labs.labs.models import LabsOrg
from connect_labs.supply_chain.alerts.models import AlertNotice, AlertSubscription
from connect_labs.supply_chain.models import SupplyPoint
from connect_labs.supply_chain.update_links import tokens
from connect_labs.supply_chain.update_links.models import UpdateLink

pytestmark = pytest.mark.django_db

PROGRAM = 10504


@pytest.fixture
def user(client, django_user_model):
    account = django_user_model.objects.create_user(username="grace", password="x", email="grace@dimagi.com")
    client.force_login(account)
    return account


@pytest.fixture
def scoped(client, user, monkeypatch):
    from connect_labs.supply_chain import form_views, views  # noqa: F401  -- bind before patching
    from connect_labs.supply_chain.alerts import views as alert_views  # noqa: F401
    from connect_labs.supply_chain.api_views import _access as real_access
    from connect_labs.supply_chain.update_links import views as link_views  # noqa: F401

    def _scoped(request):
        access = real_access(request)
        access.program_id = PROGRAM
        return access

    for module in ("form_views", "views", "alerts.views", "update_links.views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}.has_program_context", lambda request: True)
        if module != "update_links.views":
            monkeypatch.setattr(f"connect_labs.supply_chain.{module}._access", _scoped)
    return client


@pytest.fixture
def store():
    return SupplyPoint.objects.create(
        program_id=PROGRAM, slug="central-store", name="Central store", kind="central_store", source="we_recorded"
    )


class TestAlertScreens:
    def test_the_page_lists_subscriptions_and_the_log(self, scoped, user):
        sub = AlertSubscription.objects.create(
            program_id=PROGRAM,
            label="Low stock to EvAc",
            check_kinds=["stock_below_minimum"],
            recipient_email="evac@example.org",
        )
        from django.utils import timezone

        AlertNotice.objects.create(
            subscription=sub,
            program_id=PROGRAM,
            kind="check",
            subject_kind="stock_below_minimum",
            subject={"type": "supply_point", "id": 1, "label": "Central store"},
            detected_at=timezone.now(),
            delivery="email_disabled",
        )
        body = scoped.get(reverse("supply_chain:alerts")).content.decode()
        assert "Low stock to EvAc" in body
        assert "evac@example.org" in body
        assert "not sent — email is off" in body
        assert reverse("supply_chain:alert_create") in body

    def test_the_new_alert_screen_offers_every_check_the_domain_can_emit(self, scoped):
        from connect_labs.supply_chain.checks import KINDS

        body = scoped.get(reverse("supply_chain:alert_create")).content.decode()
        for kind in KINDS:
            assert f'value="{kind}"' in body, f"{kind} cannot be picked"

    def test_creating_an_alert_for_an_outside_address(self, scoped, store):
        response = scoped.post(
            reverse("supply_chain:alert_create"),
            {
                "label": "Chlorine low → EvAc",
                "check_kinds": ["stock_below_minimum", "stock_stockout"],
                "supply_point": store.pk,
                "recipient": "email",
                "recipient_email": "evac@example.org",
                "cadence": "immediate",
            },
        )
        assert response.status_code == 302, response.content.decode()[:2000]
        sub = AlertSubscription.objects.get()
        assert (sub.recipient_email, sub.supply_point_id) == ("evac@example.org", store.pk)
        assert sub.check_kinds == ["stock_below_minimum", "stock_stockout"]

    def test_creating_an_alert_for_myself(self, scoped, user):
        response = scoped.post(
            reverse("supply_chain:alert_create"),
            {"movement_kinds": ["transfer"], "recipient": "me", "cadence": "daily_digest"},
        )
        assert response.status_code == 302
        assert AlertSubscription.objects.get().recipient_user_id == user.pk

    def test_an_alert_that_watches_nothing_is_refused_on_the_page(self, scoped):
        response = scoped.post(
            reverse("supply_chain:alert_create"),
            {"recipient": "email", "recipient_email": "a@example.org", "cadence": "immediate"},
        )
        assert response.status_code == 200
        assert "has to watch something" in response.content.decode()
        assert not AlertSubscription.objects.exists()

    def test_editing_and_pausing(self, scoped):
        sub = AlertSubscription.objects.create(
            program_id=PROGRAM, check_kinds=["stock_stockout"], recipient_email="a@example.org"
        )
        page = scoped.get(reverse("supply_chain:alert_edit", args=[sub.pk])).content.decode()
        assert 'value="stock_stockout"' in page and "checked" in page
        response = scoped.post(
            reverse("supply_chain:alert_edit", args=[sub.pk]),
            {
                "check_kinds": ["stock_stockout"],
                "recipient": "email",
                "recipient_email": "b@example.org",
                "cadence": "immediate",
            },
        )
        assert response.status_code == 302
        sub.refresh_from_db()
        assert (sub.recipient_email, sub.active) == ("b@example.org", False)

    def test_editing_a_colleagues_alert_keeps_it_theirs(self, scoped, user, django_user_model):
        colleague = django_user_model.objects.create_user(username="ada", password="x", email="ada@dimagi.com")
        sub = AlertSubscription.objects.create(
            program_id=PROGRAM, check_kinds=["stock_stockout"], recipient_user=colleague, cadence="immediate"
        )
        page = scoped.get(reverse("supply_chain:alert_edit", args=[sub.pk])).content.decode()
        assert 'value="keep"' in page
        assert "ada@dimagi.com" in page
        response = scoped.post(
            reverse("supply_chain:alert_edit", args=[sub.pk]),
            {"check_kinds": ["stock_stockout"], "recipient": "keep", "cadence": "daily_digest", "active": "on"},
        )
        assert response.status_code == 302, response.content.decode()[:2000]
        sub.refresh_from_db()
        assert (sub.recipient_user_id, sub.cadence) == (colleague.pk, "daily_digest")

    def test_my_own_alert_offers_no_keep_choice(self, scoped, user):
        sub = AlertSubscription.objects.create(program_id=PROGRAM, check_kinds=["stock_stockout"], recipient_user=user)
        page = scoped.get(reverse("supply_chain:alert_edit", args=[sub.pk])).content.decode()
        assert 'value="keep"' not in page

    def test_another_programmes_alert_is_not_editable_here(self, scoped):
        theirs = AlertSubscription.objects.create(
            program_id=PROGRAM + 1, check_kinds=["stock_stockout"], recipient_email="a@example.org"
        )
        assert scoped.get(reverse("supply_chain:alert_edit", args=[theirs.pk])).status_code == 404

    def test_deleting(self, scoped):
        sub = AlertSubscription.objects.create(
            program_id=PROGRAM, check_kinds=["stock_stockout"], recipient_email="a@example.org"
        )
        scoped.post(reverse("supply_chain:alert_delete", args=[sub.pk]))
        assert not AlertSubscription.objects.exists()


class TestLinkScreens:
    @pytest.fixture
    def eha(self):
        return LabsOrg.objects.create(slug="eha", name="EHA Clinics")

    def test_issue_then_list_then_revoke(self, scoped, eha, store):
        response = scoped.post(
            reverse("supply_chain:update_link_issue"),
            {"org": eha.pk, "supply_points": [store.pk], "expires_in_days": 14, "label": "EHA warehouse"},
        )
        assert response.status_code == 200
        assert response["Cache-Control"] == "no-store"
        raw = re.search(r"/supply/u/([A-Za-z0-9_\-]+)/", response.content.decode()).group(1)
        link = tokens.find_usable_link(raw)
        assert link is not None and link.issued_by is not None

        listing = scoped.get(reverse("supply_chain:update_links")).content.decode()
        assert "EHA Clinics" in listing
        assert raw not in listing, "the list shows the token"
        assert link.token_hint in listing

        scoped.post(reverse("supply_chain:update_link_revoke", args=[link.pk]))
        assert UpdateLink.objects.get(pk=link.pk).revoked_at is not None
        assert tokens.find_usable_link(raw) is None

    def test_a_link_that_covers_nothing_is_refused_on_the_page(self, scoped, eha):
        response = scoped.post(reverse("supply_chain:update_link_issue"), {"org": eha.pk, "expires_in_days": 14})
        assert response.status_code == 200
        assert "has to cover something" in response.content.decode()
        assert not UpdateLink.objects.exists()


class TestThePublicPathIsOutsideTheLabsLogin:
    def test_the_oauth_boundary_skips_it(self):
        from connect_labs.labs.oauth_session import get_skip_path_prefixes

        assert "/supply/u/" in get_skip_path_prefixes()

    def test_nothing_else_under_supply_is_skipped(self):
        from connect_labs.labs.oauth_session import get_skip_path_prefixes

        assert [p for p in get_skip_path_prefixes() if p.startswith("/supply")] == ["/supply/u/"]
