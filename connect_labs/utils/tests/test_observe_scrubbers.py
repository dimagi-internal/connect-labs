"""The scrubbers that feed /labs-jj/observe/* (infra/observe_pipeline/).

The observe role (infra/labs-observe-readonly.yml) may read those log groups and no
raw log, so each scrubber is the ONLY thing between PHI and an agent. Pinned here:
every record a scrubber writes has exactly its allow-listed keys, and a sentinel
planted in every disallowed field (task commands, request parameters, paths,
usernames, client IPs, user agents, query strings) never survives. Also pinned: the
template's inlined ZipFile code is these files, byte for byte, and fits ZipFile's
4096-byte limit.
"""

from __future__ import annotations

import base64
import gzip
import importlib.util
import io
import json
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
PIPE = ROOT / "infra" / "observe_pipeline"
SENTINEL = "SENSITIVE-SENTINEL"


class FakeLogs:
    def __init__(self):
        self.streams, self.events = [], []

    def create_log_stream(self, logGroupName, logStreamName):
        self.streams.append((logGroupName, logStreamName))

    def put_log_events(self, logGroupName, logStreamName, logEvents):
        self.events += [(logGroupName, json.loads(e["message"])) for e in logEvents]


class FakeS3:
    def __init__(self, body: bytes):
        self.body = body

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.body)}


