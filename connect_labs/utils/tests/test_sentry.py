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
from connect_labs.utils.sentry import before_send


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

    assert event["user"] == {"id": str(user.pk), "username": user.username, "email": user.email}
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
    assert event["user"]["id"] == str(user.pk)
    assert event["user"]["username"] == user.username
    assert event["tags"]["labs.path"] == "mcp:workflow_get"


@pytest.mark.django_db
def test_pii_scrubber_does_not_strip_the_explicit_user(captured_events, user):
    """send_default_pii stays off; the scrubber must not eat identity with it."""
    import sentry_sdk

    with audit_context(user=user, source=Source.WEB):
        sentry_sdk.capture_message("hello")

    (event,) = captured_events
    assert event["user"]["id"] == str(user.pk)
    assert event["user"]["email"] == user.email
