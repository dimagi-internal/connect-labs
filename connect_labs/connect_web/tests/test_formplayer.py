"""The Formplayer bridge: signing (exact bytes), the build-id resolver, error mapping, input validation."""

import base64
import hashlib
import hmac
import json

import httpx
import pytest

from connect_labs.connect_web import formplayer, http
from connect_labs.connect_web.errors import (
    ENGINE_AUTH_FAILED,
    ENGINE_BUSY,
    ENGINE_ERROR,
    NO_RELEASED_BUILD,
    FormplayerError,
    FormSessionExpired,
    HQError,
    InvalidInput,
    NotConfigured,
)

from . import fake_formplayer, fakes

TARGET = formplayer.AppTarget(
    opportunity_id=fakes.OPP_UUID,
    app_kind="learn",
    hq_base=fakes.HQ,
    domain="nets-learn",
    cc_app_id="learnapp123",
    build_id=fakes.BUILD_IDS["learnapp123"],
    hq_username="pooluserone@nets-learn.commcarehq.org",
)


def _fp(**kwargs):
    return formplayer.Formplayer(TARGET, "hq-token-abc", **kwargs)


# -- signing ----------------------------------------------------------------------------------


def test_sign_is_padded_base64_hmac_sha256_of_the_exact_bytes():
    body = b'{"username":"u","domain":"d"}'
    expected = base64.b64encode(hmac.new(b"secret", body, hashlib.sha256).digest()).decode()
    assert formplayer.sign("secret", body) == expected
    # Pinned, so a change to the algorithm or the encoding can't slip through.
    assert formplayer.sign("secret", body) == "PAhZ/Qnwg7B3QF2RwhsQDhwjZNaGxNTOO+ikqlnUKDM="
    assert formplayer.sign("secret", body).endswith("=")
    assert formplayer.sign("secret", body + b" ") != expected


def test_a_call_sends_the_signed_bytes_and_the_hq_token(servers):
    result = formplayer.navigate(_fp(tz_offset_millis=3600000, tz_from_browser="Africa/Lagos"), [])
    assert result["type"] == "commands"
    request = servers.formplayer.requests[-1]
    assert str(request.url) == f"{fakes.FORMPLAYER}/navigate_menu_start"
    assert request.headers["Content-Type"] == "application/json"
    assert request.headers["X-Labs-HQ-Token"] == "hq-token-abc"
    assert request.headers["X-Labs-Signature"] == formplayer.sign(fake_formplayer.KEY, request.content)
    assert request.content == (
        b'{"username":"pooluserone@nets-learn.commcarehq.org","domain":"nets-learn",'
        b'"tz_offset_millis":3600000,"tz_from_browser":"Africa/Lagos",'
        b'"app_id":"' + fakes.BUILD_IDS["learnapp123"].encode() + b'","preview":false}'
    )
    assert "Cookie" not in request.headers and "X-MAC-DIGEST" not in request.headers


def test_non_ascii_is_signed_as_the_utf8_sent(servers):
    fp = _fp()
    formplayer.answer(fp, "6a0c8f2e-6b1d-4c4e-9a52-3f1d2b7c9e10", "1", "Ngozi Ọ̀kọ́")
    request = servers.formplayer.requests[-1]
    assert "Ngozi Ọ̀kọ́".encode() in request.content
    assert request.headers["X-Labs-Signature"] == formplayer.sign(fake_formplayer.KEY, request.content)


def test_the_caller_can_never_choose_the_user_or_domain(servers):
    fp = _fp()
    fp.call("navigate_menu", {"username": "someone@else", "domain": "other", "restoreAs": "x", "restoreAsCaseId": "y"})
    body = servers.formplayer.body_for("navigate_menu")[-1]
    assert body["username"] == TARGET.hq_username and body["domain"] == "nets-learn"
    assert "restoreAs" not in body and "restoreAsCaseId" not in body


