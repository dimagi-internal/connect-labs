"""One path: every read of visit data is over a data space, and only ``spaces`` makes one.

``labsdata/spaces.py`` makes the space a call reads -- a saved one through ``open`` (every
frozen source held, now, else ``not_found``) or an unsaved one through ``generate`` (the
selection resolved as the caller, now) -- and ``service`` reads over nothing else. That
protects only the reads that go THROUGH it. A view holding ``DataSpace.objects.get(pk=...)``,
a tool filtering ``SavedQuery.objects.filter(space_id=...)`` or a module resolving ids with
``scope.expand`` has stepped around it, and the ids came off a URL or out of an agent's
arguments. So this reads the code itself (the shape of supply_chain's
``test_views_do_not_escape_the_scope`` and canopy-web's ``test_workspace_authorizer_is_sole_gate``):

1. ``DataSpace`` is named only in ``spaces.py`` and ``models.py`` (and migrations).
2. In the Data page's views and the ``data_*`` tools, every function that touches a
   ``space_id`` -- a parameter, ``body.get("space_id")``, ``self.kwargs["space_id"]``, a
   ``space_id=`` keyword -- or filters/creates a saved query by space calls the gate:
   ``spaces.open`` or ``spaces.from_request`` itself, or a helper in the same module that does.
3. ``spaces.open`` resolves the space's sources through ``spaces._resolve`` (which refuses
   anything not held), and ``spaces.py`` never runs a query itself.
4. Sources are resolved in ``spaces.py`` alone: no other module of the read path calls
   ``scope.resolve`` / ``scope.expand`` or makes an ``OpenSpace``, and ``service``'s reads
   refuse anything that is not one.

A static check on purpose: it runs on code nobody has written yet.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.labsdata import service

LABS = pathlib.Path(__file__).resolve().parents[2]
LABSDATA = LABS / "labsdata"
SPACES = LABSDATA / "spaces.py"
#: Where a space_id arrives from a person or an agent.
DOORS = (LABSDATA / "views.py", LABS / "mcp" / "tools" / "labsdata.py")
#: The modules a read of visit data passes through.
READ_PATH = (*DOORS, LABSDATA / "service.py", LABSDATA / "engine.py", LABSDATA / "drill.py", LABSDATA / "fields.py")
#: The gate's own calls: a function making one of these is a gate.
GATES = {"open", "from_request"}


def _tree(path: pathlib.Path) -> ast.Module:
    return ast.parse(path.read_text(), filename=str(path))


def _functions(tree: ast.AST):
    return [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]


def _calls_open(node: ast.AST) -> bool:
    """A call to ``spaces.open(...)`` or ``spaces.from_request(...)``."""
    for n in ast.walk(node):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            if n.func.attr in GATES and isinstance(n.func.value, ast.Name) and n.func.value.id == "spaces":
                return True
    return False


def _called_names(node: ast.AST) -> set[str]:
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            if isinstance(n.func, ast.Name):
                out.add(n.func.id)
            elif isinstance(n.func, ast.Attribute):
                out.add(n.func.attr)
    return out


def _touches_a_space(fn: ast.AST) -> bool:
    for n in ast.walk(fn):
        if isinstance(n, ast.arg) and n.arg == "space_id":
            return True
        if isinstance(n, ast.Name) and n.id == "space_id":
            return True
        if isinstance(n, ast.Constant) and n.value == "space_id":
            return True
        if isinstance(n, ast.keyword) and n.arg and (n.arg in {"space", "space_id"} or n.arg.startswith("space__")):
            if n.arg == "space__isnull" and isinstance(n.value, ast.Constant) and n.value.value is True:
                continue  # a personal saved query: no space at all
            return True
    return False


def test_dataspace_is_named_only_by_the_gate_module():
    offenders = []
    for path in LABS.rglob("*.py"):
        parts = set(path.relative_to(LABS).parts)
        if "tests" in parts or "migrations" in parts or path in (SPACES, LABSDATA / "models.py"):
            continue
        tree = _tree(path)
        for n in ast.walk(tree):
            named = (isinstance(n, ast.Name) and n.id == "DataSpace") or (
                isinstance(n, ast.Attribute) and n.attr == "DataSpace"
            )
            imported = isinstance(n, ast.ImportFrom) and any(a.name == "DataSpace" for a in n.names)
            if named or imported:
                offenders.append(f"{path.relative_to(LABS)}:{getattr(n, 'lineno', '?')}")
    assert not offenders, "Read a data space only through labsdata.spaces (open / openable / reads): " + ", ".join(
        sorted(set(offenders))
    )


def test_every_door_that_takes_a_space_id_opens_it_through_the_gate():
    offenders = []
    for path in DOORS:
        tree = _tree(path)
        functions = _functions(tree)
        gates = {f.name for f in functions if _calls_open(f)}
        assert gates, f"{path.name} names no gate at all"
        for fn in functions:
            if fn.name in gates or not _touches_a_space(fn):
                continue
            if not (_called_names(fn) & gates):
                offenders.append(f"{path.relative_to(LABS)}:{fn.lineno} {fn.name}")
    assert not offenders, "These take a space_id (or read saved queries by space) without spaces.open: " + ", ".join(
        offenders
    )


def test_the_gate_resolves_sources_itself_and_runs_nothing_itself():
    tree = _tree(SPACES)
    for name in ("open", "generate"):
        fn = next(f for f in _functions(tree) if f.name == name)
        assert "_resolve" in _called_names(fn), f"spaces.{name} must resolve the space's sources through _resolve"
    for n in ast.walk(tree):
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "engine":
            raise AssertionError(f"spaces.py:{n.lineno} runs engine.{n.attr} itself; go through service")
        if isinstance(n, ast.ImportFrom) and n.module and n.module.endswith("engine"):
            raise AssertionError("spaces.py imports the engine; queries over a space go through service")


def test_this_check_would_catch_a_door_that_skips_the_gate():
    """The checker itself: a view reading a space by id, without the gate, is caught."""
    bad = ast.parse(
        "class V:\n"
        "    def handle(self, request, body):\n"
        "        return SavedQuery.objects.filter(space_id=body['space_id'])\n"
    )
    fn = _functions(bad)[0]
    assert _touches_a_space(fn) and not _calls_open(fn)
    good = ast.parse("def handle(request, body):\n    return spaces.open(caller, body.get('space_id'))\n")
    assert _calls_open(_functions(good)[0])


def test_sources_are_resolved_only_in_spaces():
    offenders = []
    for path in READ_PATH:
        for n in ast.walk(_tree(path)):
            if isinstance(n, ast.Call):
                f = n.func
                resolves = (
                    isinstance(f, ast.Attribute)
                    and f.attr in {"resolve", "expand"}
                    and isinstance(f.value, ast.Name)
                    and f.value.id == "scope"
                )
                private = (isinstance(f, ast.Attribute) and f.attr == "_resolve") or (
                    isinstance(f, ast.Name) and f.id in {"_resolve", "OpenSpace"}
                )
                made = isinstance(f, ast.Attribute) and f.attr == "OpenSpace"
                if resolves or private or made:
                    offenders.append(f"{path.relative_to(LABS)}:{n.lineno}")
    assert not offenders, "Resolve sources (and make a space) only in labsdata.spaces: " + ", ".join(offenders)


@pytest.mark.parametrize(
    "call",
    [
        lambda: service.describe(SYSTEM, [1]),
        lambda: service.query(SYSTEM, [1], "SELECT 1 FROM visits"),
        lambda: service.drill_into(SYSTEM, [1], "SELECT COUNT(*) FROM visits", [1], 0),
        lambda: service.visits(SYSTEM, [1], ["v"]),
        lambda: service.workflows(SYSTEM, {"opportunity_ids": [1]}),
        lambda: service.validate_selected("SELECT 1 FROM visits", [1]),
        lambda: service.open_on_page_url([1], "SELECT 1 FROM visits"),
    ],
)
def test_service_reads_refuse_anything_but_a_space(call):
    with pytest.raises(TypeError, match="data space"):
        call()
