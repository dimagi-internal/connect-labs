import pytest
from django.http import JsonResponse

from connect_labs.campaign.auth import decorators
from connect_labs.campaign.models import CampaignUser
from connect_labs.users.models import User


def _request(rf, user, role=None):
    req = rf.get("/campaign/api/thing/")
    req.user = user
    # minimal session stub
    req.session = (
        {"campaign_oauth": {"identity": {"username": getattr(user, "username", "")}}} if user.is_authenticated else {}
    )
    return req


@pytest.mark.django_db
def test_login_required_redirects_anonymous(rf):
    from django.contrib.auth.models import AnonymousUser

    @decorators.campaign_login_required
    def view(request):
        return JsonResponse({"ok": True})

    req = _request(rf, AnonymousUser())
    resp = view(req)
    assert resp.status_code == 302
    assert "/campaign/login/" in resp.url


@pytest.mark.django_db
def test_require_perm_allows_admin(rf):
    u = User.objects.create(username="a@dimagi.com", email="a@dimagi.com", name="A")
    CampaignUser.objects.create(
        commcare_username="a@dimagi.com", email="a@dimagi.com", name="A", role="campaign_admin"
    )

    @decorators.require_perm("payments", "approve")
    def view(request):
        return JsonResponse({"ok": True})

    resp = view(_request(rf, u))
    assert resp.status_code == 200


@pytest.mark.django_db
def test_require_perm_blocks_wrong_role(rf):
    u = User.objects.create(username="r@x.org", email="r@x.org", name="R")
    CampaignUser.objects.create(commcare_username="r@x.org", email="r@x.org", name="R", role="reporting_user")

    @decorators.require_perm("payments", "approve")
    def view(request):
        return JsonResponse({"ok": True})

    resp = view(_request(rf, u))
    assert resp.status_code == 403


@pytest.mark.django_db
def test_current_campaign_user(rf):
    u = User.objects.create(username="a@dimagi.com", email="a@dimagi.com", name="A")
    cu = CampaignUser.objects.create(
        commcare_username="a@dimagi.com", email="a@dimagi.com", name="A", role="campaign_admin"
    )
    assert decorators.current_campaign_user(_request(rf, u)).id == cu.id
