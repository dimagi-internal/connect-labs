"""Sentry enrichment: who did it, and what we refuse to report.

Most of these drive ``before_send`` directly — the hook is the whole contract,
and calling it in isolation keeps them independent of whether a DSN is
configured for the environment under test. The last two go through a real SDK
client with a collecting transport, because the claim being made ("a user
reaches Sentry even with ``send_default_pii=False``") is a claim about the
serialized envelope, including everything the PII scrubber does to it.
"""

import pytest

from connect_labs.audit_trail.context import AuditContext, audit_context, reset_audit_context, set_audit_context
from connect_labs.audit_trail.models import Source
from connect_labs.utils.sentry import before_breadcrumb, before_send, before_send_transaction, scrub_event, strip_query


def _exception_event(exc_type: str, value: str, logger: str = "connect_labs.thing") -> dict:
    return {
        "logger": logger,
        "exception": {"values": [{"type": exc_type, "value": value}]},
    }


# --- noise filtering --------------------------------------------------------


def test_fastmcp_tool_error_is_dropped():
    """FastMCP logs an ERROR for every ToolError we raise on purpose. Genuine
    failures are reported separately by mcp.server with a real traceback."""
    event = _exception_event("ToolError", "No workflow with id 6719", logger="fastmcp.server.server")
    assert before_send(event, {}) is None


def test_tool_error_from_our_own_logger_is_kept():
    event = _exception_event("ToolError", "boom", logger="connect_labs.mcp.server")
    assert before_send(event, {}) is not None


def test_websocket_rejection_is_dropped():
    event = _exception_event(
        "ValueError",
        "Django can only handle ASGI/HTTP connections, not websocket.",
        logger="django.request",
    )
    assert before_send(event, {}) is None


def test_unrelated_value_error_is_kept():
    event = _exception_event("ValueError", "invalid literal for int()")
    assert before_send(event, {}) is not None


def test_noise_matching_uses_hint_exception_when_present():
    exc = ValueError("Django can only handle ASGI/HTTP connections, not websocket.")
    assert before_send({"logger": "django.request"}, {"exc_info": (ValueError, exc, None)}) is None


# --- attribution ------------------------------------------------------------


def test_event_is_unattributed_without_a_context():
    event = before_send(_exception_event("RuntimeError", "x"), {})
    assert "user" not in event


@pytest.mark.django_db
def test_user_and_scope_tags_from_audit_context(user):
    with audit_context(user=user, source=Source.MCP, request_id="mcp:abc", path="mcp:workflow_get"):
        event = before_send(_exception_event("RuntimeError", "x"), {})

    # Id only — username and email are deliberately withheld (see module
    # docstring). An exact-equality assert is the point here: it fails if
    # anyone widens the payload.
    assert event["user"] == {"id": str(user.pk)}
    assert event["tags"]["labs.source"] == Source.MCP
    assert event["tags"]["labs.request_id"] == "mcp:abc"
    assert event["tags"]["labs.path"] == "mcp:workflow_get"


@pytest.mark.django_db
def test_web_request_user_is_resolved_lazily(rf, user):
    """The middleware stores the request, not the user — request.user is a lazy
    object at that point. Attribution has to resolve it at capture time."""
    request = rf.get("/labs/overview/")
    request.user = user
    request.labs_context = {"opportunity_id": 765, "program_id": 25}
    ctx = AuditContext(source=Source.WEB, buffer=[], request=request)
    token = set_audit_context(ctx)
    try:
        event = before_send(_exception_event("RuntimeError", "x"), {})
    finally:
        reset_audit_context(token)

    assert event["user"]["id"] == str(user.pk)
    assert event["tags"]["labs.opportunity_id"] == "765"
    assert event["tags"]["labs.program_id"] == "25"


@pytest.mark.django_db
def test_anonymous_request_yields_no_user(rf):
    from django.contrib.auth.models import AnonymousUser

    request = rf.get("/")
    request.user = AnonymousUser()
    ctx = AuditContext(source=Source.WEB, buffer=[], request=request)
    token = set_audit_context(ctx)
    try:
        event = before_send(_exception_event("RuntimeError", "x"), {})
    finally:
        reset_audit_context(token)

    assert "user" not in event
    assert event["tags"]["labs.source"] == Source.WEB


