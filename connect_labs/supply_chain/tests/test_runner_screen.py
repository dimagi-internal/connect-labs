"""Platform support declares who runs a programme from a screen, not only over MCP (#2458).

THIS REPOSITORY IS PUBLIC. Every organisation and person here is invented.
"""

import pytest
from django.urls import reverse

from connect_labs.labs.models import LabsOrg
from connect_labs.supply_chain.runner import declared_runner

pytestmark = [pytest.mark.django_db, pytest.mark.real_standing]

PROGRAM = 10904


@pytest.fixture
def scoped(client, monkeypatch):
    from connect_labs.supply_chain import form_views, views  # noqa: F401  -- bind before patching
    from connect_labs.supply_chain.api_views import _access as real_access

    def _scoped(request):
        access = real_access(request)
        access.program_id = PROGRAM
        return access

    for module in ("form_views", "views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}.has_program_context", lambda request: True)
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}._access", _scoped)
    return client


@pytest.fixture
def kilele():
    return LabsOrg.objects.create(slug="labs-synthetic-kilele", name="Kilele Community Health", labs_only=True)


def _sign_in(client, django_user_model, **flags):
    user = django_user_model.objects.create_user(username="someone", email="someone@kilele.example", **flags)
    client.force_login(user)
    return user


def test_support_declares_from_the_screen(scoped, django_user_model, kilele):
    _sign_in(scoped, django_user_model, is_staff=True)
    url = reverse("supply_chain:programme_runner")
    assert "Kilele Community Health" in scoped.get(url).content.decode()

    response = scoped.post(url, {"org": kilele.pk, "note": "asked for by their admin"})

    assert response.status_code == 302
    assert declared_runner(PROGRAM) == kilele


def test_anyone_else_is_refused_the_screen(scoped, django_user_model, kilele):
    _sign_in(scoped, django_user_model)
    url = reverse("supply_chain:programme_runner")
    assert scoped.get(url).status_code == 403
    assert scoped.post(url, {"org": kilele.pk}).status_code == 403
    assert declared_runner(PROGRAM) is None
