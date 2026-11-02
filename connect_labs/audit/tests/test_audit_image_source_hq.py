"""Audits whose photos come from CommCare HQ (criteria.image_source == "commcare_hq").

Covers: criteria parsing, the image entries extraction records, the resume key,
the image URL a review page is given, and the HQ proxy view (HQ call mocked).
No real names, ids or blobs: every identifier below is made up.
"""

import json
from unittest.mock import patch

import httpx
import pytest
from django.contrib.auth.models import AnonymousUser
from django.db import connection
from django.test import RequestFactory
from django.urls import resolve, reverse

from connect_labs.audit import hq_images
from connect_labs.audit.data_access import AuditCriteria, AuditDataAccess
from connect_labs.audit.hq_images import HQImageError, fetch_hq_image
from connect_labs.audit.image_source import (
    hq_fields_for_image,
    is_hq_image,
    normalize_image_source,
    sniff_image_type,
    valid_attachment_name,
)
from connect_labs.audit.link_helpers import image_proxy_path
from connect_labs.audit.run_checkpoints import call_key, session_key
from connect_labs.audit.views import ExperimentAuditImageHQView
from connect_labs.labs.integrations.commcare.cchq_tokens import CCHQTokenError

OPP = 4242
DOMAIN = "demo-domain"
XFORM = "0a1b2c3d-0000-4000-8000-000000000001"
NAME = "photo_1.jpg"
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


# --------------------------------------------------------------------------- criteria


def test_image_source_defaults_to_connect():
    assert AuditCriteria.from_dict({}).image_source == "connect"
    assert AuditCriteria().image_source == "connect"


@pytest.mark.parametrize("key", ["image_source", "imageSource"])
def test_image_source_hq_parsed_from_either_case(key):
    assert AuditCriteria.from_dict({key: "commcare_hq"}).image_source == "commcare_hq"


def test_unknown_source_is_strict_error_but_lenient_on_read():
    with pytest.raises(ValueError):
        normalize_image_source({"image_source": "ftp"}, strict=True)
    assert normalize_image_source({"image_source": "ftp"}) == "connect"


# --------------------------------------------------------------------------- extraction


def _visit(vid, images, form_json):
    return {
        "id": vid,
        "username": "worker1",
        "entity_name": "E",
        "visit_date": "2026-09-01",
        "images": images,
        "form_json": form_json,
    }


def _extract(visits, **kw):
    da = AuditDataAccess.__new__(AuditDataAccess)
    da.opportunity_id = OPP
    with patch.object(AuditDataAccess, "fetch_visits_for_ids", return_value=visits):
        return da.extract_images_for_visits([v["id"] for v in visits], OPP, **kw)


FORM = {
    "domain": DOMAIN,
    "id": XFORM,
    "form": {"group": {"pic": NAME}, "meta": {"timeEnd": "2026-09-01T10:00:00Z"}},
}
RAW = {"name": NAME, "blob_id": "blob-1", "parent_id": XFORM}


def test_extraction_default_adds_no_hq_fields():
    result = _extract([_visit(1, [RAW], FORM)])
    (img,) = result["1"]
    assert img["blob_id"] == "blob-1"
    assert "hq_xform_id" not in img and "image_source" not in img


def test_extraction_hq_records_fetch_fields_and_keeps_blob_id():
    result = _extract([_visit(1, [RAW], FORM)], image_source="commcare_hq")
    (img,) = result["1"]
    assert img["blob_id"] == "blob-1"
    assert img["question_id"] == "group/pic"
    assert img["image_source"] == "commcare_hq"
    assert img["hq_xform_id"] == XFORM
    assert img["hq_attachment_name"] == NAME
    assert img["hq_domain"] == DOMAIN
    assert is_hq_image(img)


def test_hq_fields_fall_back_to_attachment_url_for_domain_and_form_id():
    form = {"attachments": {NAME: {"url": f"https://hq.example/a/{DOMAIN}/api/form_attachment/v1/{XFORM}/{NAME}"}}}
    fields = hq_fields_for_image({"name": NAME, "blob_id": "b"}, form, visit_xform_id=XFORM)
    assert fields["hq_domain"] == DOMAIN and fields["hq_xform_id"] == XFORM


