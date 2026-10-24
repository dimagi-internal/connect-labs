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

5. **Who can open a space, and who changes it** (the ACL). ``open`` and ``openable`` ask
   the audience only after the data rule. Every management write in ``spaces.py`` (sources,
   audience, managers, archive, deleting an item) checks the role itself; every route that
   makes one goes through it; and no ``data_*`` tool and no page action names any of them --
   an agent acts as the visitor and can never change sources, audience or managers.

A static check on purpose: it runs on code nobody has written yet.
"""

from __future__ import annotations

import ast
import pathlib
import re

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
#: The gate's own calls: a function making one of these is a gate. ``admin_add_manager``
#: opens the space as the person being made a manager (pinned below).
GATES = {"open", "from_request", "admin_add_manager"}
#: The writes that change who can open a space or what it is: managers only (or, for
#: ``admin_add_manager``, Dimagi staff), never an agent.
MANAGEMENT = {
    "update_sources": "require_manager",
    "set_audience": "require_manager",
    "add_manager": "require_manager",
    "remove_manager": "require_manager",
    "archive": "require_manager",
    "delete_item": "may_delete",
    "delete_conversation": "may_delete_conversation",
    "admin_add_manager": "is_dimagi_staff",
}
#: Words a tool or page action that changed sources, audience or managers would carry.
MANAGEMENT_WORDS = ("source", "audience", "manager", "archive")
DATA_JS = LABS / "static" / "labsdata" / "data.js"
TOOLS = LABS / "mcp" / "tools" / "labsdata.py"


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
    for name in ("_data_rule", "generate"):
        fn = next(f for f in _functions(tree) if f.name == name)
        assert "_resolve" in _called_names(fn), f"spaces.{name} must resolve the space's sources through _resolve"
    opened = next(f for f in _functions(tree) if f.name == "open")
    assert "_data_rule" in _called_names(opened), "spaces.open must apply the data rule (_data_rule)"
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


def _fn(tree, name):
    return next(f for f in _functions(tree) if f.name == name)


def test_the_audience_is_asked_after_the_data_rule_and_only_there():
    """``open``: the data rule's call comes before the audience's, in source order; and
    ``openable`` filters on ``_holds(...) and _admits(...)`` -- short-circuit, data rule first."""
    tree = _tree(SPACES)
    opened = _fn(tree, "open")
    lines = {}
    for n in ast.walk(opened):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
            lines.setdefault(n.func.id, n.lineno)
    assert lines["_data_rule"] < lines["_admits"], "open asks the audience before the data rule"
    listed = _fn(tree, "openable")
    ands = [n for n in ast.walk(listed) if isinstance(n, ast.BoolOp) and isinstance(n.op, ast.And)]
    assert any(
        [getattr(v.func, "id", None) for v in n.values if isinstance(v, ast.Call)][:2] == ["_holds", "_admits"]
        for n in ands
    ), "openable must filter on _holds(...) and _admits(...), in that order"
    # Nothing else asks an audience.
    offenders = []
    for path in LABS.rglob("*.py"):
        if "tests" in path.parts or path == SPACES:
            continue
        for n in ast.walk(_tree(path)):
            if (isinstance(n, ast.Name) and n.id in {"AUDIENCES", "_admits"}) or (
                isinstance(n, ast.Attribute) and n.attr in {"AUDIENCES", "_admits"}
            ):
                offenders.append(f"{path.relative_to(LABS)}:{n.lineno}")
    assert not offenders, "Only spaces.py asks a space's audience: " + ", ".join(offenders)


def test_every_management_write_checks_the_role_itself():
    tree = _tree(SPACES)
    for name, check in MANAGEMENT.items():
        assert check in _called_names(_fn(tree, name)), f"spaces.{name} must call {check}"
    assert "open" in _called_names(_fn(tree, "admin_add_manager")), "admin_add_manager opens as the person"
    assert "is_manager" in ast.unparse(_fn(tree, "require_manager"))


def _spaces_calls(node) -> set[str]:
    return {
        n.func.attr
        for n in ast.walk(node)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and isinstance(n.func.value, ast.Name)
        and n.func.value.id == "spaces"
    }


def test_every_management_route_calls_the_manager_check():
    """Each URL that changes sources, audience, managers, archive or deletes an item routes
    to a view that makes the matching ``spaces`` write (which checks the role itself)."""
    from django.urls import get_resolver

    from connect_labs.labsdata import urls

    views_tree = _tree(LABSDATA / "views.py")
    classes = {n.name: n for n in ast.walk(views_tree) if isinstance(n, ast.ClassDef)}
    management_routes = [
        p for p in urls.urlpatterns if any(w in str(p.pattern) for w in (*MANAGEMENT_WORDS, "delete"))
    ]
    checked = 0
    for p in management_routes:
        view = p.callback.view_class.__name__
        if view == "SavedQueryDeleteView":  # a personal query, owner-only, no space
            continue
        assert _spaces_calls(classes[view]) & set(MANAGEMENT), f"{p.pattern} ({view}) makes no spaces management write"
        checked += 1
    assert checked >= 6, "fewer management routes than expected; did the check stop finding them?"
    assert get_resolver()  # the urlconf loads


def test_saved_conversations_go_through_the_gate_and_no_agent_surface_saves_one():
    """A conversation pointer is listed, saved and deleted only by a view that opens the
    space (``spaces.open``), and deleting one checks the role; no ``data_*`` tool and no
    page action saves or deletes one -- the person's own click does."""
    from connect_labs.labsdata import urls

    views_tree = _tree(LABSDATA / "views.py")
    classes = {n.name: n for n in ast.walk(views_tree) if isinstance(n, ast.ClassDef)}
    routes = [p for p in urls.urlpatterns if "conversations" in str(p.pattern)]
    assert len(routes) == 2
    for p in routes:
        view = classes[p.callback.view_class.__name__]
        assert _calls_open(view), f"{p.pattern} does not open the space through spaces.open"
    delete = next(p for p in routes if "delete" in str(p.pattern))
    assert "delete_conversation" in _spaces_calls(classes[delete.callback.view_class.__name__])
    tree = _tree(SPACES)
    save = _fn(tree, "save_conversation")
    assert "NotThisSpace" in _called_names(save), "save_conversation must refuse another page's conversation"
    assert not _spaces_calls(_tree(TOOLS)) & {"save_conversation", "delete_conversation", "conversations"}
    assert "conversations/" not in TOOLS.read_text()
    js = DATA_JS.read_text()
    actions = js[js.index("const ACTIONS = {") : js.index("window.LABSDATA_ACTIONS")]
    assert "conversation" not in actions.lower(), "a page action reaches saved conversations"


