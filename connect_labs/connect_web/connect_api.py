"""Connect's mobile API, as the worker's phone calls it.

Base ``settings.CONNECT_PRODUCTION_URL``; every call carries the worker's PersonalID access token as
``Authorization: Bearer`` and ``Accept: application/json;version=2.0``. Checked against
commcare-connect (``config/api_router.py``, ``opportunity/api/views/mobile.py``,
``opportunity/api/serializers/mobile.py``, ``users/views.py``):

* ``GET /api/opportunity/`` — the opportunities this worker has access to.
* ``POST /users/start_learn_app/`` (form ``opportunity=<uuid>``) — accepts the invite and creates the
  worker's HQ mobile user in the learn app's domain. Idempotent. Answers a bare 500 when the access
  has no invite.
* ``GET /api/opportunity/<uuid>/learn_progress`` — ``completed_modules[{module, date, duration, id}]``
  (``module`` is the learn module's ``id``) and ``assessments[{score, passing_score, passed, date, id}]``.
* ``POST /api/opportunity/<uuid>/claim`` (``{}``) — 201 claimed, 200 already claimed (a bare JSON
  string body), 400 ``OPPORTUNITY_FULL`` / ``OPPORTUNITY_ENDED``. Connect does NOT check that
  learning is complete: the service layer does (`service.claim`).
* ``GET /api/opportunity/<uuid>/delivery_progress`` — ``max_payments, end_date, payment_accrued,
  deliveries[], payments[{id, payment_id, amount, date_paid, confirmed, confirmation_date}],
  assigned_tasks[]``.
* ``POST /api/payment/confirm`` (JSON ``{"payments": [{"id": …, "confirmed": "true"|"false"}]}``) —
  ``confirmed`` is the STRING ``"true"``/``"false"``; Connect answers ``INVALID_FLAG`` to a boolean.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.conf import settings

from . import http
from .errors import START_LEARN_FAILED, ConnectAPIError

ACCEPT_V2 = "application/json;version=2.0"
LIST_READ_TIMEOUT = 18.0


class ConnectClient:
    def __init__(self, access_token: str):
        self._token = access_token

    def __repr__(self) -> str:
        return "ConnectClient(<token hidden>)"

    @property
    def base(self) -> str:
        return settings.CONNECT_PRODUCTION_URL.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}", "Accept": ACCEPT_V2}

    def _get(self, path: str, log_name: str, **options) -> Any:
        response = http.request(
            "GET", f"{self.base}{path}", ConnectAPIError, log_name=log_name, headers=self._headers(), **options
        )
        return http.json_or_none(response)

    def _post(self, path: str, log_name: str, **kwargs) -> Any:
        response = http.request(
            "POST", f"{self.base}{path}", ConnectAPIError, log_name=log_name, headers=self._headers(), **kwargs
        )
        return http.json_or_none(response)

    def list_opportunities(self) -> list[dict]:
        # The list can legitimately take ~10 s: give one read most of the budget, and don't repeat
        # a read that timed out (that only doubles the wait).
        body = self._get(
            "/api/opportunity/",
            "connect.opportunities",
            read_timeout=LIST_READ_TIMEOUT,
            retry_read_timeout=False,
        )
        if isinstance(body, dict) and "results" in body:  # tolerate a paginated answer
            body = body["results"]
        return body if isinstance(body, list) else []

    def start_learn_app(self, opportunity_uuid: str) -> None:
        try:
            self._post("/users/start_learn_app/", "connect.start_learn_app", data={"opportunity": opportunity_uuid})
        except ConnectAPIError as exc:
            if exc.status and exc.status >= 500 and not exc.error_code:
                raise ConnectAPIError(status=exc.status, error_code=START_LEARN_FAILED) from None
            raise

    def learn_progress(self, opportunity_uuid: str) -> dict:
        body = self._get(f"/api/opportunity/{opportunity_uuid}/learn_progress", "connect.learn_progress")
        return body if isinstance(body, dict) else {}

    def claim(self, opportunity_uuid: str) -> bool:
        """Claim; True when this call made the claim, False when it already existed."""
        response = http.request(
            "POST",
            f"{self.base}/api/opportunity/{opportunity_uuid}/claim",
            ConnectAPIError,
            log_name="connect.claim",
            headers=self._headers(),
            json={},
        )
        return response.status_code == 201

    def delivery_progress(self, opportunity_uuid: str) -> dict:
        body = self._get(f"/api/opportunity/{opportunity_uuid}/delivery_progress", "connect.delivery_progress")
        return body if isinstance(body, dict) else {}

    def confirm_payments(self, decisions: Iterable[tuple[str | int, bool]]) -> None:
        payments = [{"id": pid, "confirmed": "true" if confirmed else "false"} for pid, confirmed in decisions]
        if not payments:
            raise ValueError("confirm_payments needs at least one payment")
        self._post("/api/payment/confirm", "connect.confirm_payments", json={"payments": payments})
