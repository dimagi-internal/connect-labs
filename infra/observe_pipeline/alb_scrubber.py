# ALB access-log object (S3, via EventBridge) -> /labs-jj/observe/alb-requests.
# Keeps time, target group, method, route TEMPLATE, statuses, latencies, target
# and bytes. Drops client IP, user agent, query string, host, TLS and trace ids.
# Inlined into the template by render.py.
import calendar
import gzip
import json
import os
import re
import time
import uuid

import boto3

s3, logs = boto3.client("s3"), boto3.client("logs")
FIELD = re.compile(r'"[^"]*"|\S+')
STATIC = re.compile(r"^[a-z][a-z_-]{0,29}$")
IDENTITY = {
    "user",
    "users",
    "u",
    "username",
    "worker",
    "workers",
    "flw",
    "flws",
    "profile",
    "people",
    "person",
    "member",
    "members",
    "contact",
    "contacts",
    "email",
    "share",
}


def route(path):
    """A path with every segment that could identify someone replaced by {id}."""
    out, prev = [], ""
    for seg in path.split("?", 1)[0].split("/"):
        orig = seg
        if seg and (not STATIC.match(seg) or prev in IDENTITY):
            seg = "{id}"
        out.append(seg)
        prev = orig
    return "/".join(out) or "/"


def scrub(line):
    f = [x.strip('"') for x in FIELD.findall(line)]
    if len(f) < 13 or f[12].count(" ") < 2:
        return None
    method, url, _ = f[12].split(" ", 2)
    m = re.match(r"^[a-z]+://[^/]+(/[^ ]*)?$", url)
    if not m:
        return None

    def num(x):
        try:
            return float(x) if "." in x else int(x)
        except ValueError:
            return None

    return {
        "time": f[1],
        "target_group": (f[16].split("/") + ["", ""])[1] if len(f) > 16 else None,
        "method": method if method.isalpha() else "OTHER",
        "route": route(m.group(1) or "/"),
        "elb_status": num(f[8]),
        "target_status": num(f[9]),
        "request_s": num(f[5]),
        "target_s": num(f[6]),
        "response_s": num(f[7]),
        "received_bytes": num(f[10]),
        "sent_bytes": num(f[11]),
        "target": f[4],
    }


def _ms(t):
    try:
        return calendar.timegm(time.strptime(t[:19], "%Y-%m-%dT%H:%M:%S")) * 1000
    except ValueError:
        return int(time.time() * 1000)


def handler(ev, ctx):
    d = ev.get("detail") or {}
    body = s3.get_object(Bucket=d["bucket"]["name"], Key=d["object"]["key"])["Body"].read()
    lines = gzip.decompress(body).decode("utf-8", "replace").splitlines()
    recs = [r for r in map(scrub, lines) if r]
    group, stream = os.environ["GROUP"], time.strftime("%Y/%m/%d/") + uuid.uuid4().hex
    if recs:
        logs.create_log_stream(logGroupName=group, logStreamName=stream)
    events = sorted(
        ({"timestamp": _ms(r["time"]), "message": json.dumps(r, separators=(",", ":"))} for r in recs),
        key=lambda x: x["timestamp"],
    )
    for i in range(0, len(events), 3000):
        logs.put_log_events(logGroupName=group, logStreamName=stream, logEvents=events[i : i + 3000])
    return {"written": len(events)}
