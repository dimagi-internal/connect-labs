"""A view that queries a model directly must still say which programme.

`SupplyDataAccess` authorises a caller's programme once, in its constructor,
and `test_scope_authorisation` pins that. But it only protects reads that go
THROUGH it. A view holding `Contract.objects.filter(pk=contract_id)` has
stepped around the gate entirely, and the id in that filter came off the URL.

Nothing caught that shape, and it was in the tree: `views._link_updates`
re-fetched a contract by bare primary key, reasoning in its docstring that
the caller had already fetched the same contract through the scoped
`contract_get`. The reasoning was sound and the protection was a comment --
one new caller away from being wrong.

So this reads every view module and checks the queries themselves. It is a
static check on purpose: it costs nothing, it runs on code nobody has written
yet, and the defect it prevents is one a reviewer has to notice by eye.

The rule: a query on a model that carries its own scope column must filter on
that column. A query on a child record -- an Award, a Quote, an Invoice --
must reach a scope through a relation (`round__program_id`). Anything else
has to be named in ALLOWED below, with the reason it is safe.
"""

import ast
import pathlib

import pytest
from django.apps import apps

SUPPLY = pathlib.Path(__file__).resolve().parent.parent

# A query proves it is scoped by naming one of these in a keyword.
SCOPE_FIELDS = ("program_id", "scope_key", "opportunity_id")

# Reads that are genuinely not programme-scoped, each with the reason.
# A new entry here is a claim somebody has to defend in review.
ALLOWED = {
    # LabsOrg and the marketplace profiles hanging off it are labs-wide by
    # design -- an organisation is not owned by a programme. marketplace/
    # README calls LabsOrg the master registry.
    "LabsOrg",
    "OrgMembership",
    "SupplierProfile",
    "SupplierOffering",
    # Portfolio is the one model in this app with no programme scope, and its
    # view re-derives reachability from get_org_data per row. Its own tests
    # pin that; see PortfolioView.
    "Portfolio",
    # Not a supply model at all.
    "User",
    # An update link IS the authorisation: the token identifies the link, and
    # the link carries the programme. update_links/service.scope_for() does
    # the scoping, and test_update_links pins it.
    "UpdateLinkSubmission",
}


def _view_modules():
    return sorted(
        path for path in SUPPLY.rglob("*views*.py") if "tests" not in path.parts and "__pycache__" not in path.parts
    )


def _scoped_model_names():
    """Models carrying their own scope column, from the app registry itself.

    Read from Django rather than listed here, so a new scoped model is
    covered the day it is added rather than the day somebody remembers.
    """
    names = set()
    for model in apps.get_app_config("supply_chain").get_models():
        fields = {f.name for f in model._meta.get_fields()}
        if fields & set(SCOPE_FIELDS):
            names.add(model.__name__)
    return names


# Manager and queryset methods that carry the scope in their NAME rather than
# in a keyword. `Movement.objects.for_program(pid)` is scoped; the checker
# cannot see that from keywords alone, and pretending otherwise would train
# people to ignore this test.
SCOPED_BY_NAME = ("for_program", "for_scope", "in_scope")


def _queries(tree):
    """Every `Model.objects....` chain, with the keywords used anywhere in it.

    Chains matter. `Item.objects.select_related("commodity").in_bulk(ids)`
    parses as a call on a call, and looking only at the first link sees
    `select_related` with no scope and reports a query that is fine. So the
    whole chain is walked and its keywords pooled: a scope named at any link
    scopes the read.
    """
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Walk down the chain collecting every call's keywords and methods,
        # stopping at `<Name>.objects`.
        keywords, methods, cursor = [], [], node
        while isinstance(cursor, ast.Call) and isinstance(cursor.func, ast.Attribute):
            keywords.extend(kw.arg or "" for kw in cursor.keywords)
            methods.append(cursor.func.attr)
            cursor = cursor.func.value
        if not (isinstance(cursor, ast.Attribute) and cursor.attr == "objects"):
            continue
        if not isinstance(cursor.value, ast.Name):
            continue
        # Only the outermost call of a chain is reported, so one chain does
        # not produce one complaint per link.
        parent_is_chain = any(
            isinstance(other, ast.Call) and isinstance(other.func, ast.Attribute) and other.func.value is node
            for other in ast.walk(tree)
        )
        if parent_is_chain:
            continue
        found.append((cursor.value.id, methods, keywords, node.lineno))
    return found


def _is_scoped(keywords):
    return any(any(field in kw for field in SCOPE_FIELDS) for kw in keywords)


@pytest.mark.parametrize("path", _view_modules(), ids=lambda p: f"{p.parent.name}/{p.name}")
def test_a_view_query_names_the_scope_it_is_reading(path):
    scoped_models = _scoped_model_names()
    offenders = []
    for model, methods, keywords, line in _queries(ast.parse(path.read_text())):
        method = methods[0]
        if model in ALLOWED or set(methods) & set(SCOPED_BY_NAME):
            continue
        if model in scoped_models and not _is_scoped(keywords):
            offenders.append(f"{path.name}:{line} {model}.objects.{method}({', '.join(keywords) or '...'})")
        elif model not in scoped_models and not _is_scoped(keywords) and set(methods) & {"filter", "get"}:
            # A child record reaches its scope through a relation. Without one
            # it is being looked up by bare id off a URL.
            offenders.append(
                f"{path.name}:{line} {model}.objects.{method}({', '.join(keywords) or '...'}) — no relation to a scope"
            )

    assert not offenders, (
        "these view queries do not say which programme they are reading:\n  "
        + "\n  ".join(offenders)
        + "\n\nFilter on program_id/scope_key (or a relation reaching one), read it through "
        "`self.op(...)` so SupplyDataAccess authorises it, or add the model to ALLOWED with "
        "the reason it is not programme-scoped."
    )