def test_a_wrong_key_is_refused_by_the_engine(servers, settings):
    settings.CONNECT_WEB_FORMPLAYER_KEY = "not-the-engine-key"
    with pytest.raises(FormplayerError) as exc:
        formplayer.navigate(_fp(), [])
    assert exc.value.error_code == ENGINE_AUTH_FAILED and exc.value.status == 401
    assert "hq-token-abc" not in str(exc.value) and "not-the-engine-key" not in str(exc.value)


def test_answer_media_signs_only_the_json_part(servers):
    jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 100
    formplayer.answer_media(_fp(), "6a0c8f2e-6b1d-4c4e-9a52-3f1d2b7c9e10", "16", content=jpeg)
    request = servers.formplayer.requests[-1]
    parts = fake_formplayer.multipart_parts(request)
    content_type, signed = parts["answer"]
    assert content_type == "application/json"
    assert request.headers["X-Labs-Signature"] == formplayer.sign(fake_formplayer.KEY, signed)
    assert json.loads(signed)["ix"] == "16" and json.loads(signed)["username"] == TARGET.hq_username
    assert servers.formplayer.uploads == [("photo.jpg", jpeg, "image/jpeg")]


def test_not_configured_refuses_before_sending(settings, servers):
    settings.CONNECT_WEB_FORMPLAYER_KEY = ""
    assert not formplayer.is_configured()
    with pytest.raises(NotConfigured):
        formplayer.require_configured()
    settings.CONNECT_WEB_FORMPLAYER_KEY = "k"
    settings.CONNECT_WEB_FORMPLAYER_URL = "  "
    assert not formplayer.is_configured()


def test_the_client_repr_hides_the_token():
    assert "hq-token-abc" not in repr(_fp())


# -- the build id -----------------------------------------------------------------------------

PROFILE = fake_formplayer.fixture("media_profile").encode()


@pytest.mark.parametrize(
    "profile, build_id",
    [
        (PROFILE, "0b5e4d2a9c7f4e1b8a3d6c5f2e1a9b8c"),
        (
            b'<profile><property key="PostURL" value="https://h/a/d/receiver/secure/abcdef0123456789/"/></profile>',
            "abcdef0123456789",
        ),
        (
            b'<profile><property value="https://h/a/d/receiver/abcdef0123456789" key="PostURL"/></profile>',
            "abcdef0123456789",
        ),
        (
            b'<profile><property key="PostURL" value="https://h/a/d/receiver/secure/abcdef0123456789/"',
            "abcdef0123456789",
        ),
        (b"<profile><property key='other' value='x'/></profile>", None),
        (b'<profile><property key="PostURL" value="https://h/a/d/receiver/"/></profile>', None),
        (b'<!DOCTYPE p [<!ENTITY x "y">]><profile><property key="PostURL" value="&x;"/></profile>', None),
        (b"not xml at all", None),
        (b"", None),
        (b"<profile>" + b" " * (600 * 1024) + b"</profile>", None),
    ],
)
def test_parse_build_id(profile, build_id):
    assert formplayer.parse_build_id(profile) == build_id


def test_resolve_build_id_reads_the_latest_release_and_caches_it(servers):
    build = formplayer.resolve_build_id(fakes.HQ, "nets-learn", "learnapp123")
    assert build == fakes.BUILD_IDS["learnapp123"]
    assert formplayer.resolve_build_id(fakes.HQ, "nets-learn", "learnapp123") == build
    assert servers.profiles_served == 1
    request = servers.calls("/a/nets-learn/apps/download/learnapp123/media_profile.ccpr")[0]
    assert request.method == "GET" and request.url.params["latest"] == "true"
    assert "Authorization" not in request.headers


def test_resolve_build_id_without_a_release_is_a_clear_error(servers):
    with pytest.raises(HQError) as exc:
        formplayer.resolve_build_id(fakes.HQ, "nets-learn", "unknown-app")
    assert exc.value.error_code == NO_RELEASED_BUILD
    assert "no released build" in exc.value.user_message