def test_hq_fields_empty_when_unfetchable():
    assert hq_fields_for_image({"name": NAME}, {"domain": DOMAIN}) == {}  # no form id
    assert hq_fields_for_image({"name": NAME, "parent_id": XFORM}, {}) == {}  # no domain


# --------------------------------------------------------------------------- resume key


def test_call_key_differs_by_image_source():
    base = {"tag": "t", "start_date": "2026-09-01", "end_date": "2026-09-30"}
    assert call_key(OPP, base) != call_key(OPP, {**base, "image_source": "commcare_hq"})
    # absent == "connect": existing callers keep their key
    assert call_key(OPP, base) == call_key(OPP, {**base, "image_source": "connect"})


def test_session_key_reads_stored_source_and_old_sessions_are_connect():
    class S:
        def __init__(self, criteria):
            self.data = {"opportunity_id": OPP, "tag": "t", "criteria": criteria}

    window = {"start_date": "2026-09-01", "end_date": "2026-09-30"}
    hq_call = call_key(OPP, {"tag": "t", **window, "image_source": "commcare_hq"})
    old_session = S(dict(window))  # created before the option existed
    hq_session = S({**window, "image_source": "commcare_hq"})
    assert session_key(old_session) != hq_call  # an old Connect session is NOT resumed for an HQ call
    assert session_key(hq_session) == hq_call
    assert session_key(old_session) == call_key(OPP, {"tag": "t", **window})


# --------------------------------------------------------------------------- URLs


def test_image_proxy_path_connect_vs_hq():
    connect = image_proxy_path(OPP, "blob-1", {"blob_id": "blob-1"})
    assert connect == reverse("audit:audit_image_connect", kwargs={"opp_id": OPP, "blob_id": "blob-1"})
    entry = {
        "blob_id": "blob-1",
        "image_source": "commcare_hq",
        "hq_xform_id": XFORM,
        "hq_attachment_name": NAME,
        "hq_domain": DOMAIN,
    }
    hq = image_proxy_path(OPP, "blob-1", entry)
    assert hq.startswith(f"/audit/image/hq/{OPP}/{XFORM}/{NAME}/")
    assert hq.endswith(f"?domain={DOMAIN}")
    assert resolve(hq.split("?")[0]).url_name == "audit_image_hq"


# --------------------------------------------------------------------------- helpers


def test_sniff_and_name_validation():
    assert sniff_image_type(JPEG) == "image/jpeg"
    assert sniff_image_type(PNG) == "image/png"
    assert sniff_image_type(b"GIF89a....") == "image/gif"
    assert sniff_image_type(b"<html>error</html>") is None
    assert sniff_image_type(b'{"error": "nope"}') is None
    assert valid_attachment_name("a b.jpg")
    for bad in ("../x.jpg", "a/../b", "/abs", "a?b", "", "a\\b", "a\nb"):
        assert not valid_attachment_name(bad)


# --------------------------------------------------------------------------- fetch_hq_image


class _Resp:
    def __init__(self, status, content=b""):
        self.status_code = status
        self.content = content


def test_fetch_checks_status_and_bytes(monkeypatch):
    monkeypatch.setattr(hq_images, "HQ_FETCH_BACKOFF_BASE", 0)
    calls = []

    def fake_get(url, access_token):
        calls.append((url, access_token))
        return _Resp(200, JPEG)

    with patch.object(hq_images, "_get", fake_get):
        content, ctype = fetch_hq_image("tok", DOMAIN, XFORM, NAME)
    assert (content, ctype) == (JPEG, "image/jpeg")
    assert calls[0][0].endswith(f"/a/{DOMAIN}/api/form/attachment/{XFORM}/{NAME}")
    assert calls[0][1] == "tok"


def test_fetch_refuses_error_body_served_with_200(monkeypatch):
    with patch.object(hq_images, "_get", lambda *a: _Resp(200, b'{"error": "Invalid API key"}')):
        with pytest.raises(HQImageError) as exc:
            fetch_hq_image("tok", DOMAIN, XFORM, NAME)
    assert exc.value.code == "not_image"


def test_fetch_401_is_unauthorized():
    with patch.object(hq_images, "_get", lambda *a: _Resp(401)):
        with pytest.raises(hq_images.HQUnauthorized):
            fetch_hq_image("tok", DOMAIN, XFORM, NAME)


