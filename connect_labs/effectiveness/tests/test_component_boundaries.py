"""The layers, and which way their imports may point.

See ``docs/EFFECTIVENESS.md``. Three layers, each reading only the ones below:

* **Baseline** (``labs/indicators``, ``labs/admin_boundaries``): what the indicators say about a place, with
  filters. A first-class system others build on; it knows nothing about models, costs or Pulse.
* **Pulse** (``pulse``): what Connect delivered, and what it cost. It does not know about the models.
* **Effectiveness** (``effectiveness``): the models run on the baseline, applied to what Pulse delivered
  (Implementations) and to places not yet served (Market size). The only layer that reads the other two.

Inside effectiveness the arithmetic (``engine``, ``valuation``, ``result``, ``groups``, ``explore``,
``sheets_check``, ``market``, and the Implementations catalog and what-if) is pure: no Django, no database, so every
page and agent tool that shows a number shows the one the models give. The baseline is read through its public
modules only (``query``, ``access``, ``measures``, ...), never through its views.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # connect_labs/

BASELINE_DIRS = ("labs/indicators", "labs/admin_boundaries")
BASELINE_PKGS = ("connect_labs.labs.indicators", "connect_labs.labs.admin_boundaries")
#: What the baseline must not import: the layers built on top of it.
ABOVE_BASELINE = ("connect_labs.effectiveness", "connect_labs.pulse")
PURE_CORE = (
    "engine.py",
    "valuation.py",
    "result.py",
    "groups.py",
    "explore.py",
    "sheets_check.py",
    "market.py",
    "implementation/catalog.py",
    "implementation/whatif.py",
)
#: The only effectiveness modules that read the baseline packages.
READS_BASELINE = {"baseline.py", "places.py", "interventions.py", "people.py", "views/market.py"}


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
    found = sorted(rel for rel, p in _files(*BASELINE_DIRS) if any(_under(m, ABOVE_BASELINE) for m in _imports(p)))
    assert not found, (
        f"The baseline imports effectiveness or Pulse: {found}. The baseline must not depend on what is built on "
        "it (docs/EFFECTIVENESS.md). Put the code in effectiveness, or pass the value in."
    )


def test_pulse_does_not_import_effectiveness():
    found = sorted(
        rel for rel, p in _files("pulse") if any(_under(m, ("connect_labs.effectiveness",)) for m in _imports(p))
    )
    assert not found, f"Pulse is delivered data and must not know about the models: {found}"


def test_the_arithmetic_is_pure():
    """The core imports no Django and only other core modules: the one evaluator everything calls."""
    core = {"connect_labs.effectiveness." + n.removesuffix(".py").replace("/", ".") for n in PURE_CORE}
    # a package's own __init__ is imported on the way to its modules
    core |= {"connect_labs.effectiveness", "connect_labs.effectiveness.implementation"}
    for name in PURE_CORE:
        outside = sorted(
            m
            for m in _imports(ROOT / "effectiveness" / name)
            if m.split(".")[0] in ("django", "connect_labs") and not _under(m, core)
        )
        assert not outside, f"effectiveness/{name} must stay pure, but imports {outside}"


def test_the_baseline_is_read_through_its_public_modules_not_its_views():
    for rel, p in _files("effectiveness"):
        found = sorted(m for m in _imports(p) if _under(m, ("connect_labs.labs.indicators.views",)))
        assert not found, f"{rel} imports the baseline's views ({found}); use its query/access modules"


def test_only_the_place_layer_and_the_market_page_read_the_baseline():
    for rel, p in _files("effectiveness"):
        name = rel.removeprefix("effectiveness/")
        if name in READS_BASELINE:
            continue
        found = sorted(m for m in _imports(p) if _under(m, BASELINE_PKGS))
        assert not found, f"{rel} reads the baseline ({found}); route it through effectiveness.baseline or .places"


def test_implementations_reach_the_models_only_through_the_place_layer():
    """It applies the models to what was delivered, placed and baselined by ``places`` and ``baseline``."""
    imports = set()
    for _, p in _files("effectiveness/implementation"):
        imports |= _imports(p)
    assert {
        "connect_labs.effectiveness.result",
        "connect_labs.effectiveness.baseline",
        "connect_labs.effectiveness.places",
    } <= imports