def test_resolve_build_id_with_no_post_url(servers, monkeypatch):
    monkeypatch.setattr(fake_formplayer, "FIXTURES", {**fake_formplayer.FIXTURES, "media_profile": "<profile/>"})
    with pytest.raises(HQError) as exc:
        formplayer.resolve_build_id(fakes.HQ, "nets-learn", "learnapp123")
    assert exc.value.error_code == NO_RELEASED_BUILD


# -- what Formplayer's answers mean ------------------------------------------------------------


def _response(status=200, body=None, text=None):
    if text is not None:
        return httpx.Response(status, text=text)
    return httpx.Response(status, json=body)


def test_parse_response_passes_a_result_through_without_instance_xml():
    body = formplayer.parse_response("answer", _response(body=fake_formplayer.fixture("answer_accepted")))
    assert body["status"] == "accepted" and "instanceXml" not in body


def test_a_refused_submission_is_a_result_not_an_exception():
    body = formplayer.parse_response("submit-all", _response(body=fake_formplayer.fixture("submit_error")))
    assert body["status"] == "error" and "403 Forbidden" in body["notification"]["message"]


@pytest.mark.parametrize(
    "response, error, code",
    [
        (_response(401, text="Labs auth failed: invalid signature"), FormplayerError, ENGINE_AUTH_FAILED),
        (_response(423, body={"exception": "User lock timed out", "status": "error"}), FormplayerError, ENGINE_BUSY),
        (_response(body=fake_formplayer.fixture("engine_session_gone")), FormSessionExpired, "SESSION_EXPIRED"),
        (_response(body=fake_formplayer.fixture("engine_app_error")), FormplayerError, ENGINE_ERROR),
        (_response(500, text="<html>boom</html>"), FormplayerError, ENGINE_ERROR),
        (_response(200, text="not json"), FormplayerError, ""),
    ],
)
def test_parse_response_errors(response, error, code):
    with pytest.raises(error) as exc:
        formplayer.parse_response("navigate_menu", response)
    assert exc.value.error_code == code


def test_an_engine_error_shows_its_reason_cut_short():
    body = fake_formplayer.fixture("engine_app_error")
    body["exception"] = "XPath problem " + "x" * 1000
    with pytest.raises(FormplayerError) as exc:
        formplayer.parse_response("navigate_menu", _response(body=body))
    assert exc.value.user_message.startswith("The form engine reported a problem: XPath problem")
    assert len(exc.value.detail) == FormplayerError.max_detail


def test_an_unreachable_engine(monkeypatch):
    def boom(request):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(http, "HTTP_TRANSPORT", httpx.MockTransport(boom))
    with pytest.raises(FormplayerError) as exc:
        formplayer.navigate(_fp(), [])
    assert exc.value.status is None and "Couldn't reach the form engine" in exc.value.user_message


# -- validating the browser's input -----------------------------------------------------------


@pytest.mark.parametrize("ix", ["0", "12", "1,0", "13,1", "15_0", "15_0,0", "3_1,2_0,4", "5_-10", "5J", ">", "<"])
def test_valid_ix(ix):
    assert formplayer.clean_ix(ix) == ix


@pytest.mark.parametrize("ix", ["", None, 3, "a", "1;2", "1,,2", "../etc", "1" * 50, "0 OR 1", ["0"]])
def test_invalid_ix(ix):
    with pytest.raises(InvalidInput):
        formplayer.clean_ix(ix)


def test_selections():
    assert formplayer.clean_selections(None) == []
    assert formplayer.clean_selections(["0", 1, "8c1a9f4e-1111-4b5e-9f00-000000000001", "action 0"]) == [
        "0",
        "1",
        "8c1a9f4e-1111-4b5e-9f00-000000000001",
        "action 0",
    ]
    for bad in ("0", [{"a": 1}], ["0"] * 31, ["x" * 200], ["../.."], [""], [True]):
        with pytest.raises(InvalidInput):
            formplayer.clean_selections(bad)