def test_fetch_falls_back_to_second_endpoint_on_404():
    urls = []

    def fake_get(url, tok):
        urls.append(url)
        return _Resp(404) if "/api/form/attachment/" in url else _Resp(200, PNG)

    with patch.object(hq_images, "_get", fake_get):
        content, ctype = fetch_hq_image("tok", DOMAIN, XFORM, NAME)
    assert ctype == "image/png" and len(urls) == 2 and "form_attachment/v1" in urls[1]


def test_fetch_retries_transient_failures(monkeypatch):
    monkeypatch.setattr(hq_images, "HQ_FETCH_BACKOFF_BASE", 0)
    seq = iter([httpx.ConnectError("boom"), _Resp(503), _Resp(200, JPEG)])

    def fake_get(url, tok):
        r = next(seq)
        if isinstance(r, Exception):
            raise r
        return r

    with patch.object(hq_images, "_get", fake_get):
        assert fetch_hq_image("tok", DOMAIN, XFORM, NAME)[0] == JPEG


# --------------------------------------------------------------------------- the view


@pytest.fixture
def user(django_user_model):
    return django_user_model(username="reviewer1", email="r@example.org")


def _get(user, path=None, domain=DOMAIN, connect_token="connect-tok", name=NAME, xform=XFORM):
    path = path or f"/audit/image/hq/{OPP}/{xform}/{name}/"
    request = RequestFactory().get(path, {"domain": domain} if domain else {})
    request.user = user
    request.session = {"labs_oauth": {"access_token": connect_token}} if connect_token else {}
    return ExperimentAuditImageHQView.as_view()(request, opp_id=OPP, xform_id=xform, name=name)


VIEW = "connect_labs.audit.views"
TOKEN = "connect_labs.labs.integrations.commcare.cchq_tokens.get_valid_cchq_access_token"
META = "connect_labs.labs.analysis.data_access.fetch_opportunity_metadata"
FETCH = "connect_labs.audit.hq_images._get"
OPP_META = {"cc_domain": DOMAIN, "raw": {"deliver_app": {"cc_domain": DOMAIN}}}


def test_view_is_non_atomic_and_login_required():
    view = resolve(f"/audit/image/hq/{OPP}/{XFORM}/{NAME}/").func
    assert "default" in getattr(view, "_non_atomic_requests", set())
    request = RequestFactory().get(f"/audit/image/hq/{OPP}/{XFORM}/{NAME}/")
    request.user = AnonymousUser()
    response = ExperimentAuditImageHQView.as_view()(request, opp_id=OPP, xform_id=XFORM, name=NAME)
    assert response.status_code == 302


def test_view_serves_a_real_jpeg_with_proxy_headers(user):
    with (
        patch(TOKEN, return_value="hq-tok"),
        patch(META, return_value=OPP_META),
        patch(FETCH, return_value=_Resp(200, JPEG)) as get,
        patch.object(connection, "close") as close,
    ):
        response = _get(user)
    assert response.status_code == 200
    assert response.content == JPEG
    assert response["Content-Type"] == "image/jpeg"
    assert response["Cache-Control"] == "private, max-age=3600"
    assert 'filename="photo_1.jpg"' in response["Content-Disposition"]
    # the user's token was used, and the connection was handed back before the fetch
    assert get.call_args[0][1] == "hq-tok"
    assert close.called


def test_view_closes_connection_before_upstream_fetch(user):
    order = []
    with (
        patch(TOKEN, return_value="t"),
        patch(META, return_value=OPP_META),
        patch(FETCH, side_effect=lambda *a: order.append("fetch") or _Resp(200, JPEG)),
        patch.object(connection, "close", side_effect=lambda: order.append("close")),
    ):
        _get(user)
    assert order.index("close") < order.index("fetch")


@pytest.mark.parametrize("body", [b'{"error": "Invalid API key"}', b"<html>sign in</html>", b""])
def test_view_never_serves_an_error_body_as_an_image(user, body):
    with (
        patch(TOKEN, return_value="t"),
        patch(META, return_value=OPP_META),
        patch(FETCH, return_value=_Resp(200, body)),
    ):
        response = _get(user)
    assert response.status_code == 502
    assert body not in response.content or body == b""
    assert json.loads(response.content)["error"] == "not_image"


