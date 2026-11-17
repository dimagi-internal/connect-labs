"""A CommCare project's page shown inside CommCare HQ to a person with no Connect login.

HQ is an embed client with a ``commcare_host``. It may name only ``cchq:<its host>:<user>``, only for the
``hq_workflow`` kind, and (unless it vouches) only people labs already knows to belong to the project.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from canopy_sdk.contract import JWT_BEARER_GRANT
from canopy_sdk.keys import generate_private_key, public_jwk
from django.core.exceptions import ValidationError
from django.utils import timezone as dj_timezone

from connect_labs.embed.models import EmbedClient, EmbedSession
from connect_labs.hqdata.access import create_project
from connect_labs.hqdata.models import HQForm, HQProject, HQProjectMember
from connect_labs.hqdata.workflows import ensure_project_dashboard
from connect_labs.users.models import User

LABS = "https://labs.example.org"
HQ_ORIGIN = "https://hq.example.org"
HOST = "www.commcarehq.org"
TOKEN_URL = "/embed/token/"

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _labs_url(settings):
    settings.LABS_PUBLIC_URL = LABS
    settings.LABS_EMBED_ENABLED = True
    settings.ALLOWED_HOSTS = ["*"]
    settings.COMMCARE_HQ_URL = f"https://{HOST}"


@pytest.fixture
def key():
    return generate_private_key("EdDSA")


def cchq_user(name="ann@example.org") -> User:
    user = User.objects.create(username=f"cchq:{HOST}:{name}", email=None)
    user.set_unusable_password()
    user.save()
    return user


@pytest.fixture
def member():
    return cchq_user()


@pytest.fixture
def project(member):
    return create_project(member, "demo", hq_host=HOST, can_read_domain=lambda: True)


@pytest.fixture
def page(member, project):
    for i, name in enumerate(["ann", "ann", "bob"]):
        HQForm.objects.create(
            project_id=project.pk,
            form_id=f"f{i}",
            xmlns="http://x/1",
            app_id="app1",
            username=name,
            received_on=datetime(2026, 11, 1, 10, tzinfo=timezone.utc),
            form_json={"id": f"f{i}", "received_on": "2026-11-01T10:00:00Z", "form": {"meta": {"username": name}}},
        )
    return ensure_project_dashboard(member, project)


def make_client(key, project, page, **overrides):
    fields = {
        "client_id": "hq",
        "name": "CommCare HQ",
        "commcare_host": HOST,
        "jwks": {"keys": [public_jwk(key)]},
        "allowed_origins": [HQ_ORIGIN],
        "resources": [{"kind": "hq_workflow", "id": page, "project_id": project.pk}],
    }
    fields.update(overrides)
    return EmbedClient.objects.create(**fields)


@pytest.fixture
def hq(key, project, page):
    return make_client(key, project, page)


def resource(page, client_id="hq"):
    return f"{LABS}/embed/c/{client_id}/hq_workflow/{page}/"


def assertion(key, page, sub, **overrides):
    now = int(time.time())
    claims = {
        "iss": "hq",
        "sub": sub,
        "aud": f"{LABS}/embed/token/",
        "iat": now,
        "exp": now + 60,
        "jti": str(uuid.uuid4()),
        "resource": resource(page),
    }
    claims.update(overrides)
    return jwt.encode(claims, key, algorithm="EdDSA", headers={"kid": public_jwk(key)["kid"]})


def redeem(client, token):
    return client.post(TOKEN_URL, {"grant_type": JWT_BEARER_GRANT, "assertion": token})


def sub_of(user_name="ann@example.org", host=HOST):
    return f"cchq:{host}:{user_name}"


# --- the grant: who HQ may name ----------------------------------------------------------------


def test_a_known_member_gets_a_token_with_no_connect_token_anywhere(client, key, hq, page, member):
    response = redeem(client, assertion(key, page, sub_of()))
    assert response.status_code == 200, response.json()
    session = EmbedSession.objects.get()
    assert (session.user, session.kind, session.resource_id) == (member, "hq_workflow", str(page))
    assert response.json()["embed_url"] == resource(page)


@pytest.mark.parametrize(
    "sub",
    ["ann@example.org", "pat", "cchq:other.example.org:ann@example.org", f"cchq:{HOST}:", f"cchq:{HOST.upper()}:ann"],
)
def test_hq_may_only_name_its_own_commcare_people(client, key, hq, page, sub):
    response = redeem(client, assertion(key, page, sub))
    assert response.status_code == 400 and response.json()["error"] == "invalid_grant"
    assert not EmbedSession.objects.exists()


def test_a_person_labs_has_never_seen_is_refused_when_hq_does_not_vouch(client, key, hq, page):
    response = redeem(client, assertion(key, page, sub_of("new@example.org")))
    assert response.status_code == 400
    assert not User.objects.filter(username__contains="new@example.org").exists()  # not even created


def test_a_known_person_who_is_not_in_the_project_is_refused(client, key, hq, page):
    cchq_user("outsider@example.org")
    response = redeem(client, assertion(key, page, sub_of("outsider@example.org")))
    assert response.status_code == 400 and not EmbedSession.objects.exists()


def test_a_member_whose_proof_has_lapsed_is_refused(client, key, hq, page, member, project):
    HQProjectMember.objects.filter(project=project).update(verified_at=dj_timezone.now() - timedelta(days=3))
    assert redeem(client, assertion(key, page, sub_of())).status_code == 400


def test_a_connect_style_client_cannot_show_a_project_page_or_name_commcare_people(key, project, page):
    row = EmbedClient(
        client_id="conn",
        name="Connect",
        identity_authority=True,
        jwks={"keys": [public_jwk(key)]},
        allowed_origins=[HQ_ORIGIN],
        resources=[{"kind": "hq_workflow", "id": page, "project_id": project.pk}],
    )
    with pytest.raises(ValidationError):
        row.full_clean()


def test_a_commcare_client_cannot_show_a_connect_report(key, project, page):
    row = EmbedClient(
        client_id="hq2",
        name="HQ",
        commcare_host=HOST,
        jwks={"keys": [public_jwk(key)]},
        allowed_origins=[HQ_ORIGIN],
        resources=[{"kind": "workflow", "id": 1, "opportunity_id": 2}],
    )
    with pytest.raises(ValidationError):
        row.full_clean()


@pytest.mark.parametrize(
    "fields",
    [
        {"identity_authority": True},  # a CommCare client is never an identity authority
        {"commcare_host": "https://www.commcarehq.org/"},  # a bare host only
    ],
)
def test_a_commcare_client_is_registered_narrowly(key, project, page, fields):
    row = EmbedClient(
        client_id="hq3",
        name="HQ",
        commcare_host=fields.get("commcare_host", HOST),
        identity_authority=fields.get("identity_authority", False),
        jwks={"keys": [public_jwk(key)]},
        allowed_origins=[HQ_ORIGIN],
        resources=[{"kind": "hq_workflow", "id": page, "project_id": project.pk}],
    )
    with pytest.raises(ValidationError):
        row.full_clean()


def test_vouching_needs_a_commcare_host(key):
    row = EmbedClient(
        client_id="x",
        name="X",
        vouches_for_membership=True,
        jwks={"keys": [public_jwk(key)]},
        allowed_origins=[HQ_ORIGIN],
    )
    with pytest.raises(ValidationError):
        row.full_clean()


def test_a_forced_mismatch_in_the_database_is_still_refused_at_the_grant(client, key, project, page, member):
    # Registration checks are not the only line: the grant refuses a Connect-style client showing a project page.
    make_client(key, project, page, client_id="hq", commcare_host="", identity_authority=True)
    response = redeem(client, assertion(key, page, member.username))
    assert response.status_code == 400 and not EmbedSession.objects.exists()


# --- vouching ---------------------------------------------------------------------------------


@pytest.fixture
def vouching(key, project, page):
    return make_client(key, project, page, vouches_for_membership=True)


def test_a_vouching_client_creates_an_unseen_person_with_no_email_and_one_membership(
    client, key, vouching, page, project
):
    other = HQProject.objects.create(domain="other", hq_host=HOST)
    response = redeem(client, assertion(key, page, sub_of("new@example.org")))
    assert response.status_code == 200, response.json()
    user = User.objects.get(username=sub_of("new@example.org"))
    assert not user.email  # never a Dimagi or staff address
    assert not user.has_usable_password()
    assert HQProjectMember.objects.filter(user=user, project=project, role=HQProjectMember.ROLE_MEMBER).exists()
    assert not HQProjectMember.objects.filter(user=user, project=other).exists()  # only the registered project


def test_vouching_never_promotes_and_never_reaches_a_project_on_another_host(
    client, key, vouching, page, project, member
):
    assert HQProjectMember.objects.get(project=project, user=member).role == HQProjectMember.ROLE_ADMIN
    assert redeem(client, assertion(key, page, sub_of())).status_code == 200
    assert HQProjectMember.objects.get(project=project, user=member).role == HQProjectMember.ROLE_ADMIN
    HQProject.objects.filter(pk=project.pk).update(hq_host="other.example.org")
    assert redeem(client, assertion(key, page, sub_of("again@example.org"))).status_code == 400


def test_vouching_does_not_create_a_project_that_is_not_registered(client, key, project, page):
    row = make_client(key, project, page, vouches_for_membership=True)
    row.resources = [{"kind": "hq_workflow", "id": page, "project_id": project.pk + 999}]
    row.save()
    assert redeem(client, assertion(key, page, sub_of("new@example.org"))).status_code == 400
    assert not HQProject.objects.filter(pk=project.pk + 999).exists()


# --- the session ----------------------------------------------------------------------------


def start(client, token, page):
    # A browser's frame sends its own address as the Referer, which is what makes the middleware treat the
    # POST as "from a frame" (and so subject to the read-only rule).
    payload = {"token": token, "client_id": "hq", "kind": "hq_workflow", "id": str(page)}
    return client.post(
        "/embed/session/", payload, content_type="application/json", HTTP_REFERER=resource(page), HTTP_ORIGIN=LABS
    )


def sign_in(client, key, page, who=None):
    token = redeem(client, assertion(key, page, who or sub_of())).json()["access_token"]
    assert start(client, token, page).status_code == 200
    return resource(page)[len(LABS) :]


def test_the_frame_draws_the_page_sandboxed_with_one_data_read_and_no_labs_routes(client, key, hq, page):
    frame = sign_in(client, key, page)
    response = client.get(frame)
    assert response.status_code == 200
    html = response.content.decode()
    data = json.loads(
        re.search(r'<script id="workflow-data" type="application/json">(.*?)</script>', html, re.S).group(1)
    )
    assert data["sandbox"]["mode"] == "page" and data["is_page"] is True
    api = data["apiEndpoints"]
    assert api["getPipelineData"] == f"/embed/hq/{hq.resources[0]['project_id']}/{page}/pipeline-data/"
    assert all(v is None for k, v in api.items() if k != "getPipelineData")
    assert (
        response["Content-Security-Policy"].startswith("frame-src ")
        and HQ_ORIGIN in response["Content-Security-Policy"]
    )


def test_the_frames_one_read_returns_the_projects_rows_as_the_person(client, key, hq, page, project):
    frame = sign_in(client, key, page)
    client.get(frame)  # the frame is drawn once
    url = f"/embed/hq/{project.pk}/{page}/pipeline-data/"
    got = client.get(url, HTTP_REFERER=LABS + frame)
    assert got.status_code == 200
    assert sorted((r["username"], r["forms"]) for r in got.json()["workers"]["rows"]) == [("ann", 2), ("bob", 1)]


def test_the_read_is_refused_without_the_frames_own_referrer_and_without_a_session(client, key, hq, page, project):
    frame = sign_in(client, key, page)
    client.get(frame)
    url = f"/embed/hq/{project.pk}/{page}/pipeline-data/"
    assert client.get(url).status_code == 404  # no Referer from the frame: the cookie is not authority here
    client.cookies.clear()
    assert client.get(url, HTTP_REFERER=LABS + frame).status_code == 404


def test_a_session_reaches_nothing_but_its_own_pages_read(client, key, hq, page, project):
    frame = sign_in(client, key, page)
    client.get(frame)
    other = HQProject.objects.create(domain="other", hq_host=HOST)
    for path in (
        f"/embed/hq/{other.pk}/{page}/pipeline-data/",  # another project
        f"/embed/hq/{project.pk}/{page + 1}/pipeline-data/",  # another page
        "/hq/",  # the person's /hq/ home is not an embed route
        f"/hq/projects/{project.pk}/",
        "/labs/workflow/api/1/pipeline-data/",
    ):
        got = client.get(path, HTTP_REFERER=LABS + frame)
        assert got.status_code in (302, 403, 404), (path, got.status_code)
        assert b"workers" not in got.content, path


def test_a_write_from_the_frame_is_refused(client, key, hq, page, project):
    frame = sign_in(client, key, page)
    client.get(frame)
    got = client.post(f"/embed/hq/{project.pk}/{page}/pipeline-data/", {}, HTTP_REFERER=LABS + frame)
    assert got.status_code == 403


def test_a_person_removed_from_the_project_stops_reading_at_once_not_when_the_session_ends(
    client, key, hq, page, project
):
    frame = sign_in(client, key, page)
    client.get(frame)
    url = f"/embed/hq/{project.pk}/{page}/pipeline-data/"
    assert client.get(url, HTTP_REFERER=LABS + frame).status_code == 200
    HQProjectMember.objects.filter(project=project).delete()
    got = client.get(url, HTTP_REFERER=LABS + frame)
    assert got.status_code == 401 and b"workers" not in got.content


def test_a_membership_that_lapses_mid_session_also_stops_it(client, key, hq, page, project):
    frame = sign_in(client, key, page)
    client.get(frame)
    HQProjectMember.objects.filter(project=project).update(verified_at=dj_timezone.now() - timedelta(days=3))
    assert client.get(f"/embed/hq/{project.pk}/{page}/pipeline-data/", HTTP_REFERER=LABS + frame).status_code == 401


def test_the_session_carries_no_connect_session_and_the_users_labs_session_is_untouched(client, key, hq, page):
    frame = sign_in(client, key, page)
    client.get(frame)
    assert "labs_oauth" not in client.session
    assert EmbedSession.objects.get().user.username == sub_of()


# --- review fixes: a refused assertion changes nothing; the host binds every CommCare client ---------


def test_a_replayed_vouching_assertion_does_not_keep_a_membership_alive(client, key, vouching, page, project, member):
    token = assertion(key, page, sub_of())
    assert redeem(client, token).status_code == 200
    old = dj_timezone.now() - timedelta(hours=20)
    HQProjectMember.objects.filter(project=project, user=member).update(verified_at=old)
    assert redeem(client, token).status_code == 400  # replayed
    assert HQProjectMember.objects.get(project=project, user=member).verified_at == old


def test_an_assertion_refused_after_the_person_was_seen_creates_nobody(client, key, vouching, page, project):
    HQProject.objects.filter(pk=project.pk).update(store=HQProject.STORE_EXTERNAL)  # workflows unavailable
    response = redeem(client, assertion(key, page, sub_of("late@example.org")))
    assert response.status_code == 400
    assert not User.objects.filter(username=sub_of("late@example.org")).exists()
    assert not HQProjectMember.objects.filter(user__username=sub_of("late@example.org")).exists()


@pytest.mark.parametrize("name", [{"a": 1}, 12345, "x" * 5000, None])
def test_a_strange_name_claim_never_breaks_the_grant(client, key, vouching, page, name):
    response = redeem(client, assertion(key, page, sub_of("named@example.org"), name=name))
    assert response.status_code == 200
    assert len(User.objects.get(username=sub_of("named@example.org")).name) <= 100


def test_a_project_on_another_host_is_refused_even_when_hq_does_not_vouch(client, key, hq, page, project):
    HQProject.objects.filter(pk=project.pk).update(hq_host="other.example.org")
    assert redeem(client, assertion(key, page, sub_of())).status_code == 400


def test_registering_a_project_on_another_host_or_a_missing_one_is_refused(key, project, page):
    for entry in (
        {"kind": "hq_workflow", "id": page, "project_id": project.pk + 999},
        {"kind": "hq_workflow", "id": page, "project_id": project.pk},
    ):
        HQProject.objects.filter(pk=project.pk).update(hq_host="other.example.org")
        row = EmbedClient(
            client_id="hq4",
            name="HQ",
            commcare_host=HOST,
            jwks={"keys": [public_jwk(key)]},
            allowed_origins=[HQ_ORIGIN],
            resources=[entry],
        )
        with pytest.raises(ValidationError):
            row.full_clean()


def test_one_page_id_cannot_be_listed_twice_for_two_projects(key, project, page, member):
    second = create_project(member, "second", hq_host=HOST, can_read_domain=lambda: True)
    row = EmbedClient(
        client_id="hq5",
        name="HQ",
        commcare_host=HOST,
        jwks={"keys": [public_jwk(key)]},
        allowed_origins=[HQ_ORIGIN],
        resources=[
            {"kind": "hq_workflow", "id": page, "project_id": project.pk},
            {"kind": "hq_workflow", "id": page, "project_id": second.pk},
        ],
    )
    with pytest.raises(ValidationError):
        row.full_clean()


def test_only_a_post_to_the_session_start_is_exempt_from_the_read_only_rule():
    from connect_labs.embed.kinds import get_kind

    kind = get_kind("hq_workflow")
    assert not kind.writes("POST", "/embed/session/")
    for method in ("PUT", "DELETE", "PATCH"):
        assert kind.writes(method, "/embed/session/")
    assert kind.writes("POST", "/embed/hq/1/2/pipeline-data/")
    assert not kind.writes("GET", "/embed/hq/1/2/pipeline-data/")
