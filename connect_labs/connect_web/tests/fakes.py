"""Fake PersonalID, Connect and CommCare HQ behind one ``httpx.MockTransport``, for tests.

Behaves as the real servers do for the calls Connect Web makes (see the module docstrings of
`personalid`, `connect_api` and `hq`), and records every request so tests can assert its shape.
Nothing here can reach a real server.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs

import httpx

PERSONALID = "https://personalid.test"
CONNECT = "https://connect.test"
HQ = "https://www.commcarehq.org"

OPP_UUID = "3f2a6b8e-0000-4000-8000-000000000001"
USERNAME = "PoolUserOne"
BACKUP_CODE = "123456"
PASSWORD_FROM_RECOVERY = "rotated-password-1"


def opportunity(**overrides) -> dict:
    opp = {
        "id": 77,
        "opportunity_id": OPP_UUID,
        "name": "Malaria nets",
        "description": "Distribute bed nets.",
        "short_description": "Nets",
        "organization": "demo-org",
        "start_date": "2026-11-01",
        "end_date": "2026-12-31",
        "max_visits_per_user": 20,
        "daily_max_visits_per_user": 5,
        "budget_per_visit": 3,
        "budget_per_user": 60,
        "currency": "USD",
        "is_active": True,
        "is_user_suspended": False,
        "verification_flags": {"form_submission_start": None, "form_submission_end": None},
        "payment_units": [{"name": "Household visit", "amount": 3}],
        "claim": None,
        "learn_progress": {"total_modules": 2, "completed_modules": 1},
        "deliver_progress": 0,
        "learn_app": {
            "cc_domain": "nets-learn",
            "cc_app_id": "learnapp123",
            "name": "Nets Learn",
            "description": "",
            "organization": "demo-org",
            "passing_score": 70,
            "install_url": f"{HQ}/a/nets-learn/apps/download/learnapp123/media_profile.ccpr",
            "learn_modules": [
                {"id": 1, "slug": "intro", "name": "Intro", "description": "Start here", "time_estimate": 5},
                {"id": 2, "slug": "nets", "name": "Using nets", "description": "", "time_estimate": 10},
            ],
        },
        "deliver_app": {
            "cc_domain": "nets-deliver",
            "cc_app_id": "deliverapp456",
            "name": "Nets Deliver",
            "description": "",
            "organization": "demo-org",
            "passing_score": None,
            "install_url": f"{HQ}/a/nets-deliver/apps/download/deliverapp456/media_profile.ccpr",
            "learn_modules": [],
        },
    }
    opp.update(overrides)
    return opp


class FakeServers:
    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.backup_code = BACKUP_CODE
        self.attempts_left = 3
        self.passwords = {USERNAME: None}  # set by recovery
        self.issued = 0
        self.valid_access_tokens: set[str] = set()
        self.hq_issued = 0
        self.opportunities = [opportunity()]
        self.learn_progress = {
            "completed_modules": [{"module": 1, "date": "2026-11-02", "duration": "00:05:00", "id": 10}],
            "assessments": [],
        }
        self.delivery_progress = {
            "max_payments": 20,
            "end_date": "2026-12-31",
            "payment_accrued": 6,
            "is_user_suspended": False,
            "deliveries": [
                {
                    "id": 1,
                    "status": "approved",
                    "reason": "",
                    "visit_date": "2026-11-10",
                    "deliver_unit_name": "Household visit",
                    "deliver_unit_slug": "1",
                    "entity_id": "e1",
                    "entity_name": "House 1",
                    "flags": {},
                },
                {
                    "id": 2,
                    "status": "pending",
                    "reason": "",
                    "visit_date": "2026-11-11",
                    "deliver_unit_name": "Household visit",
                    "deliver_unit_slug": "1",
                    "entity_id": "e2",
                    "entity_name": "House 2",
                    "flags": {"gps": "Too far from the last visit"},
                },
            ],
            "payments": [
                {
                    "id": 5,
                    "payment_id": "9b0c-pay-uuid",
                    "amount": 6,
                    "date_paid": "2026-11-15",
                    "confirmed": False,
                    "confirmation_date": None,
                }
            ],
            "assigned_tasks": [],
        }
        self.claim_status = 201
        self.claim_error = None
        self.start_learn_status = 200
        self.force_status: dict[str, int] = {}
        self.access_token_ttl = 3600
        self.hq_token_ttl = 900

    # -- plumbing ---------------------------------------------------------------------------
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def calls(self, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == path]

    @staticmethod
    def form(request: httpx.Request) -> dict:
        return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}

    @staticmethod
    def body(request: httpx.Request):
        return json.loads(request.content or b"null")

    def _bearer_ok(self, request) -> bool:
        auth = request.headers.get("Authorization", "")
        return auth.startswith("Bearer ") and auth[len("Bearer ") :] in self.valid_access_tokens

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path in self.force_status:
            return httpx.Response(self.force_status[path], json={})
        host = f"{request.url.scheme}://{request.url.host}"
        if host == PERSONALID:
            return self._personalid(request, path)
        if host == CONNECT:
            return self._connect(request, path)
        if host == HQ:
            return self._hq(request, path)
        return httpx.Response(404)

    # -- PersonalID --------------------------------------------------------------------------
    def _personalid(self, request, path):
        if path == "/users/start_configuration":
            return httpx.Response(
                200,
                json={
                    "token": "config-session-token",
                    "required_lock": "pin",
                    "demo_user": True,
                    "toggles": {},
                    "sms_method": "personal_id",
                },
            )
        if path == "/users/recover/complete_recovery":
            if request.headers.get("Authorization") != "Bearer config-session-token":
                return httpx.Response(401, json={"error_code": "INVALID_TOKEN"})
            if self.body(request).get("backup_code") != self.backup_code:
                self.attempts_left -= 1
                return httpx.Response(200, json={"attempts_left": self.attempts_left})
            self.issued += 1
            password = f"rotated-password-{self.issued}"
            self.passwords[USERNAME] = password
            return httpx.Response(
                200, json={"username": USERNAME, "password": password, "db_key": "dbkey", "invited_user": True}
            )
        if path == "/o/token/":
            form = self.form(request)
            if form.get("client_id") != "pid-client":
                return httpx.Response(401, json={"error": "invalid_client"})
            if self.passwords.get(form.get("username")) != form.get("password"):
                return httpx.Response(400, json={"error": "invalid_grant"})
            token = f"pid-access-{len(self.valid_access_tokens) + 1}"
            self.valid_access_tokens.add(token)
            return httpx.Response(
                200, json={"access_token": token, "expires_in": self.access_token_ttl, "token_type": "Bearer"}
            )
        return httpx.Response(404)

    # -- Connect -----------------------------------------------------------------------------
    def _connect(self, request, path):
        if not self._bearer_ok(request):
            return httpx.Response(401, json={"detail": "Invalid token."})
        if path == "/api/opportunity/":
            return httpx.Response(200, json=self.opportunities)
        if path == "/users/start_learn_app/":
            if self.start_learn_status != 200:
                return httpx.Response(self.start_learn_status, text="Server Error")
            return httpx.Response(200)
        if path == f"/api/opportunity/{OPP_UUID}/learn_progress":
            return httpx.Response(200, json=self.learn_progress)
        if path == f"/api/opportunity/{OPP_UUID}/delivery_progress":
            return httpx.Response(200, json=self.delivery_progress)
        if path == f"/api/opportunity/{OPP_UUID}/claim":
            if self.claim_error:
                return httpx.Response(400, json={"error_code": self.claim_error})
            return httpx.Response(self.claim_status, json="Opportunity is already claimed")
        if path == "/api/payment/confirm":
            payments = self.body(request).get("payments") or []
            if any(p.get("confirmed") not in ("true", "false") for p in payments):
                return httpx.Response(400, json={"error_code": "INVALID_FLAG"})
            return httpx.Response(200)
        return httpx.Response(404)

    # -- HQ ----------------------------------------------------------------------------------
    def _hq(self, request, path):
        if path == "/oauth/token/":
            form = self.form(request)
            if form.get("password") not in self.valid_access_tokens:
                return httpx.Response(400, json={"error": "invalid_grant"})
            self.hq_issued += 1
            return httpx.Response(200, json={"access_token": f"hq-{self.hq_issued}", "expires_in": self.hq_token_ttl})
        return httpx.Response(404)
