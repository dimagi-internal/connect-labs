"""The three components, and which way their imports may point.

See ``docs/CEA_BASELINE_SCENARIOS.md``. In one line each:

* **Baseline** (``labs/indicators``, ``labs/admin_boundaries``): what the indicators say about a place, with
  filters. It knows nothing about models, costs or scenarios.
* **Cost-effectiveness** (``impact`` and its views, plus ``pulse/cea.py`` for delivered opportunities): the
  models, run on a place's baseline. It may read the baseline; the baseline may not read it.
* **Scenarios** (``scenarios``): market sizing today: unit-price presets (priced from the models where one
  exists) over the places a threshold selects. It may read both; neither may read it.

The test does not ask for a perfect tree today. It pins the exceptions that exist (the ledgers below), so a
new violation fails and a fixed one has to be taken off the list. Each ledger entry says why it is there and
what removes it.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # connect_labs/

BASELINE_DIRS = ("labs/indicators", "labs/admin_boundaries")
#: What the baseline must not import: the components built on top of it.
ABOVE_BASELINE = ("connect_labs.impact", "connect_labs.pulse", "connect_labs.scenarios", "connect_labs.cea_urls")
CEA_PURE_CORE = ("engine", "valuation", "result", "explore")

#: Baseline files that import the CEA valuation, because PMC (cost per death averted by state) lives in
#: the baseline package. Remove an entry by moving PMC (``labs/indicators/pmc.py``, ``emod/``, the PMC views,
#: ``PmcModelRun``) into the cost-effectiveness component; the test then fails until the entry is deleted.
BASELINE_IMPORTS_CEA = {
    "labs/indicators/views.py": "PMC views (PmcView, pmc_fixed_assumptions) sit in the baseline views module",
    "labs/indicators/emod/mortality.py": "PMC's deaths-to-multiple valuation sits in the baseline package",
    "labs/indicators/emod/rank.py": "PMC's ranking sits in the baseline package",
}


def _files(*dirs: str):
    for d in dirs:
        for p in sorted((ROOT / d).rglob("*.py")):
            rel = p.relative_to(ROOT).as_posix()
            if "/tests/" in f"/{rel}" or "/migrations/" in f"/{rel}" or p.name.startswith("test_"):
                continue
            yield rel, p


def _imports(path: Path) -> set[str]:
    """Every module a file imports, at any depth: ``from a.b import c`` gives ``a.b`` and ``a.b.c``."""
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
            out.update(f"{node.module}.{a.name}" for a in node.names)
        elif isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
    return out


def _under(module: str, prefixes) -> bool:
    return any(module == p or module.startswith(p + ".") for p in prefixes)


def test_the_baseline_does_not_import_what_is_built_on_it():
    found = {rel for rel, p in _files(*BASELINE_DIRS) if any(_under(m, ABOVE_BASELINE) for m in _imports(p))}
    new = sorted(found - set(BASELINE_IMPORTS_CEA))
    assert not new, (
        f"The baseline imports the cost-effectiveness or scenarios components: {new}. The baseline must not "
        "depend on what is built on it (docs/CEA_BASELINE_SCENARIOS.md). Put the code in the component that "
        "needs both, or pass the value in."
    )
    stale = sorted(set(BASELINE_IMPORTS_CEA) - found)
    assert not stale, f"Fixed, so take them off BASELINE_IMPORTS_CEA: {stale}"


def test_the_cost_effectiveness_core_is_pure():
    """engine, valuation, result and explore import only each other: no Django, no baseline, no Pulse."""
    for name in CEA_PURE_CORE:
        outside = sorted(
            m
            for m in _imports(ROOT / "impact" / f"{name}.py")
            if (m.split(".")[0] in ("django", "connect_labs")) and not _under(m, ("connect_labs.impact",))
        )
        assert (
            not outside
        ), f"impact/{name}.py must stay pure (the one evaluator everything calls), but imports {outside}"


def test_nothing_below_scenarios_imports_it():
    for rel, p in _files("impact", *BASELINE_DIRS):
        assert not any(_under(m, ("connect_labs.scenarios",)) for m in _imports(p)), f"{rel} imports scenarios"


def test_existing_opportunities_reach_the_models_only_through_impact():
    """The Implementation view (pulse/cea.py) applies the models to what was delivered through ``impact``.

    It may read Pulse's own data (visits, works, costs) and the ``impact`` place layer and evaluator. It must not
    reach into the baseline package itself or carry its own copy of the arithmetic: where a delivery is placed
    and what its baseline is come from ``impact.places`` and ``impact.baseline``, the same as the explorer.
    """
    imports = _imports(ROOT / "pulse" / "cea.py")
    assert not any(_under(m, ("connect_labs.labs.indicators",)) for m in imports), sorted(
        m for m in imports if _under(m, ("connect_labs.labs.indicators",))
    )
    assert {"connect_labs.impact.result", "connect_labs.impact.baseline", "connect_labs.impact.places"} <= imports


def test_scenarios_build_on_the_baseline_and_the_models_only():
    """Market sizing lives in ``scenarios``: it may read the baseline and ``impact``, never Pulse or the CEA pages."""
    for rel, p in _files("scenarios"):
        bad = sorted(m for m in _imports(p) if _under(m, ("connect_labs.pulse", "connect_labs.cea_urls")))
        assert not bad, f"{rel} imports {bad}"
    assert (ROOT / "scenarios" / "interventions.py").exists()
    assert not (ROOT / "labs" / "indicators" / "interventions.py").exists(), "sizing presets belong to scenarios"
