"""Check the labs formplayer end to end, from where the web tier runs.

    manage.py connect_web_formplayer_check

On AWS, run it inside the VPC with run-labs-command.yml (the web task definition), after the
formplayer service is up and web.json carries CONNECT_WEB_FORMPLAYER_URL / _KEY (formplayer/DEPLOY.md).
It sends three requests to CONNECT_WEB_FORMPLAYER_URL and fails unless each gets the answer a
healthy labs-mode formplayer gives:

1. a labs request with no signature             -> 401  (labs mode is on, and enforcing)
2. the same request signed with the WRONG key   -> 401  (the signature is really checked)
3. signed with CONNECT_WEB_FORMPLAYER_KEY:
   delete_application_dbs for a made-up user/app -> 200, "error": false

Request 3 is a real authenticated call that never reaches HQ: it takes the user's lock (Redis),
clears an app sandbox that does not exist (the EFS data dir) and needs the database up to boot.
So it proves the URL, the shared key, and formplayer's own backing services, with no worker and
no HQ token. The placeholder token is never sent anywhere.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

import httpx
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from connect_labs.connect_web import http

SIGNATURE_HEADER = "X-Labs-Signature"
TOKEN_HEADER = "X-Labs-HQ-Token"
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

    def handle(self, *args, **options):
        base = settings.CONNECT_WEB_FORMPLAYER_URL.rstrip("/")
        key = settings.CONNECT_WEB_FORMPLAYER_KEY
        if not base or not key:
            raise CommandError("CONNECT_WEB_FORMPLAYER_URL and CONNECT_WEB_FORMPLAYER_KEY must both be set.")
        url = f"{base}/delete_application_dbs"
        body = json.dumps(CHECK_BODY).encode()
        headers = {"Content-Type": "application/json", TOKEN_HEADER: "labs-formplayer-check"}

        failures = []
        with http.client() as client:

            def post(extra: dict[str, str]) -> httpx.Response | None:
                try:
                    return client.post(url, content=body, headers={**headers, **extra})
                except httpx.HTTPError as e:
                    failures.append(f"{url}: {type(e).__name__}: {e}")
                    return None

            for label, extra in (
                ("unsigned", {}),
                ("wrong key", {SIGNATURE_HEADER: sign(key + "-wrong", body)}),
            ):
                response = post(extra)
                if response is None:
                    break
                if response.status_code != 401:
                    failures.append(f"{label} request: expected 401, got {response.status_code}")
                else:
                    self.stdout.write(f"ok   {label} request refused (401)")

            if not failures:
                response = post({SIGNATURE_HEADER: sign(key, body)})
                if response is not None:
                    payload = http.json_or_none(response)
                    if response.status_code == 401:
                        failures.append("signed request: 401, so formplayer's LABS_AUTH_KEY is not this key")
                    elif response.status_code != 200 or not isinstance(payload, dict) or payload.get("error"):
                        failures.append(f"signed request: expected 200 and error=false, got {response.status_code}")
                    else:
                        self.stdout.write("ok   signed request accepted (200): key matches, Redis and storage up")

        if failures:
            raise CommandError("formplayer check failed:\n  " + "\n  ".join(failures))
        self.stdout.write(self.style.SUCCESS(f"formplayer at {base} is healthy"))