def test_answers():
    assert formplayer.clean_answer(None) is None
    assert formplayer.clean_answer(3) == 3
    assert formplayer.clean_answer("2026-11-25") == "2026-11-25"
    assert formplayer.clean_answer([1, 3]) == [1, 3]
    assert formplayer.clean_answer([9.05, 7.48]) == [9.05, 7.48]
    for bad in ({"a": 1}, [[1]], [None], "x" * 20_001, float("nan"), list(range(1001))):
        with pytest.raises(InvalidInput):
            formplayer.clean_answer(bad)
    assert formplayer.clean_answers({"1": "a", "13,0": 2}) == {"1": "a", "13,0": 2}
    with pytest.raises(InvalidInput):
        formplayer.clean_answers({"bad ix": 1})
    with pytest.raises(InvalidInput):
        formplayer.clean_answers(["1"])


def test_session_ids():
    assert formplayer.clean_session_id("6a0c8f2e-6b1d-4c4e-9a52-3f1d2b7c9e10")
    for bad in (None, "", "short", "has space in it here", "x" * 65, "../../../etc/passwd", 5):
        with pytest.raises(InvalidInput):
            formplayer.clean_session_id(bad)


@pytest.mark.parametrize("value", ["9.0765 7.4983", "-9.0765 7.4983 512.3 12", "0 0 0", "9.0765 7.4983 512 4.5"])
def test_valid_meta_location(value):
    assert formplayer.clean_meta_location(value) == value


@pytest.mark.parametrize("value", ["91 0", "0 181", "abc", "1,2", "1 2 3 4 5", "1  2", "NaN NaN", 5, ["1", "2"]])
def test_invalid_meta_location(value):
    with pytest.raises(InvalidInput):
        formplayer.clean_meta_location(value)


def test_no_meta_location():
    assert formplayer.clean_meta_location(None) is None and formplayer.clean_meta_location("") is None


def test_timezone_and_paging():
    assert formplayer.clean_timezone(-18000000, "America/New_York") == (-18000000, "America/New_York")
    assert formplayer.clean_timezone(None, None) == (None, None)
    for offset, name in ((10**12, None), (None, "Robert'); DROP"), ("x", None)):
        with pytest.raises(InvalidInput):
            formplayer.clean_timezone(offset, name)
    assert formplayer.clean_int("20", low=0, high=100, name="n") == 20
    with pytest.raises(InvalidInput):
        formplayer.clean_int(101, low=0, high=100, name="n")
    assert formplayer.clean_search("Amina") == "Amina"
    with pytest.raises(InvalidInput):
        formplayer.clean_search("a\x00b")


def test_photos_are_checked_by_their_bytes_and_size(settings):
    assert formplayer.clean_image(b"\xff\xd8\xff\xe0rest") == ("image/jpeg", "photo.jpg")
    assert formplayer.clean_image(b"\x89PNG\r\n\x1a\nrest") == ("image/png", "photo.png")
    for bad in (b"", b"GIF89a....", b"%PDF-1.4", b"<svg onload=alert(1)>"):
        with pytest.raises(InvalidInput):
            formplayer.clean_image(bad)
    settings.CONNECT_WEB_MEDIA_MAX_BYTES = 1000
    with pytest.raises(InvalidInput):
        formplayer.clean_image(b"\xff\xd8\xff" + b"\x00" * 1000)
    # Never above Formplayer's own limit, whatever the setting says.
    settings.CONNECT_WEB_MEDIA_MAX_BYTES = 50 * 1024 * 1024
    assert formplayer.media_max_bytes() == formplayer.FORMPLAYER_MEDIA_MAX_BYTES
