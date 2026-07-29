import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from connect_labs.supply.models import SupplierMember, SupplierOrg

pytestmark = pytest.mark.django_db

SIGNUP = {
    "email": "amina@acmefoods.example",
    "password1": "correct-horse-9",
    "password2": "correct-horse-9",
    "org_name": "Acme Therapeutic Foods",
    "org_country": "NG",
}


def test_signup_creates_user_org_membership():
    resp = Client().post("/supply/signup/", SIGNUP)
    assert resp.status_code == 302
    assert resp.url == "/supply/"
    member = SupplierMember.objects.get(user__email="amina@acmefoods.example")
    assert member.org.legal_name == "Acme Therapeutic Foods"
    assert member.org.country == "NG"


def test_login_logout_flow():
    Client().post("/supply/signup/", SIGNUP)
    c = Client()
    resp = c.post("/supply/login/", {"email": SIGNUP["email"], "password": "correct-horse-9"})
    assert resp.status_code == 302
    assert resp.url == "/supply/"
    assert c.post("/supply/logout/").status_code == 302
    # after logout the SPA shell bounces to login
    assert c.get("/supply/").url == "/supply/login/"


def test_signup_duplicate_email_shows_error():
    Client().post("/supply/signup/", SIGNUP)
    resp = Client().post("/supply/signup/", {**SIGNUP, "org_name": "Other Org"})
    assert resp.status_code == 200
    assert b"already" in resp.content.lower()
    assert SupplierOrg.objects.filter(legal_name="Other Org").count() == 0


def test_signup_duplicate_org_name_shows_error():
    Client().post("/supply/signup/", SIGNUP)
    resp = Client().post("/supply/signup/", {**SIGNUP, "email": "other@x.example"})
    assert resp.status_code == 200
    assert b"already" in resp.content.lower()
    assert get_user_model().objects.filter(email="other@x.example").count() == 0


@pytest.mark.parametrize("email", ["evil@dimagi.com", "attacker@dimagi-ai.com", "MixedCase@Dimagi.com"])
def test_signup_rejects_privileged_domains(email):
    """Open signup must not mint labs-privileged (Dimagi) accounts in the shared
    user table — that would grant labs admin from an unverified web form."""
    resp = Client().post("/supply/signup/", {**SIGNUP, "email": email})
    assert resp.status_code == 200
    assert get_user_model().objects.filter(email__iexact=email).count() == 0
    assert get_user_model().objects.filter(username__iexact=email).count() == 0
    assert SupplierOrg.objects.count() == 0


def test_signup_password_mismatch():
    resp = Client().post("/supply/signup/", {**SIGNUP, "password2": "different-9"})
    assert resp.status_code == 200
    assert b"match" in resp.content.lower()
    assert SupplierOrg.objects.count() == 0


def test_login_bad_password():
    Client().post("/supply/signup/", SIGNUP)
    resp = Client().post("/supply/login/", {"email": SIGNUP["email"], "password": "wrong"})
    assert resp.status_code == 200
    assert b"invalid" in resp.content.lower()


def test_login_page_renders_for_anonymous():
    assert Client().get("/supply/login/").status_code == 200
    assert Client().get("/supply/signup/").status_code == 200
