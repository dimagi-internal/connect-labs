# Subscription filter on /ecs/labs-jj-canopy-web -> /labs-jj/observe/canopy-web-errors.
# canopy-web logs one JSON object per line (apps/common/log_format.JsonFormatter); an
# exception carries its traceback as the string `exc_info`. Keeps the shape of an
# ERROR and never its content: logger, module, the exception CLASS (never its
# message), and the traceback's frames as file:line function (code locations, no
# source text). From the message, only ids: UUIDs, integer user/contact ids and an
# agent slug, each picked out by a strict pattern; the rest of the message is dropped.
# Inlined into the template by render.py.
import base64
import gzip
import json
import os
import re
import time
import uuid

import boto3

logs = boto3.client("logs")
LEVELS = ("ERROR", "CRITICAL")
FRAME = re.compile(r'File "(?:.*?/)?((?:apps|config|runner|packages|sdk)/[\w/.-]+\.py)", line (\d+), in (\w+)')
EXC = re.compile(r"^([A-Za-z_][\w.]*(?:Error|Exception|Exit|Interrupt|Warning|Timeout|DoesNotExist|NotFound))\b", re.M)
UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
IDS = {
    "session": re.compile(rf"\bsession=({UUID})\b"),
    "runner": re.compile(rf"\brunner=({UUID})\b"),
    "turn_id": re.compile(rf"\bturn_id=({UUID})\b"),
    "user": re.compile(r"\buser=(\d{1,10})\b"),
    "contact": re.compile(r"\bcontact=(\d{1,10})\b"),
    "agent": re.compile(r"\bagent=([a-z][a-z0-9-]{0,39})\b"),
}
# A message that starts with one of these is a fixed template, kept up to its colon.
TEMPLATES = ("session socket connect failed", "record-session failed", "Exception in ASGI application")


def scrub(line):
    try:
        rec = json.loads(line)
    except ValueError:
        return None
    if not isinstance(rec, dict) or rec.get("severity") not in LEVELS:
        return None
    out = {"severity": rec["severity"]}
    for k in ("logger", "module"):
        v = rec.get(k)
        if isinstance(v, str) and re.fullmatch(r"[\w.-]{1,100}", v):
            out[k] = v
    msg = rec.get("message") if isinstance(rec.get("message"), str) else ""
    for t in TEMPLATES:
        if msg.startswith(t):
            out["template"] = t
    for k, pat in IDS.items():
        m = pat.search(msg)
        if m:
            out[k] = m.group(1)
    exc = rec.get("exc_info")
    if isinstance(exc, str):
        found = EXC.findall(exc)
        if found:
            out["exc_type"] = found[-1][:120]
        frames = ["%s:%s %s" % f for f in FRAME.findall(exc)]
        if frames:
            out["frames"] = frames[-8:]
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