@pytest.mark.django_db
def test_existing_user_on_event_is_not_clobbered(user):
    event = _exception_event("RuntimeError", "x")
    event["user"] = {"id": "999"}
    with audit_context(user=user, source=Source.CELERY):
        result = before_send(event, {})
    assert result["user"]["id"] == "999"


@pytest.mark.django_db
def test_enrichment_failure_still_delivers_the_event(user, monkeypatch):
    """A broken transaction can make resolving request.user raise. An
    unattributed event beats a swallowed one."""
    monkeypatch.setattr(
        "connect_labs.audit_trail.service.resolve_context_user",
        lambda ctx: (_ for _ in ()).throw(RuntimeError("db is gone")),
    )
    with audit_context(user=user, source=Source.CELERY):
        event = before_send(_exception_event("RuntimeError", "x"), {})
    assert event is not None


# --- end-to-end through a real SDK client ------------------------------------


@pytest.fixture
def captured_events():
    """A live Sentry client whose transport collects envelopes in a list."""
    import sentry_sdk
    from sentry_sdk.transport import Transport

    events = []

    class _Collecting(Transport):
        def capture_envelope(self, envelope):
            for item in envelope.items:
                if item.type == "event":
                    events.append(item.payload.json)

    client = sentry_sdk.Client(
        dsn="https://public@example.com/1",
        before_send=before_send,
        before_breadcrumb=before_breadcrumb,
        include_local_variables=False,
        transport=_Collecting(),
        default_integrations=False,
        auto_enabling_integrations=False,
    )
    scope = sentry_sdk.get_global_scope()
    previous = scope.client
    scope.set_client(client)
    try:
        yield events
    finally:
        scope.set_client(previous)


@pytest.mark.django_db
def test_captured_exception_reaches_sentry_with_a_user(captured_events, user):
    """The regression this whole module exists for: issues showing Users: 0."""
    import sentry_sdk

    with audit_context(user=user, source=Source.MCP, path="mcp:workflow_get"):
        try:
            raise RuntimeError("kaboom")
        except RuntimeError:
            sentry_sdk.capture_exception()

    (event,) = captured_events
    assert event["user"] == {"id": str(user.pk)}
    assert event["tags"]["labs.path"] == "mcp:workflow_get"


@pytest.mark.django_db
def test_pii_scrubber_does_not_strip_the_explicit_user(captured_events, user):
    """send_default_pii stays off; the scrubber must not eat identity with it."""
    import sentry_sdk

    with audit_context(user=user, source=Source.WEB):
        sentry_sdk.capture_message("hello")

    (event,) = captured_events
    assert event["user"] == {"id": str(user.pk)}


@pytest.mark.django_db
def test_no_email_or_username_reaches_sentry(captured_events, user):
    """The email is known to the audit context and must not leave with the
    event — asserted on the serialized envelope, not just before_send."""
    import json

    import sentry_sdk

    assert user.email, "fixture must have an email for this to prove anything"

    with audit_context(user=user, source=Source.WEB):
        sentry_sdk.capture_message("hello")

    (event,) = captured_events
    assert "email" not in event["user"]
    assert user.email not in json.dumps(event, default=str)
    assert user.username not in json.dumps(event, default=str)


