"""The per-request CloudWatch EMF line (``request_telemetry.emit_route_metric``).

Two properties are load-bearing and pinned here:

* **The dimension set is closed.** EMF copies every top-level key of the line into
  the log event and every dimension combination is a billed metric, so a fourth key
  is both a cost and — if it is a path, id or username — a HIPAA leak into a stream
  the read-only observe role can see as metrics. Only ``route``, ``method`` and
  ``status_class`` may appear.
* **The route is the URL pattern TEMPLATE, never the raw path,** and anything that
  did not resolve collapses to ``unmatched``.

The requests go through Django's real handler (test client, real resolver) with
only the telemetry middleware installed, so ``resolver_match`` is set exactly the
way it is in production rather than faked.
"""

import json
import logging

import pytest
from django.test import Client, override_settings

from connect_labs.utils import request_telemetry

# The test client fires request_started/finished, which touch the connection.
pytestmark = pytest.mark.django_db

ALLOWED_DIMENSIONS = {"route", "method", "status_class"}
METRIC_NAMES = {"RequestLatency", "RequestCount"}


@pytest.fixture
def emf(caplog):
    """Capture the metrics stream, which deliberately does not propagate."""
    metrics_logger = logging.getLogger("connect_labs.telemetry.metrics")
    metrics_logger.addHandler(caplog.handler)
    previous = metrics_logger.level
    metrics_logger.setLevel(logging.INFO)
    yield lambda: [json.loads(r.message) for r in caplog.records if r.name == "connect_labs.telemetry.metrics"]
    metrics_logger.removeHandler(caplog.handler)
    metrics_logger.setLevel(previous)


@pytest.fixture
def client():
    with override_settings(
        ROOT_URLCONF="connect_labs.utils.tests.route_metric_urls",
        MIDDLEWARE=[
            "connect_labs.utils.request_telemetry.RequestTelemetryMiddleware",
        ],
    ):
        yield Client()


def _only(lines):
    assert len(lines) == 1, lines
    return lines[0]


def _declared_dimensions(line):
    (directive,) = line["_aws"]["CloudWatchMetrics"]
    dims = set()
    for dim_set in directive["Dimensions"]:
        dims.update(dim_set)
    return directive, dims


class TestDimensionSetIsClosed:
    def test_only_route_method_status_class_are_emitted(self, client, emf):
        client.get("/labs/workflow/4242/runs/secret-run/?opportunity_id=99&email=a@b.org")
        line = _only(emf())

        directive, declared = _declared_dimensions(line)
        assert declared == ALLOWED_DIMENSIONS
        assert directive["Namespace"] == "Labs/Requests"
        assert {m["Name"] for m in directive["Metrics"]} == METRIC_NAMES

        # Every top-level key ends up in the log event, so the key set itself is the
        # boundary: metric values, the allowed dimensions and the EMF envelope only.
        assert set(line) == ALLOWED_DIMENSIONS | METRIC_NAMES | {"_aws"}

    def test_no_user_derived_value_appears_anywhere_in_the_line(self, client, emf):
        client.get("/labs/workflow/4242/runs/secret-run/?opportunity_id=99&email=a@b.org")
        raw = json.dumps(_only(emf()))
        for leaked in ("4242", "secret-run", "opportunity_id", "99", "a@b.org"):
            assert leaked not in raw


class TestRouteIsTheTemplate:
    def test_id_in_url_becomes_the_pattern_not_the_value(self, client, emf):
        client.get("/labs/workflow/4242/")
        line = _only(emf())
        assert line["route"] == "labs/workflow/<int:workflow_id>/"
        assert line["method"] == "GET"
        assert line["status_class"] == "2xx"
        assert line["RequestCount"] == 1
        assert isinstance(line["RequestLatency"], int)

    def test_two_ids_share_one_route(self, client, emf):
        client.get("/labs/workflow/1/")
        client.get("/labs/workflow/2/")
        assert {line["route"] for line in emf()} == {"labs/workflow/<int:workflow_id>/"}

    def test_unresolved_path_is_unmatched(self, client, emf):
        client.get("/no/such/path/patient-123/")
        line = _only(emf())
        assert line["route"] == "unmatched"
        assert line["status_class"] == "4xx"
        assert "patient-123" not in json.dumps(line)

    def test_root_route_is_slash(self, client, emf):
        client.get("/")
        assert _only(emf())["route"] == "/"


class TestEveryRequestIsCounted:
    def test_fast_clean_request_still_emits(self, client, emf):
        """The diagnostic log is threshold-gated; the metric must not be."""
        client.get("/labs/workflow/7/")
        assert len(emf()) == 1

    def test_5xx_is_classed(self, client, emf, monkeypatch):
        # Django logs a 5xx on django.request, whose AdminEmailHandler is not what
        # this test is about (and rejects base.py's ADMINS shape outside labs_aws).
        monkeypatch.setattr("django.core.handlers.base.log_response", lambda *a, **k: None)
        client.get("/unavailable/")
        assert _only(emf())["status_class"] == "5xx"

    def test_unknown_method_is_bucketed(self, client, emf):
        client.generic("BREW", "/labs/workflow/7/")
        assert _only(emf())["method"] == "OTHER"

    def test_kill_switch(self, client, emf, monkeypatch):
        monkeypatch.setattr(request_telemetry, "ROUTE_METRICS_ENABLED", False)
        client.get("/labs/workflow/7/")
        assert emf() == []


class TestLineShape:
    def test_line_is_a_bare_json_object(self, client, emf, caplog):
        """CloudWatch only extracts EMF from a log event that IS the JSON object."""
        client.get("/labs/workflow/7/")
        (record,) = (r for r in caplog.records if r.name == "connect_labs.telemetry.metrics")
        assert record.getMessage().startswith("{") and record.getMessage().endswith("}")

    def test_production_formatter_is_message_only(self):
        from django.conf import settings

        handler_name = settings.LOGGING["loggers"]["connect_labs.telemetry.metrics"]["handlers"]
        assert handler_name == ["audit_stream"]
        formatter = settings.LOGGING["handlers"]["audit_stream"]["formatter"]
        assert settings.LOGGING["formatters"][formatter]["format"] == "%(message)s"
