"""One gate: nothing reads a data space except through ``spaces.open``.

``labsdata/spaces.py`` decides who may open a space -- every frozen source held, now --
and answers ``not_found`` otherwise. That protects only the reads that go THROUGH it. A
view holding ``DataSpace.objects.get(pk=space_id)`` or a tool filtering
``SavedQuery.objects.filter(space_id=...)`` has stepped around the gate, and the id came
off a URL or out of an agent's arguments. So this reads the code itself (the shape of
supply_chain's ``test_views_do_not_escape_the_scope`` and canopy-web's
``test_workspace_authorizer_is_sole_gate``):

1. ``DataSpace`` is named only in ``spaces.py`` and ``models.py`` (and migrations).
2. In the Data page's views and the ``data_*`` tools, every function that touches a
   ``space_id`` -- a parameter, ``body.get("space_id")``, ``self.kwargs["space_id"]``, a
   ``space_id=`` keyword -- or filters/creates a saved query by space calls the gate:
   ``spaces.open`` itself, or a helper in the same module that does.
3. ``spaces.open`` resolves the space's sources through ``service.select`` (which refuses
   anything not held), and ``spaces.py`` never runs a query itself.

A static check on purpose: it runs on code nobody has written yet.
"""

from __future__ import annotations

import ast
import pathlib

LABS = pathlib.Path(__file__).resolve().parents[2]
LABSDATA = LABS / "labsdata"
SPACES = LABSDATA / "spaces.py"
#: Where a space_id arrives from a person or an agent.
DOORS = (LABSDATA / "views.py", LABS / "mcp" / "tools" / "labsdata.py")


def _tree(path: pathlib.Path) -> ast.Module:
    return ast.parse(path.read_text(), filename=str(path))


def _functions(tree: ast.AST):
    return [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]


def _calls_open(node: ast.AST) -> bool:
    """A call to ``spaces.open(...)``."""
    for n in ast.walk(node):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            if n.func.attr == "open" and isinstance(n.func.value, ast.Name) and n.func.value.id == "spaces":
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


def test_the_gate_resolves_sources_through_service_select_and_runs_nothing_itself():
    tree = _tree(SPACES)
    gate = next(f for f in _functions(tree) if f.name == "open")
    selects = [
        n
        for n in ast.walk(gate)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "select"
        and isinstance(n.func.value, ast.Name)
        and n.func.value.id == "service"
    ]
    assert selects, "spaces.open must resolve the space's sources through service.select"
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