@pytest.mark.django_db
def test_local_variables_and_breadcrumb_queries_never_reach_sentry(captured_events, user):
    """End to end: a frame local holding programme data and an httpx-style log
    breadcrumb with a query string, through the real serializer."""
    import json

    import sentry_sdk

    flw = "amina" + ".k"  # runtime-built, as above
    sentry_sdk.add_breadcrumb(
        category="httpx",
        message=f'HTTP Request: GET https://connect.dimagi.com/api/visits/?flw={flw}&page=2 "HTTP/1.1 200 OK"',
        data={"url": f"https://connect.dimagi.com/api/visits/?flw={flw}", "http.query": f"flw={flw}"},
    )

    def failing():
        # Built at runtime: the source line itself ships as stack context.
        beneficiary_name = "SECRET-" + "BENEFICIARY"  # noqa: F841 - the local under test
        raise RuntimeError("visit failed")

    with audit_context(user=user, source=Source.CELERY, path="connect_labs.tasks.sync"):
        try:
            failing()
        except RuntimeError:
            sentry_sdk.capture_exception()

    (event,) = captured_events
    blob = json.dumps(event, default=str)
    assert "SECRET-BENEFICIARY" not in blob
    assert "amina.k" not in blob
    crumb = event["breadcrumbs"]["values"][-1]
    assert crumb["data"]["url"] == "https://connect.dimagi.com/api/visits/"


# --- scrubber units ---------------------------------------------------------


def test_strip_query_in_free_text():
    text = "GET https://h.example/api/x/?flw=amina&visit=9 failed; then /labs/y/?q=z#frag ok"
    assert strip_query(text) == "GET https://h.example/api/x/?[Filtered] failed; then /labs/y/?[Filtered] ok"
    assert strip_query("no urls here? none.") == "no urls here? none."
    assert strip_query(42) == 42


def _web_event() -> dict:
    return {
        "request": {
            "url": "https://labs.connect.dimagi.com/labs/flw/amina.k/visits/?case=abc#x",
            "method": "GET",
            "query_string": "case=abc",
            "data": {"name": "beneficiary"},
            "cookies": {"sessionid": "s"},
            "headers": {
                "User-Agent": "Mozilla",
                "Content-Type": "text/html",
                "Cookie": "sessionid=s",
                "Authorization": "Bearer t",
                "X-Csrftoken": "c",
                "Referer": "https://labs.connect.dimagi.com/x/?q=1",
                "X-Forwarded-For": "1.2.3.4",
            },
            "env": {"SERVER_NAME": "labs", "SERVER_PORT": "443", "REMOTE_ADDR": "1.2.3.4"},
        },
        "user": {"id": "7", "username": "amina.k", "email": "a@x.org", "ip_address": "1.2.3.4"},
        "extra": {
            "celery-job": {"task_name": "t.sync", "args": [{"form": 1}], "kwargs": {"visit": 2}},
            "form_json": {"q": 1},
            "reason": "see https://h/x/?token=abc",
        },
        "logentry": {"message": "fetch %s", "params": ["https://h/x/?q=1"], "formatted": "fetch https://h/x/?q=1"},
        "exception": {
            "values": [
                {
                    "type": "ValueError",
                    "value": "bad url https://h/x/?id=1",
                    "stacktrace": {"frames": [{"function": "f", "vars": {"visit": "SECRET"}}]},
                }
            ]
        },
        "breadcrumbs": {"values": [{"message": "GET /labs/a/?q=SECRET", "data": {"url": "/labs/a/?q=SECRET"}}]},
        "tags": {"labs.opportunity_id": "765"},
    }


def test_scrub_event_strips_request_content():
    event = scrub_event(_web_event(), template="/labs/flw/<str:username>/visits/")
    request = event["request"]
    assert "query_string" not in request
    assert "data" not in request
    assert "cookies" not in request
    assert request["url"] == "https://labs.connect.dimagi.com/labs/flw/<str:username>/visits/"
    assert request["headers"] == {"User-Agent": "Mozilla", "Content-Type": "text/html"}
    assert request["env"] == {"SERVER_NAME": "labs", "SERVER_PORT": "443"}
    assert request["method"] == "GET"


def test_scrub_event_without_template_still_drops_query():
    event = scrub_event(_web_event())
    assert event["request"]["url"] == "https://labs.connect.dimagi.com/labs/flw/amina.k/visits/"


