"""The form player's JSON API against the fake Formplayer (and the fake PersonalID / Connect / HQ).

What matters most here: the browser can never choose whose data or which app (username, domain,
app id and token come from the server), form sessions are scoped, inputs are validated, and
failures come back as plain-language JSON with the right status.
"""

import json

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from connect_labs.connect_web import service
from connect_labs.connect_web.models import PoolWorker

from . import fake_formplayer, fakes

pytestmark = pytest.mark.django_db

OPP = fakes.OPP_UUID
SESSION_ID = fake_formplayer.fixture("form")["session_id"]
LEARN_USER = "pooluserone@nets-learn.commcarehq.org"


@pytest.fixture
def acting(client, staff, pool_worker):
    client.force_login(staff)
    service.check_out(staff, pool_worker)
    return client


def _url(name, kind="learn", opp=OPP):
    return reverse(f"connect_web:api_{name}", args=[opp, kind])


def post(client, name, body=None, kind="learn", opp=OPP):
    return client.post(_url(name, kind, opp), data=json.dumps(body or {}), content_type="application/json")


def _open_form(client, kind="learn"):
    response = post(client, "navigate", {"selections": ["0", "1"]}, kind=kind)
    assert response.status_code == 200, response.content
    assert response.json()["session_id"] == SESSION_ID
    return response.json()


def _claim(servers):
    servers.opportunities = [fakes.opportunity(claim={"max_payments": 20, "date_claimed": "2026-11-05"})]


# -- scoping: the server decides who and what ----------------------------------------------


def test_navigate_start_runs_the_released_build_as_the_worker(acting, servers):
    response = post(acting, "navigate")
    assert response.status_code == 200
    assert response.json()["type"] == "commands"
    body = servers.formplayer.body_for("navigate_menu_start")[-1]
    assert body["username"] == LEARN_USER
    assert body["domain"] == "nets-learn"
    # The released build id from HQ's profile, never Connect's cc_app_id.
    assert body["app_id"] == fakes.BUILD_IDS["learnapp123"] != "learnapp123"
    request = servers.formplayer.requests[-1]
    assert request.headers["X-Labs-HQ-Token"] == "hq-1"


def test_the_client_cannot_pick_the_user_domain_or_app(acting, servers):
    response = post(
        acting,
        "navigate",
        {
            "selections": ["0"],
            "username": "admin@other.commcarehq.org",
            "domain": "other-domain",
            "app_id": "someone-elses-app",
            "restoreAs": "victim",
            "hq_token": "stolen",
        },
    )
    assert response.status_code == 200
    body = servers.formplayer.body_for("navigate_menu")[-1]
    assert body["username"] == LEARN_USER and body["domain"] == "nets-learn"
    assert body["app_id"] == fakes.BUILD_IDS["learnapp123"]
    assert "restoreAs" not in body and "hq_token" not in body
    assert servers.formplayer.requests[-1].headers["X-Labs-HQ-Token"] == "hq-1"


def test_deliver_runs_the_deliver_app_once_claimed(acting, servers):
    _claim(servers)
    assert post(acting, "navigate", kind="deliver").status_code == 200
    body = servers.formplayer.body_for("navigate_menu_start")[-1]
    assert body["domain"] == "nets-deliver" and body["username"] == "pooluserone@nets-deliver.commcarehq.org"
    assert body["app_id"] == fakes.BUILD_IDS["deliverapp456"]


def test_deliver_before_a_claim_is_refused_without_calling_the_engine(acting, servers):
    response = post(acting, "navigate", kind="deliver")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "NOT_CLAIMED"
    assert servers.formplayer.requests == []


def test_an_opportunity_the_worker_does_not_have_is_refused(acting, servers):
    response = post(acting, "navigate", opp="00000000-0000-4000-8000-00000000dead")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "NO_OPPORTUNITY_ACCESS"
    assert servers.formplayer.requests == []


def test_an_unknown_app_kind_is_a_404(acting, servers):
    assert post(acting, "navigate", kind="bogus").status_code == 404


def test_the_app_target_is_cached_briefly(acting, servers):
    post(acting, "navigate")
    lists = len(servers.calls("/api/opportunity/"))
    post(acting, "navigate", {"selections": ["0"]})
    assert len(servers.calls("/api/opportunity/")) == lists
    assert servers.profiles_served == 1


# -- form sessions ----------------------------------------------------------------------------


def test_a_session_labs_opened_can_be_answered(acting, servers):
    _open_form(acting)
    response = post(acting, "answer", {"session_id": SESSION_ID, "ix": "1", "answer": "Amina Yusuf"})
    assert response.status_code == 200
    assert response.json()["status"] == "accepted" and "instanceXml" not in response.json()
    body = servers.formplayer.body_for("answer")[-1]
    assert body == {
        "username": LEARN_USER,
        "domain": "nets-learn",
        "session_id": SESSION_ID,
        "ix": "1",
        "answer": "Amina Yusuf",
    }


