# Subscription filter on /ecs/labs-jj-web -> /labs-jj/observe/request-telemetry.
# Keeps route TEMPLATE, method, status and latency numbers; drops path, username,
# outbound hosts and everything else. A line with no `route` is dropped, never
# backfilled from the path. Inlined into the template by render.py.
import base64
import gzip
import json
import os
import time
import uuid

import boto3

logs = boto3.client("logs")
KEEP = ("route", "method", "status")
MS = ("duration_ms", "db_ms", "outbound_ms", "self_ms", "cpu_ms", "retry_wait_ms")


def scrub(line):
    try:
        rec = json.loads(line)
    except ValueError:
        return None
    if not isinstance(rec, dict) or not isinstance(rec.get("route"), str):
        return None
    out = {k: rec[k] for k in KEEP if isinstance(rec.get(k), (str, int))}
    out.update({k: rec[k] for k in MS if isinstance(rec.get(k), (int, float))})
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
