"""Labs' canopy agent settings in a test: the ``CanopyAgentSettings`` row Labs Admin ›
Canopy agents saves, which is the only place they come from (``labs/canopy_settings.py``).

The ``canopy_agents`` fixture (``connect_labs/conftest.py``) hands out ``save_agents``;
a test without fixtures (a ``TestCase``) calls it directly. Each call is a new row, in
force at once, so a test changes the settings by calling it again.
"""

from __future__ import annotations


def save_agents(
    default: str = "ace",
    *,
    offered=(),
    sensitive_data=(),
    runner_requirements=(),
    acts_as_visitor: bool | None = None,
    version: str = "",
):
    """Put these settings in force: ``default`` and every agent in ``offered`` and
    ``sensitive_data`` shown; those in ``sensitive_data`` with "Sensitive data" ticked. Returns the row.

    ``acts_as_visitor`` None keeps the grant as the row in force has it (off when there
    is none), as a save on the page does. ``default=""`` saves a row with no default
    agent. Not validated against canopy: a test about validation calls
    ``canopy_settings.save``.
    """
    from connect_labs.labs import canopy_settings
    from connect_labs.labs.models import CanopyAgentSettings

    if acts_as_visitor is None:
        acts_as_visitor = canopy_settings.current().acts_as_visitor

    sensitive = list(dict.fromkeys(sensitive_data))
    slugs = list(dict.fromkeys(s for s in (default, *offered, *sensitive) if s))
    return CanopyAgentSettings.objects.create(
        agents=[{"slug": s, "offered": True, "sensitive_data": s in sensitive} for s in slugs],
        default_agent=default,
        runner_requirements=list(runner_requirements),
        site_agents_version=version,
        acts_as_visitor=acts_as_visitor,
    )


#: The canopy the SDK's conformance plugin stands in for (its ``canopy_client`` has
#: client id ``https://canopy.test/oauth/client.json``). A test that turns the grant on
#: with that client sets ``CANOPY_BASE_URL`` to this, since labs derives canopy's client
#: id from it.
CONFORMANCE_CANOPY = "https://canopy.test"