def test_a_session_labs_did_not_open_is_refused(acting, servers):
    response = post(acting, "answer", {"session_id": SESSION_ID, "ix": "1", "answer": "x"})
    assert response.status_code == 410
    assert response.json()["error"]["code"] == "SESSION_UNKNOWN"
    assert servers.formplayer.body_for("answer") == []


def test_a_learn_session_cannot_be_driven_through_the_deliver_app(acting, servers):
    _claim(servers)
    _open_form(acting, "learn")
    response = post(acting, "answer", {"session_id": SESSION_ID, "ix": "1", "answer": "x"}, kind="deliver")
    assert response.status_code == 410 and response.json()["error"]["code"] == "SESSION_UNKNOWN"


def test_a_session_the_engine_has_lost_says_start_again(acting, servers):
    _open_form(acting)
    servers.formplayer.open_sessions.clear()
    response = post(acting, "answer", {"session_id": SESSION_ID, "ix": "1", "answer": "x"})
    assert response.status_code == 410
    assert response.json()["error"]["code"] == "SESSION_EXPIRED"
    assert "Start the form again" in response.json()["error"]["message"]


def test_another_labs_user_cannot_use_my_session(acting, servers, other_staff):
    _open_form(acting)
    second = PoolWorker.objects.create(phone_number="+74260000002", display_name="Test Worker 2", backup_code="123456")
    other = Client()
    other.force_login(other_staff)
    service.check_out(other_staff, second)
    response = post(other, "answer", {"session_id": SESSION_ID, "ix": "1", "answer": "x"})
    assert response.status_code == 410 and response.json()["error"]["code"] == "SESSION_UNKNOWN"
    assert servers.formplayer.body_for("answer") == []


# -- input validation -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, body",
    [
        ("navigate", {"selections": "0"}),
        ("navigate", {"selections": ["../../x"]}),
        ("navigate", {"offset": -1, "selections": ["0"]}),
        ("navigate", {"cases_per_page": 1000, "selections": ["0"]}),
        ("navigate", {"tz_offset_millis": "soon"}),
        ("answer", {"session_id": SESSION_ID, "ix": "1;DROP", "answer": "x"}),
        ("answer", {"session_id": SESSION_ID, "ix": "1", "answer": {"nested": True}}),
        ("answer", {"session_id": "nope", "ix": "1", "answer": "x"}),
        ("submit", {"session_id": SESSION_ID, "answers": {"1": "x"}, "meta_location": "somewhere"}),
        ("submit", {"session_id": SESSION_ID, "answers": "all of them"}),
        ("details", {"selections": []}),
        ("change_locale", {"session_id": SESSION_ID, "locale": "en; rm"}),
    ],
)
def test_bad_input_is_a_400_and_nothing_is_sent(acting, servers, name, body):
    _open_form(acting)
    sent = len(servers.formplayer.bodies)
    response = post(acting, name, body)
    assert response.status_code == 400, response.content
    assert response.json()["error"]["code"] == "INVALID_INPUT"
    assert len(servers.formplayer.bodies) == sent


def test_a_body_that_is_not_json_is_a_400(acting, servers):
    response = acting.post(_url("navigate"), data="{nope", content_type="application/json")
    assert response.status_code == 400
    response = acting.post(_url("navigate"), data="[1, 2]", content_type="application/json")
    assert response.status_code == 400


def test_only_post(acting):
    assert acting.get(_url("navigate")).status_code == 405


# -- each endpoint ---------------------------------------------------------------------------


def test_case_list_paging_and_search_are_passed_on(acting, servers):
    post(acting, "navigate", {"selections": ["0"], "offset": 10, "search_text": "Amina", "cases_per_page": 10})
    body = servers.formplayer.body_for("navigate_menu")[-1]
    assert body["selections"] == ["0"] and body["offset"] == 10
    assert body["search_text"] == "Amina" and body["cases_per_page"] == 10


def test_details(acting, servers):
    case_id = "8c1a9f4e-1111-4b5e-9f00-000000000001"
    response = post(acting, "details", {"selections": ["0", case_id]})
    assert response.status_code == 200 and response.json()["details"][0]["title"] == "Household"
    assert servers.formplayer.body_for("get_details")[-1]["selections"] == ["0", case_id]


