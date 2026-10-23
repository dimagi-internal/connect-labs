"""A stand-in for canopy's site agents API (``canopy_settings.site_agents``) in tests.

By default (the autouse ``fake_canopy`` fixture in ``connect_labs/conftest.py``) canopy
agrees with labs' saved settings (the ``CanopyAgentSettings`` row in force, set in a test
with ``connect_labs/testing/canopy_agents.py``): it lists every agent labs ticks, each
ready on a sensitive-data runner, and declares as handling sensitive data the ones labs
lets work with sensitive data, plus any the test names with ``fake_canopy.declare_sensitive(...)``
(an agent canopy declares, which labs shows without "Sensitive data"). A test about canopy's
word sets a whole answer with ``fake_canopy.set(...)``.
"""

from __future__ import annotations

from canopy_sdk.site_agents import SiteAgents

_AGREES = object()


def agent(
    slug,
    *,
    handles_sensitive=False,
    sensitive_data=True,
    ready=True,
    problems=(),
    problem_links=(),
    description="",
    flags=None,
) -> dict:
    """One listed agent, as canopy's claims carry it. ``problem_links``: ``(text, url)``
    pairs (canopy's ``problem_links``); their texts are problems too."""
    problems = [*problems, *(text for text, _ in problem_links)]
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
        "problem_links": [{"text": text, "url": url} for text, url in problem_links],
    }


def listing(
    *agents, version="v-test", flags=("sensitive_data",), stale=False, tenant="connect", settings_url=""
) -> SiteAgents:
    """canopy's answer listing ``agents`` (each from ``agent`` or a bare slug)."""
    claims = {
        "v": 1,
        "iss": "https://canopy.example",
        "aud": "connect-labs",
        "version": version,
        "site": {"name": "connect-labs", "tenant": tenant, "settings_url": settings_url},
        "runner_flags": [
            {"flag": f, "meaning": "a sensitive-data runner" if f == "sensitive_data" else ""} for f in flags
        ],
        "agents": [a if isinstance(a, dict) else agent(a) for a in agents],
    }
    return SiteAgents.from_claims(claims, stale=stale)


def agreeing(declared_sensitive=()) -> SiteAgents:
    """canopy agreeing with labs' ticks (the default): every agent the saved row ticks, each
    declared to handle sensitive data when the row lets it work with sensitive data or the test
    declared it."""
    from connect_labs.labs import canopy_settings

    ticks = canopy_settings.current()
    sensitive = set(ticks.sensitive_data_agents) | set(declared_sensitive)
    return listing(*(agent(t.slug, handles_sensitive=t.slug in sensitive) for t in ticks.agents))


class FakeCanopy:
    def __init__(self):
        self.answer = _AGREES
        self.declared_sensitive: set[str] = set()
        #: What ``last_error`` says when canopy has said nothing (a ``SiteAgentsError``).
        self.error = None

    def set(self, answer):
        """``answer``: a ``SiteAgents`` (``listing(...)``), or None for "canopy said nothing"."""
        self.answer = answer

    def declare_sensitive(self, *slugs):
        """canopy (agreeing) also declares ``slugs`` handle sensitive data."""
        self.declared_sensitive.update(slugs)

    def __call__(self):
        return agreeing(self.declared_sensitive) if self.answer is _AGREES else self.answer

    def fail(self, code, message, status=None):
        """canopy says nothing, for this reason (``last_error``, and what ``refresh`` raises)."""
        from canopy_sdk.site_agents import SiteAgentsError

        self.answer = None
        self.error = SiteAgentsError(code, message, status=status)

    def last_error(self):
        """``canopy_settings.last_error``: why canopy said nothing, or None while it answers."""
        if self.answer is not None:
            return None
        from canopy_sdk.site_agents import SiteAgentsError

        return self.error or SiteAgentsError("unreachable", "canopy could not be reached")

    def refresh(self):
        """``canopy_settings.refresh``: the same answer, or the SDK's error for none."""
        answer = self()
        if answer is None:
            raise self.last_error()
        return answer
