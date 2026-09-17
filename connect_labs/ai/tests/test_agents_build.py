"""Every agent must still BUILD. `connect_labs/ai` had no tests at all.

Eight modules import pydantic-ai and nothing exercised them, so the whole AI
surface rode on a major SDK upgrade (pydantic-ai 1.69 -> 2.44, anthropic
0.85 -> 1.6, openai 2.28 -> 3.14) with a green suite that said nothing about it.
A construction test is cheap and catches the failure mode that actually happens
on those upgrades: a renamed keyword on `Agent(...)`, a moved import, a decorator
that stopped accepting the shape our tools are registered in.

It deliberately DISCOVERS the factories rather than listing them. A list would
be a list of what existed the day it was written -- the first pass of this check
missed four of the six agents because they are named `create_*_agent_with_model`
and the pattern only matched names ending in `agent`.

What it does not do is call a model. Nothing here goes near the network: the
keys are fakes, and a factory that needed a real one would fail loudly here
rather than quietly in production.
"""

import importlib
import inspect
import pkgutil

import pytest
from pydantic_ai import Agent

import connect_labs.ai.agents as agents_pkg

MODEL = "anthropic:claude-sonnet-4-6"


def _agent_modules():
    return sorted(m.name for m in pkgutil.iter_modules(agents_pkg.__path__))


def _factories(module):
    """`create_*` / `get_*` callables that mention "agent" and are defined here."""
    for name, fn in vars(module).items():
        if not callable(fn) or not name.startswith(("create_", "get_")) or "agent" not in name:
            continue
        if getattr(fn, "__module__", None) != module.__name__:
            continue  # re-exported from elsewhere; it is tested where it lives
        yield name, fn


@pytest.fixture(autouse=True)
def _fake_keys(monkeypatch):
    """Construction must not need a real key, and must not reach the network."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-for-construction-only")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-for-construction-only")


@pytest.mark.parametrize("module_name", _agent_modules())
def test_every_agent_module_imports(module_name):
    importlib.import_module(f"connect_labs.ai.agents.{module_name}")


@pytest.mark.parametrize("module_name", _agent_modules())
def test_every_agent_factory_still_builds_an_agent(module_name):
    module = importlib.import_module(f"connect_labs.ai.agents.{module_name}")
    built = 0
    for name, fn in _factories(module):
        params = [
            p
            for p in inspect.signature(fn).parameters.values()
            if p.default is inspect.Parameter.empty and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]
        agent = fn(MODEL) if params else fn()
        assert isinstance(agent, Agent), f"{module_name}.{name} returned {type(agent).__name__}, not an Agent"
        built += 1
    assert built, f"no agent factory found in {module_name} — has the naming changed?"


def test_the_discovery_actually_finds_every_agent():
    """Guards the guard: if discovery silently matched nothing, every test above
    would pass while checking nothing at all."""
    total = sum(
        len(list(_factories(importlib.import_module(f"connect_labs.ai.agents.{m}")))) for m in _agent_modules()
    )
    assert total >= 6, f"only {total} agent factories discovered across {len(_agent_modules())} modules"
