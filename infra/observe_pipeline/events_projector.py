# Projects EventBridge events into /labs-jj/observe/* keeping ONLY allow-listed
# fields (labs-observe-pipeline.yml). Inlined into the template by render.py.
import json
import os
import time
import uuid

import boto3

logs = boto3.client("logs")
_streams = {}  # group -> this container's stream: one CreateLogStream per cold start, not per event

# CloudWatch Logs' own write calls. Writing a record is one of these, and every
# one is a CloudTrail event the api-calls rule delivers back here: projecting
# them made each write trigger the next (2026-10-29, ~590k invocations/hour).
_SELF_FEEDING = {"CreateLogStream", "PutLogEvents"}


def _g(d, *path):
    for p in path:
        d = d.get(p) if isinstance(d, dict) else None
    return d


def _tail(arn):
    return str(arn or "").rsplit("/", 1)[-1] or None


def project(ev):
    """(log group key, record) for one event, or None to drop it."""
    d, src, kind = ev.get("detail") or {}, ev.get("source"), ev.get("detail-type")
    if src == "aws.ecs" and kind == "ECS Task State Change":
        return "ecs", {
            "kind": "task_stopped",
            "time": ev.get("time"),
            "cluster": _tail(d.get("clusterArn")),
            "group": d.get("group"),
            "task_definition": _tail(d.get("taskDefinitionArn")),
            "stop_code": d.get("stopCode"),
            "stopped_reason": d.get("stoppedReason"),
            "started_at": d.get("startedAt"),
            "stopped_at": d.get("stoppedAt"),
            "containers": [
                {"name": c.get("name"), "exit_code": c.get("exitCode"), "reason": c.get("reason")}
                for c in d.get("containers") or []
            ],
        }
    if src == "aws.ecs" and kind == "ECS Deployment State Change":
        return "ecs", {
            "kind": "deployment",
            "time": ev.get("time"),
            "event": d.get("eventName"),
            "deployment_id": d.get("deploymentId"),
            "reason": d.get("reason"),
            "service": _tail((ev.get("resources") or [None])[0]),
        }
    if src == "aws.health":
        return "health", {
            "kind": "aws_health",
            "time": ev.get("time"),
            "service": d.get("service"),
            "event_type": d.get("eventTypeCode"),
            "category": d.get("eventTypeCategory"),
            "status": d.get("statusCode"),
            "start": d.get("startTime"),
            "end": d.get("endTime"),
        }
    if kind == "AWS API Call via CloudTrail":
        if d.get("eventSource") == "logs.amazonaws.com" and d.get("eventName") in _SELF_FEEDING:
            return None
        return "api", {
            "kind": "api_call",
            "time": d.get("eventTime"),
            "service": d.get("eventSource"),
            "event": d.get("eventName"),
            "principal": _g(d, "userIdentity", "arn"),
            "source_identity": _g(d, "userIdentity", "sessionContext", "sourceIdentity"),
            "error_code": d.get("errorCode"),
            "read_only": d.get("readOnly"),
            "source_ip": d.get("sourceIPAddress"),
        }
    return None


def handler(ev, ctx):
    out = project(ev)
    if out is None:
        return {"written": 0}
    group = os.environ["GROUP_" + out[0].upper()]
    stream = _streams.get(group)
    if stream is None:
        stream = time.strftime("%Y/%m/%d/") + uuid.uuid4().hex
        logs.create_log_stream(logGroupName=group, logStreamName=stream)
        _streams[group] = stream
    logs.put_log_events(
        logGroupName=group,
        logStreamName=stream,
        logEvents=[{"timestamp": int(time.time() * 1000), "message": json.dumps(out[1], separators=(",", ":"))}],
    )
    return {"written": 1}