def test_scrub_event_strips_user_extra_messages_frames_and_crumbs():
    import json

    event = scrub_event(_web_event())
    assert event["user"] == {"id": "7"}
    assert event["extra"]["celery-job"] == {"task_name": "t.sync"}
    assert event["extra"]["form_json"] == "[Filtered]"
    assert event["extra"]["reason"] == "see https://h/x/?[Filtered]"
    assert event["logentry"]["formatted"] == "fetch https://h/x/?[Filtered]"
    assert event["logentry"]["params"] == []
    value = event["exception"]["values"][0]
    assert value["value"] == "bad url https://h/x/?[Filtered]"
    assert value["type"] == "ValueError"
    assert "vars" not in value["stacktrace"]["frames"][0]
    assert value["stacktrace"]["frames"][0]["function"] == "f"
    assert "SECRET" not in json.dumps(event)
    assert event["tags"] == {"labs.opportunity_id": "765"}


def test_user_without_id_is_dropped():
    assert "user" not in scrub_event({"user": {"ip_address": "1.2.3.4"}})


def test_before_breadcrumb_strips_http_query():
    crumb = {
        "type": "http",
        "category": "httpx",
        "data": {
            "url": "https://connect.dimagi.com/api/x/?flw=a",
            "http.query": "flw=a",
            "http.fragment": "f",
            "method": "GET",
            "status_code": 200,
        },
    }
    out = before_breadcrumb(crumb, {})
    assert out["data"] == {"url": "https://connect.dimagi.com/api/x/", "method": "GET", "status_code": 200}


def test_before_breadcrumb_strips_urls_in_log_messages():
    crumb = {"category": "httpx", "message": 'HTTP Request: GET https://c/api/?flw=a "HTTP/1.1 200 OK"'}
    assert before_breadcrumb(crumb, {})["message"] == 'HTTP Request: GET https://c/api/?[Filtered] "HTTP/1.1 200 OK"'


def test_before_send_transaction_scrubs_request_and_spans():
    event = {
        "type": "transaction",
        "transaction": "/labs/workflow/<int:pk>/",
        "request": {"url": "https://h/labs/workflow/5/?x=1", "query_string": "x=1"},
        "spans": [
            {
                "op": "http.client",
                "description": "GET https://c/api/?flw=a",
                "data": {"url": "https://c/api/?flw=a", "http.query": "flw=a"},
            }
        ],
    }
    out = before_send_transaction(event, {})
    assert "query_string" not in out["request"]
    assert out["request"]["url"] == "https://h/labs/workflow/5/"
    assert out["spans"][0]["description"] == "GET https://c/api/?[Filtered]"
    assert out["spans"][0]["data"] == {"url": "https://c/api/"}
    assert out["transaction"] == "/labs/workflow/<int:pk>/"


def test_scrub_failure_drops_the_event(monkeypatch):
    """Scrubbing fails closed: an unscrubbed event is never sent."""

    def boom(event, template=""):
        raise RuntimeError("scrubber bug")

    monkeypatch.setattr("connect_labs.utils.sentry.scrub_event", boom)
    assert before_send(_exception_event("RuntimeError", "x"), {}) is None


@pytest.mark.django_db
def test_web_labs_path_is_the_route_template(rf, user):
    """request.path can carry record ids and worker usernames; the tag must
    name the route, never the record."""
    from django.urls import ResolverMatch

    request = rf.get("/labs/flw/amina.k/visits/?case=abc")
    request.user = user
    request.resolver_match = ResolverMatch(
        lambda r: None, (), {"username": "amina.k"}, route="labs/flw/<str:username>/visits/"
    )
    ctx = AuditContext(source=Source.WEB, buffer=[], request=request, path=request.path)
    token = set_audit_context(ctx)
    try:
        event = before_send(_exception_event("RuntimeError", "x"), {})
    finally:
        reset_audit_context(token)

    assert event["tags"]["labs.path"] == "/labs/flw/<str:username>/visits/"


@pytest.mark.django_db
def test_unresolved_web_request_has_no_labs_path(rf, user):
    request = rf.get("/labs/flw/amina.k/")
    request.user = user
    ctx = AuditContext(source=Source.WEB, buffer=[], request=request, path=request.path)
    token = set_audit_context(ctx)
    try:
        event = before_send(_exception_event("RuntimeError", "x"), {})
    finally:
        reset_audit_context(token)

    assert "labs.path" not in event["tags"]
