"""A stand-in for canopy's site agents API (``canopy_settings.site_agents``) in tests.

By default (the autouse ``fake_canopy`` fixture in ``connect_labs/conftest.py``) canopy
agrees with labs: it lists every agent labs ticks, each ready on a sensitive-data runner,
and declares as handling sensitive data the ones labs lets read real data and the deployment's
``CANOPY_DATA_AGENT_SLUG`` -- so a test that is not about canopy's word reads as it did
before the API. A test about canopy's word sets one with ``fake_canopy.set(...)``.
"""

from __future__ import annotations

from canopy_sdk.site_agents import SiteAgents

_AGREES = object()


def agent(
    slug, *, handles_sensitive=False, sensitive_data=True, ready=True, problems=(), description="", flags=None
) -> dict:
    """One listed agent, as canopy's claims carry it."""
    return {
        "slug": slug,
        "name": slug,
        "description": description,
        "url": f"https://canopy.example/agents/{slug}",
        "capabilities": [{"name": "default", "description": description, "handles_sensitive": handles_sensitive}],
        "runners": {
            "flags_available": list(flags if flags is not None else (["sensitive_data"] if sensitive_data else [])),
            "ready_with": {"sensitive_data": bool(sensitive_data and ready)},
        },
        "ready": ready,
        "problems": list(problems),
    }


def listing(*agents, version="v-test", flags=("sensitive_data",), stale=False) -> SiteAgents:
    """canopy's answer listing ``agents`` (each from ``agent`` or a bare slug)."""
    claims = {
        "v": 1,
        "iss": "https://canopy.example",
        "aud": "connect-labs",
        "version": version,
        "site": {"name": "connect-labs", "tenant": "connect"},
        "runner_flags": [
            {"flag": f, "meaning": "a sensitive-data runner" if f == "sensitive_data" else ""} for f in flags
        ],
        "agents": [a if isinstance(a, dict) else agent(a) for a in agents],
    }
    return SiteAgents.from_claims(claims, stale=stale)


def agreeing() -> SiteAgents:
    """canopy agreeing with labs' ticks (the default)."""
    from django.conf import settings

    from connect_labs.labs import canopy_settings

    ticks = canopy_settings.current()
    sensitive = set(ticks.real_data_agents) | {getattr(settings, "CANOPY_DATA_AGENT_SLUG", "") or ""}
    return listing(*(agent(t.slug, handles_sensitive=t.slug in sensitive) for t in ticks.agents))


class FakeCanopy:
    def __init__(self):
        self.answer = _AGREES

    def set(self, answer):
        """``answer``: a ``SiteAgents`` (``listing(...)``), or None for "canopy said nothing"."""
        self.answer = answer

    def __call__(self):
        return agreeing() if self.answer is _AGREES else self.answer