def test_repeats_and_language(acting, servers):
    _open_form(acting)
    for name, endpoint, extra in (
        ("new_repeat", "new-repeat", {"ix": "15_1"}),
        ("delete_repeat", "delete-repeat", {"ix": "15_0"}),
        ("change_locale", "change_locale", {"locale": "fra"}),
    ):
        response = post(acting, name, {"session_id": SESSION_ID, **extra})
        assert response.status_code == 200, name
        sent = servers.formplayer.body_for(endpoint)[-1]
        assert sent["session_id"] == SESSION_ID and all(sent[k] == v for k, v in extra.items())


def test_submit_sends_answers_and_the_browser_location(acting, servers):
    _open_form(acting)
    response = post(
        acting,
        "submit",
        {
            "session_id": SESSION_ID,
            "answers": {"1": "Amina", "9": 1, "10": [1, 3], "14,0": [9.07, 7.49]},
            "meta_location": "9.0765 7.4983 512 12.5",
            "prevalidated": False,
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert "Thanks for submitting" in response.json()["submitResponseMessage"]
    body = servers.formplayer.body_for("submit-all")[-1]
    assert body["meta_location"] == "9.0765 7.4983 512 12.5"
    assert body["prevalidated"] is True
    assert body["answers"] == {"1": "Amina", "9": 1, "10": [1, 3], "14,0": [9.07, 7.49]}


def test_submit_without_location_sends_none(acting, servers):
    _open_form(acting)
    assert post(acting, "submit", {"session_id": SESSION_ID, "answers": {}}).status_code == 200
    assert "meta_location" not in servers.formplayer.body_for("submit-all")[-1]


@pytest.mark.parametrize("fixture_name", ["submit_error", "submit_validation_error"])
def test_a_refused_submission_comes_back_as_a_result(acting, servers, fixture_name):
    _open_form(acting)
    servers.formplayer.responses["submit-all"] = fixture_name
    response = post(acting, "submit", {"session_id": SESSION_ID, "answers": {}})
    assert response.status_code == 200
    assert response.json()["status"] == fake_formplayer.fixture(fixture_name)["status"]


def test_sync(acting, servers):
    response = post(acting, "sync")
    assert response.status_code == 200 and response.json() == {"status": "accepted"}
    assert servers.formplayer.body_for("sync-db")[-1]["app_id"] == fakes.BUILD_IDS["learnapp123"]


# -- photos -----------------------------------------------------------------------------------


def _upload(client, content, *, name="photo.jpg", ix="16", session_id=SESSION_ID):
    from django.core.files.uploadedfile import SimpleUploadedFile

    return client.post(
        _url("answer_media"),
        {"session_id": session_id, "ix": ix, "file": SimpleUploadedFile(name, content, content_type="image/jpeg")},
    )


def test_a_photo_is_forwarded_as_answer_media(acting, servers):
    _open_form(acting)
    jpeg = b"\xff\xd8\xff\xe0" + b"\x01" * 2048
    response = _upload(acting, jpeg, name="IMG_0001.HEIC.jpg")
    assert response.status_code == 200, response.content
    assert servers.formplayer.uploads == [("photo.jpg", jpeg, "image/jpeg")]
    assert servers.formplayer.body_for("answer_media")[-1]["ix"] == "16"


def test_a_file_that_is_not_a_photo_is_refused(acting, servers):
    _open_form(acting)
    response = _upload(acting, b"<svg onload=alert(1)>", name="x.jpg")
    assert response.status_code == 400 and "JPEG or PNG" in response.json()["error"]["message"]
    assert servers.formplayer.uploads == []


def test_a_photo_over_the_limit_is_refused(acting, servers, settings):
    _open_form(acting)
    settings.CONNECT_WEB_MEDIA_MAX_BYTES = 1024
    response = _upload(acting, b"\xff\xd8\xff" + b"\x00" * 2000)
    assert response.status_code == 400 and "too large" in response.json()["error"]["message"]
    assert servers.formplayer.uploads == []


def test_a_photo_needs_a_file(acting, servers):
    _open_form(acting)
    response = acting.post(_url("answer_media"), {"session_id": SESSION_ID, "ix": "16"})
    assert response.status_code == 400


# -- failures ---------------------------------------------------------------------------------


def test_engine_not_configured(acting, servers, settings):
    settings.CONNECT_WEB_FORMPLAYER_URL = ""
    response = post(acting, "navigate")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ENGINE_NOT_CONFIGURED"
    assert servers.formplayer.requests == []


def test_a_key_mismatch_is_reported_as_configuration(acting, servers, settings):
    settings.CONNECT_WEB_FORMPLAYER_KEY = "wrong"
    response = post(acting, "navigate")
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "ENGINE_AUTH_FAILED"
    assert "CONNECT_WEB_FORMPLAYER_KEY" in response.json()["error"]["message"]


def test_an_engine_error_is_shown_in_plain_language(acting, servers):
    servers.formplayer.responses["navigate_menu"] = "engine_app_error"
    response = post(acting, "navigate", {"selections": ["0"]})
    assert response.status_code == 502
    assert response.json()["error"]["message"].startswith("The form engine reported a problem: XPath")


def test_an_engine_busy_is_retryable(acting, servers):
    servers.formplayer.responses["navigate_menu"] = (423, {"exception": "User lock timed out", "status": "error"})
    response = post(acting, "navigate", {"selections": ["0"]})
    assert response.status_code == 503 and response.json()["error"]["retryable"] is True


def test_no_released_build(acting, servers):
    servers.profile_status = 404
    response = post(acting, "navigate")
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "NO_RELEASED_BUILD"


# -- who may call -----------------------------------------------------------------------------


def test_anonymous_gets_json_401(client):
    response = post(client, "navigate")
    assert response.status_code == 401 and response.json()["error"]["code"] == "SIGNED_OUT"


def test_outsider_gets_json_403(client, outsider):
    client.force_login(outsider)
    assert post(client, "navigate").status_code == 403


def test_no_worker_checked_out_is_a_409(client, staff):
    client.force_login(staff)
    response = post(client, "navigate")
    assert response.status_code == 409 and response.json()["error"]["code"] == "NO_WORKER"


def test_csrf_is_enforced(staff, pool_worker, servers):
    client = Client(enforce_csrf_checks=True)
    client.force_login(staff)
    service.check_out(staff, pool_worker)
    assert post(client, "navigate").status_code == 403
    page = client.get(reverse("connect_web:form_entry", args=[OPP, "learn"]))
    token = page.context["player_config"]["csrfToken"]
    response = client.post(
        _url("navigate"), data="{}", content_type="application/json", headers={"X-CSRFToken": token}
    )
    assert response.status_code == 200


def test_no_token_or_password_ever_reaches_the_browser(acting, servers):
    responses = [
        post(acting, "navigate"),
        post(acting, "navigate", {"selections": ["0", "1"]}),
        post(acting, "answer", {"session_id": SESSION_ID, "ix": "1", "answer": "x"}),
        post(acting, "submit", {"session_id": SESSION_ID, "answers": {}}),
        acting.get(reverse("connect_web:form_entry", args=[OPP, "learn"])),
    ]
    for response in responses:
        text = response.content.decode()
        assert "hq-1" not in text and "pid-access" not in text and "rotated-password" not in text
        assert fake_formplayer.KEY not in text


# -- progress ---------------------------------------------------------------------------------


def test_progress(acting, servers):
    response = acting.get(reverse("connect_web:api_progress", args=[OPP]))
    assert response.status_code == 200
    data = response.json()
    assert data["learn"]["modules_done"] == 1 and data["learn"]["modules_total"] == 2
    assert data["claimed"] is False and data["deliver"]["visits"] == 0
    _claim(servers)
    cache.clear()  # claimed elsewhere (a phone), and the list's TTL has passed
    data = acting.get(reverse("connect_web:api_progress", args=[OPP])).json()
    assert data["deliver"]["visits"] == 2 and data["deliver"]["statuses"] == {"approved": 1, "pending": 1}
    assert acting.post(reverse("connect_web:api_progress", args=[OPP])).status_code == 405


@pytest.mark.parametrize("kind", ["learn", "deliver"])
def test_progress_for_one_kind_is_one_fresh_connect_call(acting, servers, kind):
    _claim(servers)
    url = reverse("connect_web:api_progress", args=[OPP])
    acting.get(url)  # warms the list (as the opportunity page has)
    servers.requests.clear()
    data = acting.get(f"{url}?kind={kind}").json()
    assert [r.url.path for r in servers.connect_calls()] == [
        f"/api/opportunity/{OPP}/{'learn_progress' if kind == 'learn' else 'delivery_progress'}"
    ]
    if kind == "learn":
        assert data["learn"]["modules_done"] == 1 and data["deliver"] is None
    else:
        assert data["deliver"]["visits"] == 2 and data["learn"] is None
    # Fresh every time: it is the poll for something Connect hasn't counted yet.
    servers.learn_progress["completed_modules"].append({"module": 2})
    acting.get(f"{url}?kind={kind}")
    assert len(servers.connect_calls()) == 2


def test_a_constraint_error_comes_back_as_a_result(acting, servers):
    _open_form(acting)
    response = post(acting, "answer", {"session_id": SESSION_ID, "ix": "2", "answer": 99})
    assert response.status_code == 200
    assert response.json() == {**fake_formplayer.fixture("answer_constraint")}
    response = post(acting, "answer", {"session_id": SESSION_ID, "ix": "2", "answer": 5})
    assert response.json()["status"] == "accepted"
    assert [q for q in response.json()["tree"] if q["ix"] == "2"][0]["answer"] == 5
