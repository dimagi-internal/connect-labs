"""Link management on the operator page.

Creating an unauthenticated URL to production delivery data is the riskiest
action in this app. It used to be reachable only from a Django shell on prod,
which is the least controlled place to put it.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from connect_labs.pulse.models import PulsePublicToken


@pytest.fixture
def operator(client, django_user_model):
    user = django_user_model.objects.create(username="operator")
    client.force_login(user)
    return user


@pytest.mark.django_db
class TestCreate:
    def test_creates_a_link_for_the_chosen_layout(self, client, operator):
        r = client.post(reverse("pulse:index"), {"action": "create", "layout": "financial", "label": "Gates"})
        assert r.status_code == 302, "must redirect after POST so refresh cannot mint a second link"

        t = PulsePublicToken.objects.get()
        assert (t.layout_slug, t.label, t.revoked) == ("financial", "Gates", False)
        assert t.created_by == operator
        assert len(t.token) >= 24, "share tokens must not be guessable"

    def test_default_names_partners_and_the_operator_is_told(self, client, operator):
        """An absent checkbox is the MORE disclosing branch, so it gets said
        back rather than left to be discovered."""
        r = client.post(reverse("pulse:index"), {"action": "create", "layout": "nightmap"}, follow=True)
        assert PulsePublicToken.objects.get().show_partner_names is True
        said = " ".join(m.message for m in r.context["messages"])
        assert "partner organisation names" in said
        assert "per-service rates" in said

    def test_anonymise_partners_is_honoured(self, client, operator):
        client.post(
            reverse("pulse:index"),
            {"action": "create", "layout": "nightmap", "anonymise_partners": "on"},
        )
        assert PulsePublicToken.objects.get().show_partner_names is False

    def test_unknown_layout_creates_nothing(self, client, operator):
        client.post(reverse("pulse:index"), {"action": "create", "layout": "../../etc/passwd"})
        assert not PulsePublicToken.objects.exists()


@pytest.mark.django_db
class TestRevokeAndDisclosure:
    def _mint(self, client, layout="nightmap"):
        client.post(reverse("pulse:index"), {"action": "create", "layout": layout})
        return PulsePublicToken.objects.order_by("-id").first()

    def test_revoking_one_link_spares_the_others(self, client, operator):
        doomed = self._mint(client, "nightmap")
        spared = self._mint(client, "mission")

        client.post(reverse("pulse:index"), {"action": "revoke", "token": doomed.token})
        doomed.refresh_from_db()
        spared.refresh_from_db()
        assert doomed.revoked is True
        assert spared.revoked is False

    def test_a_revoked_link_stops_working_immediately(self, client, operator):
        t = self._mint(client)
        assert client.get(reverse("pulse:public", args=[t.token])).status_code == 200
        client.post(reverse("pulse:index"), {"action": "revoke", "token": t.token})
        client.logout()
        assert client.get(reverse("pulse:public", args=[t.token])).status_code == 404

    def test_partner_names_can_be_withdrawn_after_the_link_has_gone_out(self, client, operator):
        t = self._mint(client)
        client.post(reverse("pulse:index"), {"action": "partner_names", "token": t.token, "show": "off"})
        t.refresh_from_db()
        assert t.show_partner_names is False

    def test_unknown_action_changes_nothing(self, client, operator):
        self._mint(client)
        client.post(reverse("pulse:index"), {"action": "drop-everything"})
        assert PulsePublicToken.objects.filter(revoked=False).count() == 1


@pytest.mark.django_db
class TestAccess:
    def test_anonymous_cannot_mint_a_link(self, client):
        """The whole point is that these are handed out deliberately."""
        r = client.post(reverse("pulse:index"), {"action": "create", "layout": "nightmap"})
        assert r.status_code in (302, 403)
        assert not PulsePublicToken.objects.exists()

    def test_anonymous_cannot_revoke(self, client, django_user_model):
        from connect_labs.pulse.views import mint_public_token

        t = mint_public_token(django_user_model.objects.create(username="someone"))
        client.post(reverse("pulse:index"), {"action": "revoke", "token": t.token})
        t.refresh_from_db()
        assert t.revoked is False

    def test_the_page_offers_the_controls(self, client, operator):
        from connect_labs.pulse.views import mint_public_token

        mint_public_token(operator, label="A funder")
        body = client.get(reverse("pulse:index")).content.decode()
        assert 'name="action" value="create"' in body
        assert 'name="action" value="revoke"' in body
        assert "csrfmiddlewaretoken" in body, "forms must be CSRF-protected"
