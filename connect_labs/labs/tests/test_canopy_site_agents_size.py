"""canopy's signed site agents list verifies with the dimagi-canopy labs pins, at the size
a real listing reaches.

The response carries one entry per agent canopy allows on labs (description, URLs, runner
flags, problems). With six agents it passed 8 KB, the cap every other JWS is held to, and
"Refresh from canopy" failed with ``malformed: not a compact JWS of acceptable size``.
Labs keeps its last good copy for a day, then shows no agent on any page. dimagi-canopy
0.38.0 gives this one response its own cap (``MAX_SITE_AGENTS_BYTES``).
"""

from __future__ import annotations

import pytest
from canopy_sdk import contract
from canopy_sdk import site_agents as wire
from canopy_sdk.keys import generate_private_key, public_jwk

CLIENT_ID = "https://canopy.example"
SITE = "connect-labs"


class _Keys:
    """Stands in for ``ClientKeyResolver``: canopy's one public key, by ``kid``."""

    def __init__(self, key):
        self.jwk = public_jwk(key)

    def key_for(self, client_id, kid):
        assert client_id == CLIENT_ID and kid == self.jwk["kid"]
        return self.jwk


def _listing(n: int) -> dict:
    problems = ["agent-{i} has no runner with sensitive_data ready", "no capability names connect-labs"]
    return {
        "site": {"name": SITE, "tenant": "connect"},
        "runner_flags": [{"flag": "sensitive_data", "meaning": "handles sensitive data"}],
        "agents": [
            {
                "slug": f"agent-{i}",
                "name": f"Agent {i}",
                "description": "Answers questions about the programmes and workflows on this labs page. " * 3,
                "workspace": "connect",
                "owner": "Connect team",
                "url": f"https://labs.connect.dimagi.com/canopy/w/connect/agents/agent-{i}",
                "capabilities": [{"name": "data", "description": "ask about data", "handles_sensitive": True}],
                "runners": {"flags_available": ["sensitive_data"], "ready_with": {"sensitive_data": True}},
                "ready": False,
                "problems": [p.format(i=i) for p in problems],
                "problem_links": [
                    {"text": p.format(i=i), "url": f"https://labs.connect.dimagi.com/canopy/agents/agent-{i}"}
                    for p in problems
                ],
            }
            for i in range(n)
        ],
    }


def test_a_site_agents_listing_over_8kb_verifies():
    key = generate_private_key("EdDSA")
    token = wire.sign_site_agents(key, CLIENT_ID, SITE, _listing(10))
    assert len(token) > contract.MAX_JWT_BYTES
    got = wire.verify_site_agents(token, client_id=CLIENT_ID, site_name=SITE, client_keys=_Keys(key))
    assert len(got.slugs) == 10


def test_a_listing_past_its_own_cap_is_still_refused():
    key = generate_private_key("EdDSA")
    token = wire.sign_site_agents(key, CLIENT_ID, SITE, _listing(80))
    assert len(token) > contract.MAX_SITE_AGENTS_BYTES
    with pytest.raises(wire.SiteAgentsError) as exc:
        wire.verify_site_agents(token, client_id=CLIENT_ID, site_name=SITE, client_keys=_Keys(key))
    assert exc.value.code == "malformed"