def test_no_agent_surface_can_change_sources_audience_or_managers():
    """No ``data_*`` tool and no page action names a management write: an agent acts as
    the visitor and only reads, runs and proposes. Those writes are person-session JSON
    views behind CSRF."""
    import connect_labs.mcp.tools.labsdata  # noqa: F401 -- registers the data_* tools
    from connect_labs.mcp.tool_registry import list_tools

    data_tools = [t["name"] for t in list_tools() if t["name"].startswith(("data_", "explorer_"))]
    assert data_tools, "no data_* tools registered"
    for name in data_tools:
        assert not any(w in name for w in MANAGEMENT_WORDS), f"tool {name} names a management write"
    calls = _spaces_calls(_tree(TOOLS))
    assert not calls & (set(MANAGEMENT) | {"require_manager", "save"}), f"data_* tools call {calls & set(MANAGEMENT)}"
    tools_src = TOOLS.read_text()
    for url in ("/sources/", "/audience/", "/managers/", "/archive/"):
        assert url not in tools_src

    js = DATA_JS.read_text()
    start, end = js.index("const ACTIONS = {"), js.index("window.LABSDATA_ACTIONS")
    actions = js[start:end]
    names = re.findall(r"^    (\w+): \[", actions, flags=re.M)
    assert {"showQuery", "openItem", "proposeSave", "showVisit"} <= set(names)
    for name in names:
        assert not any(w in name.lower() for w in MANAGEMENT_WORDS), f"page action {name} names a management write"
    for forbidden in (
        "'sources/'",
        "'audience/'",
        "'managers/'",
        "admin/managers/",
        "'archive/'",
        "saveSources",
        "archiveSpace",
        "manage",
    ):
        assert forbidden not in actions, f"a page action reaches {forbidden}"
