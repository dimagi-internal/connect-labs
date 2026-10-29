# Subscription filter on /ecs/labs-jj-web -> /labs-jj/observe/request-telemetry.
# Per event type, keeps only allow-listed keys: route TEMPLATES, fixed vocabularies,
# config ids and numbers. Drops path, username, outbound hosts, opportunity ids and
# everything else. A request line with no `route` is dropped, never backfilled from
# the path. top_requester keeps its requester only as a staff name or a u:<hash>,
# both decided in the app. Inlined into the template by render.py.
import base64
import gzip
import json
import os
import re
import time
import uuid

import boto3

logs = boto3.client("logs")
MEM = ("rss_kb", "rss_delta_kb", "maxrss_kb", "maxrss_rise_kb", "rss_peak_kb", "rss_peak_rise_kb")
MS = ("duration_ms", "db_ms", "outbound_ms", "self_ms", "cpu_ms", "retry_wait_ms")
# event -> (required, string keys, numeric/bool keys)
SPEC = {
    "slow_request": (
        "route",
        ("route", "method", "reason"),
        ("status", "sampled", "streaming", "db_queries", "outbound_calls", "retry_waits") + MS + MEM,
    ),
    "stream_end": (
        "route",
        ("route", "method", "outcome"),
        ("status", "duration_ms", "chunks", "bytes", "sampled") + MEM,
    ),
    "pipeline_stream": (
        "phase",
        ("phase", "stream_id", "outcome"),
        ("definition_id", "pipeline_id", "elapsed_ms", "total_rows", "rows", "step", "steps", "target", "targets")
        + ("from_cache", "merged_rows", "failed", "pid")
        + MEM,
    ),
    "top_requester": (
        "top_route",
        ("top_route", "top_user_agent_class"),
        ("window_s", "pid", "requests", "anonymous_requests", "distinct_requesters", "top_requests")
        + ("top_share_pct", "top_route_requests"),
    ),
}
HASHED = re.compile(r"u:[0-9a-f]{12}")


def scrub(line):
    try:
        rec = json.loads(line)
    except ValueError:
        return None
    if not isinstance(rec, dict):
        return None
    event = rec.get("event")
    if event not in SPEC:
        return None
    need, strs, nums = SPEC[event]
    if not isinstance(rec.get(need), str):
        return None
    out = {"event": event}
    out.update({k: rec[k] for k in strs if isinstance(rec.get(k), str) and len(rec[k]) <= 200})
    out.update({k: rec[k] for k in nums if isinstance(rec.get(k), (int, float))})
    who, kind = rec.get("top_requester"), rec.get("top_requester_kind")
    if event == "top_requester" and isinstance(who, str) and len(who) <= 150:
        if (kind == "hashed" and HASHED.fullmatch(who)) or kind == "staff":
            out["top_requester"], out["top_requester_kind"] = who, kind
    return out


def handler(ev, ctx):
    data = json.loads(gzip.decompress(base64.b64decode(ev["awslogs"]["data"])))
    if data.get("messageType") != "DATA_MESSAGE":
        return {"written": 0}
    events = []
    for e in data.get("logEvents") or []:
        rec = scrub(e.get("message", ""))
        if rec is not None:
            events.append({"timestamp": e["timestamp"], "message": json.dumps(rec, separators=(",", ":"))})
    if events:
        group = os.environ["GROUP"]
        stream = time.strftime("%Y/%m/%d/") + uuid.uuid4().hex
        logs.create_log_stream(logGroupName=group, logStreamName=stream)
        events.sort(key=lambda x: x["timestamp"])
        logs.put_log_events(logGroupName=group, logStreamName=stream, logEvents=events)
    return {"written": len(events)}