def test_view_maps_hq_404_and_403(user):
    with patch(TOKEN, return_value="t"), patch(META, return_value=OPP_META), patch(FETCH, return_value=_Resp(404)):
        assert _get(user).status_code == 404
    with patch(TOKEN, return_value="t"), patch(META, return_value=OPP_META), patch(FETCH, return_value=_Resp(403)):
        assert _get(user).status_code == 403


def test_view_401_with_connect_link_when_user_has_no_hq_token(user):
    with patch(TOKEN, side_effect=CCHQTokenError("none")), patch(FETCH) as get:
        response = _get(user)
    assert response.status_code == 401
    body = json.loads(response.content)
    assert body["error"] == "commcare_hq_not_connected"
    assert body["connect_url"] == "/labs/commcare/initiate/"
    assert "CommCare HQ" in body["message"]
    assert not get.called


def test_view_refreshes_once_when_hq_rejects_the_token(user):
    class Row:
        access_token = "fresh"

    responses = iter([_Resp(401), _Resp(200, JPEG)])
    seen = []

    def fake_get(url, tok):
        seen.append(tok)
        return next(responses)

    with (
        patch(TOKEN, return_value="stale"),
        patch(META, return_value=OPP_META),
        patch(FETCH, fake_get),
        patch("connect_labs.labs.integrations.commcare.cchq_tokens.refresh_cchq_token", return_value=Row()) as refresh,
    ):
        response = _get(user)
    assert response.status_code == 200
    assert seen == ["stale", "fresh"]
    assert refresh.call_args.kwargs["stale_access_token"] == "stale"


def test_view_401_when_refresh_also_fails(user):
    from connect_labs.labs.integrations.commcare.cchq_tokens import CCHQReLoginRequired

    with (
        patch(TOKEN, return_value="stale"),
        patch(META, return_value=OPP_META),
        patch(FETCH, return_value=_Resp(401)),
        patch(
            "connect_labs.labs.integrations.commcare.cchq_tokens.refresh_cchq_token",
            side_effect=CCHQReLoginRequired("x"),
        ),
    ):
        response = _get(user)
    assert response.status_code == 401
    assert json.loads(response.content)["error"] == "commcare_hq_not_connected"


def test_view_refuses_a_domain_outside_the_opportunitys(user):
    with patch(TOKEN, return_value="t"), patch(META, return_value=OPP_META), patch(FETCH) as get:
        response = _get(user, domain="some-other-project")
    assert response.status_code == 403
    assert json.loads(response.content)["error"] == "domain_not_allowed"
    assert not get.called


def test_view_defaults_to_the_opportunitys_domain_without_a_query(user):
    with (
        patch(TOKEN, return_value="t"),
        patch(META, return_value=OPP_META),
        patch(FETCH, return_value=_Resp(200, JPEG)) as get,
    ):
        response = _get(user, domain="")
    assert response.status_code == 200
    assert f"/a/{DOMAIN}/" in get.call_args[0][0]


@pytest.mark.parametrize("name", ["../etc/passwd", "a/../b.jpg"])
def test_view_rejects_traversal_in_the_attachment_name(user, name):
    with patch(TOKEN, return_value="t"), patch(FETCH) as get:
        response = _get(user, name=name)
    assert response.status_code == 400
    assert not get.called


def test_view_rejects_bad_xform_id_and_domain(user):
    with patch(TOKEN, return_value="t"), patch(FETCH) as get:
        assert _get(user, xform="x/../y").status_code == 400
        assert _get(user, domain="a/b").status_code == 400
    assert not get.called


def test_view_needs_a_connect_session_to_resolve_the_domain(user):
    with patch(TOKEN, return_value="t"), patch(FETCH) as get:
        assert _get(user, connect_token=None).status_code == 401
    assert not get.called


def test_view_does_not_log_the_token(user, caplog):
    with (
        caplog.at_level("DEBUG"),
        patch(TOKEN, return_value="SECRET-HQ-TOKEN"),
        patch(META, return_value=OPP_META),
        patch(FETCH, return_value=_Resp(500)),
        patch.object(hq_images, "HQ_FETCH_BACKOFF_BASE", 0),
    ):
        _get(user)
    assert "SECRET-HQ-TOKEN" not in caplog.text
