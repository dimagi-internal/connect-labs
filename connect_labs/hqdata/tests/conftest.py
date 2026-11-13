from datetime import timedelta
from unittest import mock

import pytest
from django.utils import timezone

from connect_labs.hqdata.identity import hq_host
from connect_labs.hqdata.models import HQIdentity
from connect_labs.labs.models import UserCCHQToken
from connect_labs.users.models import User


@pytest.fixture(autouse=True)
def hq_settings(settings):
    settings.COMMCARE_HQ_URL = "https://www.commcarehq.org"
    settings.COMMCARE_OAUTH_CLIENT_ID = "client-id"
    settings.COMMCARE_OAUTH_CLIENT_SECRET = "client-secret"


@pytest.fixture
def queued():
    """Stands in for the Celery queue: records what a page asked to run."""
    with mock.patch("connect_labs.hqdata.web.views.sync_project_task") as task:
        yield task


def make_hq_user(client, *, domains=("demo",), username="simon@example.org", **user_kwargs):
    """A CommCare-only person, signed in: no Connect token, a live CommCare one, a fresh identity."""
    # Not UserFactory: it is get_or_create on email, so email=None would hand back whichever
    # no-email user already exists (a service account a migration made) instead of a new one.
    user = User.objects.create(username=f"cchq:{hq_host()}:{username}", email=None, **user_kwargs)
    user.set_unusable_password()
    user.save()
    UserCCHQToken.objects.create(
        user=user, access_token="hq-access", refresh_token="hq-refresh", expires_at=timezone.now() + timedelta(hours=1)
    )
    HQIdentity.objects.create(
        user=user, hq_host=hq_host(), hq_username=username, domains=list(domains), refreshed_at=timezone.now()
    )
    client.force_login(user)
    session = client.session
    session["hq_auth"] = {"host": hq_host(), "at": timezone.now().timestamp()}
    session.save()
    return user
