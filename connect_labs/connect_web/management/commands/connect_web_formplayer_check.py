"""Check the labs formplayer end to end, from where the web tier runs.

    manage.py connect_web_formplayer_check

On AWS, run it inside the VPC with run-labs-command.yml (the web task definition), after the
formplayer service is up and web.json carries CONNECT_WEB_FORMPLAYER_URL / _KEY (formplayer/DEPLOY.md).
It sends these requests to CONNECT_WEB_FORMPLAYER_URL and fails unless each gets the answer a
healthy labs-mode formplayer gives, all signed v2 (method, path, timestamp, token, host, body):

1. no signature at all                          -> 401 or 403  (labs mode is on, and enforcing)
2. v2-signed with the WRONG key                 -> 401  (the signature is really checked)
3. v2-signed an hour ago                        -> 401  (replay window enforced)
4. v2-signed with CONNECT_WEB_FORMPLAYER_KEY:
   delete_application_dbs for a made-up user/app -> 200, "error": false
5. with --expect-v1-closed: v1-only (body-signed) -> 401  (LabsAllowV1=false)

Request 4 is a real authenticated call that never reaches HQ: it takes the user's lock, clears an
app sandbox that does not exist and needs the database and Redis up. So it proves the URL, the
shared key, and formplayer's own backing services, with no worker and no HQ token. The placeholder
token is never sent anywhere.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import httpx
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from connect_labs.connect_web import http
from connect_labs.connect_web.formplayer import SIGNATURE_V2_HEADER, TIMESTAMP_HEADER, sign_v2

SIGNATURE_HEADER = "X-Labs-Signature"
TOKEN_HEADER = "X-Labs-HQ-Token"
CHECK_TOKEN = "labs-formplayer-check"
PATH = "/delete_application_dbs"
CHECK_BODY = {
    "username": "labs-formplayer-check",
    "domain": "labs-formplayer-check",
    "app_id": "labs-formplayer-check",
}


def sign(key: str, body: bytes) -> str:
    """X-Labs-Signature: standard padded Base64 of HMAC-SHA256(key, the exact body bytes)."""
    return base64.b64encode(hmac.new(key.encode(), body, hashlib.sha256).digest()).decode()


class Command(BaseCommand):
    help = "Check the labs formplayer: labs auth enforced, the shared key accepted, backing services up."

    def add_arguments(self, parser):
        parser.add_argument(
            "--expect-v1-closed",
            action="store_true",
            help="Also require a v1-only (body-signed) request to be refused, once LabsAllowV1=false.",
        )

    def handle(self, *args, **options):
        base = settings.CONNECT_WEB_FORMPLAYER_URL.rstrip("/")
        key = settings.CONNECT_WEB_FORMPLAYER_KEY
        if not base or not key:
            raise CommandError("CONNECT_WEB_FORMPLAYER_URL and CONNECT_WEB_FORMPLAYER_KEY must both be set.")
        url = f"{base}/delete_application_dbs"
        body = json.dumps(CHECK_BODY).encode()
        headers = {"Content-Type": "application/json", TOKEN_HEADER: CHECK_TOKEN}

        failures = []
        with http.client() as client:

            def post(extra: dict[str, str]) -> httpx.Response | None:
                try:
                    return client.post(url, content=body, headers={**headers, **extra})
                except httpx.HTTPError as e:
                    failures.append(f"{url}: {type(e).__name__}: {e}")
                    return None

            def v2(signing_key: str, ts: int | None = None) -> dict[str, str]:
                ts = int(time.time()) if ts is None else ts
                return {
                    TIMESTAMP_HEADER: str(ts),
                    SIGNATURE_V2_HEADER: sign_v2(signing_key, "POST", PATH, ts, CHECK_TOKEN, None, body),
                }

            # Each must be refused: Spring answers no credentials at all with 403, LabsAuthFilter a
            # bad signature with 401. 200 or 5xx is a failure.
            for label, extra, refused in (
                ("unsigned", {}, (401, 403)),
                ("wrong key (v2)", v2(key + "-wrong"), (401,)),
                ("stale timestamp (v2, replay)", v2(key, int(time.time()) - 3600), (401,)),
            ):
                response = post(extra)
                if response is None:
                    break
                if response.status_code not in refused:
                    failures.append(
                        f"{label} request: expected {' or '.join(map(str, refused))}, got {response.status_code}"
                    )
                else:
                    self.stdout.write(f"ok   {label} request refused ({response.status_code})")

            if not failures:
                response = post(v2(key))
                if response is not None:
                    payload = http.json_or_none(response)
                    if response.status_code == 401:
                        failures.append("signed request: 401, so formplayer's LABS_AUTH_KEY is not this key")
                    elif response.status_code != 200 or not isinstance(payload, dict) or payload.get("error"):
                        failures.append(f"signed request: expected 200 and error=false, got {response.status_code}")
                    else:
                        self.stdout.write("ok   v2-signed request accepted (200): key matches, Redis and storage up")

            if not failures and options.get("expect_v1_closed"):
                response = post({SIGNATURE_HEADER: sign(key, body)})
                if response is not None and response.status_code != 401:
                    failures.append(f"v1-only request: expected 401 (LabsAllowV1=false), got {response.status_code}")
                elif response is not None:
                    self.stdout.write("ok   v1-only request refused (401): v1 is closed")

        if failures:
            raise CommandError("formplayer check failed:\n  " + "\n  ".join(failures))
        self.stdout.write(self.style.SUCCESS(f"formplayer at {base} is healthy"))