def _load(name, monkeypatch, s3_body=b""):
    fake_logs, fake_s3 = FakeLogs(), FakeS3(s3_body)
    boto3 = types.ModuleType("boto3")
    boto3.client = lambda svc, **kw: {"logs": fake_logs, "s3": fake_s3}[svc]
    monkeypatch.setitem(sys.modules, "boto3", boto3)
    spec = importlib.util.spec_from_file_location(f"observe_{name}", PIPE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, fake_logs


def _no_sentinel(obj):
    assert SENTINEL not in json.dumps(obj), obj


# ── events projector ────────────────────────────────────────────────────────

TASK_STOPPED = {
    "source": "aws.ecs",
    "detail-type": "ECS Task State Change",
    "time": "2026-10-29T12:00:00Z",
    "detail": {
        "clusterArn": "arn:aws:ecs:us-east-1:1:cluster/labs-jj-cluster",
        "group": "family:labs-jj-web",
        "taskDefinitionArn": "arn:aws:ecs:us-east-1:1:task-definition/labs-jj-web:412",
        "lastStatus": "STOPPED",
        "stopCode": "EssentialContainerExited",
        "stoppedReason": "Essential container in task exited",
        "startedAt": "2026-10-29T11:00:00Z",
        "stoppedAt": "2026-10-29T12:00:00Z",
        "overrides": {
            "containerOverrides": [
                {
                    "name": "web",
                    "command": ["python", "manage.py", "shell", "-c", f"Visit.objects.get(name='{SENTINEL}')"],
                    "environment": [{"name": "X", "value": SENTINEL}],
                }
            ]
        },
        "containers": [
            {
                "name": "web",
                "exitCode": 137,
                "reason": "OutOfMemoryError: Container killed",
                "networkInterfaces": [{"privateIpv4Address": SENTINEL}],
                "image": SENTINEL,
            }
        ],
        "attachments": [{"details": [{"name": "privateIPv4Address", "value": SENTINEL}]}],
    },
}

API_CALL = {
    "source": "aws.ecs",
    "detail-type": "AWS API Call via CloudTrail",
    "time": "2026-10-29T12:00:00Z",
    "detail": {
        "eventTime": "2026-10-29T12:00:00Z",
        "eventSource": "ecs.amazonaws.com",
        "eventName": "RunTask",
        "userIdentity": {
            "arn": "arn:aws:sts::1:assumed-role/github-actions-labs-deploy/x",
            "sessionContext": {"sourceIdentity": "canopy-agent-hal"},
        },
        "sourceIPAddress": "203.0.113.9",
        "readOnly": False,
        "errorCode": "AccessDenied",
        "errorMessage": f"not allowed to run {SENTINEL}",
        "requestParameters": {"overrides": {"containerOverrides": [{"command": [SENTINEL]}]}},
        "responseElements": {"tasks": [{"overrides": SENTINEL}]},
    },
}


def test_task_stopped_keeps_only_the_allowed_fields(monkeypatch):
    mod, _ = _load("events_projector", monkeypatch)
    key, rec = mod.project(TASK_STOPPED)
    assert key == "ecs"
    assert set(rec) == {
        "kind",
        "time",
        "cluster",
        "group",
        "task_definition",
        "stop_code",
        "stopped_reason",
        "started_at",
        "stopped_at",
        "containers",
    }
    assert rec["task_definition"] == "labs-jj-web:412"
    assert rec["containers"] == [{"name": "web", "exit_code": 137, "reason": "OutOfMemoryError: Container killed"}]
    _no_sentinel(rec)


def test_api_call_keeps_who_what_error_and_never_parameters(monkeypatch):
    mod, _ = _load("events_projector", monkeypatch)
    key, rec = mod.project(API_CALL)
    assert key == "api"
    assert set(rec) == {
        "kind",
        "time",
        "service",
        "event",
        "principal",
        "source_identity",
        "error_code",
        "read_only",
        "source_ip",
    }
    assert rec["source_identity"] == "canopy-agent-hal" and rec["error_code"] == "AccessDenied"
    _no_sentinel(rec)


def test_deployment_and_health_and_unknown(monkeypatch):
    mod, _ = _load("events_projector", monkeypatch)
    key, rec = mod.project(
        {
            "source": "aws.ecs",
            "detail-type": "ECS Deployment State Change",
            "resources": ["arn:aws:ecs:us-east-1:1:service/labs-jj-cluster/labs-jj-web"],
            "detail": {
                "eventName": "SERVICE_DEPLOYMENT_FAILED",
                "deploymentId": "ecs-svc/1",
                "reason": "rolled back",
                "extra": SENTINEL,
            },
        }
    )
    assert key == "ecs" and rec["service"] == "labs-jj-web"
    _no_sentinel(rec)
    key, rec = mod.project(
        {
            "source": "aws.health",
            "detail-type": "AWS Health Event",
            "detail": {
                "service": "EC2",
                "eventTypeCode": "AWS_EC2_OPERATIONAL_ISSUE",
                "affectedEntities": [{"entityValue": SENTINEL}],
            },
        }
    )
    assert key == "health"
    _no_sentinel(rec)
    assert mod.project({"source": "aws.s3", "detail-type": "Object Created", "detail": {"x": SENTINEL}}) is None


def test_projector_handler_writes_to_the_mapped_group(monkeypatch):
    monkeypatch.setenv("GROUP_ECS", "/labs-jj/observe/ecs-events")
    mod, logs = _load("events_projector", monkeypatch)
    assert mod.handler(TASK_STOPPED, None) == {"written": 1}
    ((group, rec),) = logs.events
    assert group == "/labs-jj/observe/ecs-events"
    _no_sentinel(rec)


def test_projector_drops_cloudwatch_logs_own_writes(monkeypatch):
    # Its own CreateLogStream/PutLogEvents come back through the api-calls rule;
    # projecting them re-invoked it in a loop (2026-10-29).
    mod, _ = _load("events_projector", monkeypatch)
    for name in ("CreateLogStream", "PutLogEvents"):
        ev = {**API_CALL, "detail": {**API_CALL["detail"], "eventSource": "logs.amazonaws.com", "eventName": name}}
        assert mod.project(ev) is None


def test_projector_handler_creates_one_stream_per_container(monkeypatch):
    monkeypatch.setenv("GROUP_ECS", "/labs-jj/observe/ecs-events")
    mod, logs = _load("events_projector", monkeypatch)
    for _ in range(3):
        assert mod.handler(TASK_STOPPED, None) == {"written": 1}
    assert len(logs.streams) == 1 and len(logs.events) == 3


def test_api_calls_rule_excludes_cloudwatch_logs_writes():
    import yaml

    class _L(yaml.SafeLoader):
        pass

    _L.add_multi_constructor("!", lambda loader, suffix, node: None)
    tpl = yaml.load((pathlib.Path(__file__).resolve().parents[3] / "infra/labs-observe-pipeline.yml").read_text(), Loader=_L)
    for branch in tpl["Resources"]["ApiCallsRule"]["Properties"]["EventPattern"]["$or"]:
        assert branch["detail"]["eventName"] == [{"anything-but": ["CreateLogStream", "PutLogEvents"]}]


# ── request telemetry ───────────────────────────────────────────────────────


def _telemetry_line(**over):
    line = {
        "event": "slow_request",
        "reason": "slow",
        "method": "GET",
        "path": f"/labs/workflow/4242/{SENTINEL}/",
        "route": "labs/workflow/<int:workflow_id>/",
        "status": 200,
        "duration_ms": 12000,
        "db_ms": 300,
        "outbound_ms": 9000,
        "self_ms": 2700,
        "cpu_ms": 150,
        "retry_wait_ms": 0,
        "outbound_calls": 139,
        "outbound_by_host": {f"{SENTINEL}.example": 139},
        "username": SENTINEL,
        "sampled": False,
    }
    line.update(over)
    return json.dumps(line)


def test_telemetry_keeps_route_method_status_and_latency_only(monkeypatch):
    mod, _ = _load("request_telemetry_scrubber", monkeypatch)
    rec = mod.scrub(_telemetry_line())
    assert set(rec) == {
        "route",
        "method",
        "status",
        "duration_ms",
        "db_ms",
        "outbound_ms",
        "self_ms",
        "cpu_ms",
        "retry_wait_ms",
    }
    assert rec["route"] == "labs/workflow/<int:workflow_id>/"
    _no_sentinel(rec)


def test_telemetry_without_route_is_dropped_not_backfilled(monkeypatch):
    mod, _ = _load("request_telemetry_scrubber", monkeypatch)
    line = json.loads(_telemetry_line())
    del line["route"]
    assert mod.scrub(json.dumps(line)) is None
    assert mod.scrub("not json " + SENTINEL) is None
    assert mod.scrub(_telemetry_line(route=None)) is None


def test_telemetry_handler_decodes_the_subscription_payload(monkeypatch):
    monkeypatch.setenv("GROUP", "/labs-jj/observe/request-telemetry")
    mod, logs = _load("request_telemetry_scrubber", monkeypatch)
    payload = {
        "messageType": "DATA_MESSAGE",
        "logEvents": [
            {"id": "1", "timestamp": 1, "message": _telemetry_line()},
            {"id": "2", "timestamp": 2, "message": f"ERROR 2026 x y {SENTINEL}"},
        ],
    }
    data = base64.b64encode(gzip.compress(json.dumps(payload).encode())).decode()
    assert mod.handler({"awslogs": {"data": data}}, None) == {"written": 1}
    _no_sentinel(logs.events)


# ── ALB access logs ─────────────────────────────────────────────────────────


def _alb_line(path_and_query):
    return (
        "https 2026-10-29T12:00:01.123456Z app/labs-jj-alb/abc 198.51.100.7:51234 10.0.1.5:8000 "
        f'0.001 1.234 0.000 200 200 512 2048 "GET https://labs.connect.dimagi.com:443{path_and_query} HTTP/1.1" '
        f'"Mozilla/5.0 {SENTINEL}" ECDHE-RSA-AES128-GCM-SHA256 TLSv1.2 '
        "arn:aws:elasticloadbalancing:us-east-1:1:targetgroup/labs-jj-web-tg/0123456789abcdef "
        f'"Root=1-{SENTINEL}" "labs.connect.dimagi.com" "arn:aws:acm:us-east-1:1:certificate/x" 0 '
        '2026-10-29T12:00:00.000000Z "forward" "-" "-" "10.0.1.5:8000" "200" "-" "-"'
    )


@pytest.mark.parametrize(
    "path,route",
    [
        ("/labs/workflow/4242/", "/labs/workflow/{id}/"),
        ("/audit/image/3f2b9c1e-1234-4bcd-9f00-1a2b3c4d5e6f/", "/audit/image/{id}/"),
        ("/users/jsmith/visits/", "/users/{id}/visits/"),
        ("/api/a.person@example.org/", "/api/{id}/"),
        ("/share/abcdefghijklmnopqrstuvwxyzABCDEF/", "/share/{id}/"),
        ("/", "/"),
    ],
)
def test_alb_route_templates_every_identifying_segment(monkeypatch, path, route):
    mod, _ = _load("alb_scrubber", monkeypatch)
    assert mod.route(path) == route


def test_alb_line_keeps_only_allowed_fields(monkeypatch):
    mod, _ = _load("alb_scrubber", monkeypatch)
    rec = mod.scrub(_alb_line(f"/labs/workflow/4242/?username={SENTINEL}&opp=9"))
    assert set(rec) == {
        "time",
        "target_group",
        "method",
        "route",
        "elb_status",
        "target_status",
        "request_s",
        "target_s",
        "response_s",
        "received_bytes",
        "sent_bytes",
        "target",
    }
    assert rec["route"] == "/labs/workflow/{id}/" and rec["target_group"] == "labs-jj-web-tg"
    assert rec["target_s"] == 1.234 and rec["elb_status"] == 200 and rec["sent_bytes"] == 2048
    blob = json.dumps(rec)
    for leaked in (SENTINEL, "198.51.100.7", "username", "Mozilla", "labs.connect.dimagi.com"):
        assert leaked not in blob


def test_alb_handler_reads_the_object_and_drops_bad_lines(monkeypatch):
    monkeypatch.setenv("GROUP", "/labs-jj/observe/alb-requests")
    body = gzip.compress("\n".join([_alb_line("/x/1/"), f"garbage {SENTINEL}"]).encode())
    mod, logs = _load("alb_scrubber", monkeypatch, s3_body=body)
    out = mod.handler(
        {"detail": {"bucket": {"name": "labs-jj-alb-access-logs"}, "object": {"key": "alb/AWSLogs/1/x.log.gz"}}}, None
    )
    assert out == {"written": 1}
    _no_sentinel(logs.events)


# ── the template carries exactly these files ────────────────────────────────


def test_template_zipfiles_are_these_sources_and_fit():
    spec = importlib.util.spec_from_file_location("observe_render", PIPE / "render.py")
    render = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(render)
    text = render.TEMPLATE.read_text()
    assert render.rendered(text) == text, "run: python infra/observe_pipeline/render.py"
    for name in render.SOURCES:
        assert render.embedded(text, name) == (PIPE / name).read_text()
        assert len((PIPE / name).read_bytes()) <= render.MAX_BYTES


def test_every_scrubbed_group_is_under_the_observe_prefix():
    text = (ROOT / "infra" / "labs-observe-pipeline.yml").read_text()
    import re

    groups = re.findall(r"LogGroupName: (/labs-jj/[^,}\s]+)", text)
    assert groups and all(g.startswith("/labs-jj/observe/") for g in groups)
