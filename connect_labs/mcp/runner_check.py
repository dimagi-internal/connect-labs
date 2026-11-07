"""Which approved canopy runner sent an agent's request (``agent_request_views``).

The agent can send ``X-Canopy-Runner-Proof``, made by ``canopy vault runner-proof`` with the
runner's approved device key over this endpoint's URL and a digest of the exact request body.
Labs cannot check such a proof (it holds no list of approved keys), so it asks canopy, which
says who holds the key and whether that runner serves the agent the request names
(``canopy_sdk.runner_attestations``).

* No proof, or canopy cannot be asked: the request is **not verified**. It still goes through;
  the person sees that on the approval page. (For now: a request without a proof is allowed.)
* A proof canopy refuses: the request is refused. A bad proof is a misconfigured or hostile
  caller, not an absent one, and must not read as "not verified".
* A proof canopy vouches for: the runner's name is stored with the request and shown.

Whatever is vouched for is the RUNNER's identity. The reason text stays the agent's claim.
"""

from __future__ import annotations

import logging

from .agent_requests import AgentRequestError

log = logging.getLogger(__name__)

HEADER = "X-Canopy-Runner-Proof"
AGENT_MAX = 64


def check(request, endpoint_url: str, speaks_for: str = "") -> dict:
    """``{runner, kind, agent, serves_agent}`` as canopy vouched for it, or ``{}`` (not verified)."""
    from canopy_sdk.django import runner_attestations as ra

    value = request.headers.get(HEADER, "")
    if not value:
        return {}
    try:
        kind, proof = ra.parse_header(value)
    except ra.AttestationError as exc:
        raise AgentRequestError("bad_runner_proof", exc.message) from exc
    speaks_for = str(speaks_for or "").strip()[:AGENT_MAX]
    try:
        got = ra.attest(kind=kind, proof=proof, htu=ra.bound_url(endpoint_url, request.body), speaks_for=speaks_for)
    except ra.AttestationError as exc:
        if exc.code == "proof_refused" or exc.status in (400, 401, 403):
            raise AgentRequestError(
                "runner_proof_refused", f"canopy would not vouch for the runner proof ({exc.code}: {exc.message})."
            ) from exc
        log.warning("runner proof not checked (%s): %s", exc.code, exc.message)
        return {}
    return {"runner": got.runner, "kind": got.kind, "agent": got.agent, "serves_agent": got.serves_agent}
