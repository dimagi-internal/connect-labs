"""Field discovery, scoping, the service + audit trail, the MCP gates, the page."""

import hashlib
import json
from datetime import date, timedelta
from unittest import mock

import pytest
from django.utils import timezone

from connect_labs.explorer import fields, scope, service
from connect_labs.explorer.scope import Opportunity, ScopeError
from connect_labs.labs.access import scopes
from connect_labs.labs.analysis.backends.sql.models import RawVisitCache
from connect_labs.labs.analysis.config import USER_VISITS_RAW_SLOT

pytestmark = pytest.mark.django_db

OPP = Opportunity(id=911, name="Opp F", llo="LLO F")


def _visit(n, form):
    return RawVisitCache(
        opportunity_id=OPP.id,
        pipeline_id=USER_VISITS_RAW_SLOT,
        visit_count=50,
        expires_at=timezone.now() + timedelta(hours=1),
        visit_id=f"v{n}",
        username="flw",
        visit_date=date(2026, 9, 1) + timedelta(days=n % 20),
        deliver_unit="Follow-up",
        form_json=form,
    )


@pytest.fixture
def forms():
    rows = []
    for n in range(40):
        rows.append(
            _visit(
                n,
                {
                    "@xmlns": "http://openrosa.org/formdesigner/x",
                    "form": {
                        "birth": {"place": ["hospital", "home", "clinic"][n % 3]},
                        "mother_name": f"Person {n}",  # unique per visit: never shown
                        "rare": "once" if n == 0 else "usual",  # one value seen once: never shown
                        "weights": [{"w": 1000 + n}, {"w": 1100 + n}],
                        "note": "" if n % 2 else "x",
                    },
                    "meta": {"instanceID": f"uuid-{n}"},
                },
            )
        )
    RawVisitCache.objects.bulk_create(rows)


def _field(described, path):
    return next(f for f in described["fields"] if f["path"] == path)


def test_categorical_values_shown_identifiers_suppressed(forms):
    d = fields.describe_fields([OPP])
    assert d["sampled_visits"] == 40
    place = _field(d, "form.birth.place")
    assert {v["value"] for v in place["values"]} == {"hospital", "home", "clinic"}
    assert place["sql"] == "form_json #>> '{form,birth,place}'"
    assert place["filled_pct"] == 100.0
    assert "values" not in _field(d, "form.mother_name")
    assert "values" not in _field(d, "form.rare")
    assert _field(d, "form.note")["filled_pct"] == 50.0
    assert "jsonb_array_elements" in _field(d, "form.weights.[].w")["sql"]
    assert not any(f["path"].startswith("@") for f in d["fields"])


def test_one_visit_repeating_a_value_cannot_reveal_it():
    # Visit 0 repeats a rare value six times in a repeat group; every other visit
    # carries the common one. Counted per leaf, "Rare" would clear MIN_VALUE_COUNT.
    rows = [_visit(0, {"form": {"kids": [{"surname": "Rare"}] * 6}})]
    rows += [_visit(n, {"form": {"kids": [{"surname": "Common"}]}}) for n in range(1, 10)]
    RawVisitCache.objects.bulk_create(rows)
    field = _field(fields.describe_fields([OPP]), "form.kids.[].surname")
    assert "values" not in field
    assert field["distinct_in_sample"] == 2


def test_field_search_matches_paths_and_values(forms):
    d = fields.search_fields(fields.describe_fields([OPP]), "hospital")
    assert [f["path"] for f in d["fields"]] == ["form.birth.place"]


def test_cache_status(forms):
    [s] = fields.cache_status([OPP])
    assert s["cached_visits"] == 40 and s["llo"] == "LLO F"


def _caller():
    return scopes.Caller(access_token="t")


def test_resolve_refuses_an_unheld_opportunity():
    with (
        mock.patch.object(scopes, "opportunity_ids", return_value={1, 2}),
        mock.patch.object(scope, "directory", return_value={}),
    ):
        assert [o.id for o in scope.resolve(_caller(), [2, 1])] == [1, 2]
        with pytest.raises(ScopeError) as e:
            scope.resolve(_caller(), [1, 3])
    assert e.value.code == "not_held" and "3" in e.value.message


def test_resolve_unknowable_is_not_empty():
    with mock.patch.object(scopes, "opportunity_ids", side_effect=scopes.ScopesUnavailable()):
        with pytest.raises(ScopeError) as e:
            scope.resolve(_caller(), [1])
    assert e.value.code == "scopes_unavailable"


def test_resolve_bounds():
    with pytest.raises(ScopeError):
        scope.resolve(_caller(), [])
    with pytest.raises(ScopeError):
        scope.resolve(_caller(), list(range(scope.MAX_OPPORTUNITIES + 1)))


def test_directory_names_and_llo():
    tree = {
        "organizations": [{"slug": "llo-f", "name": "LLO F"}],
        "opportunities": [{"id": 911, "name": "Opp F", "organization": "llo-f"}],
    }
    with mock.patch.object(scope, "_org_tree", return_value=tree):
        assert scope.directory(_caller()) == {911: OPP}


def test_query_writes_the_audit_trail(forms):
    from connect_labs.audit_trail.models import AuditEvent

    with mock.patch.object(scope, "resolve", return_value=[OPP]):
        out = service.query(_caller(), [OPP.id], "SELECT COUNT(*) FROM visits")
    assert out["rows"] == [[40]]
    event = AuditEvent.objects.filter(resource_type=service.RESOURCE_TYPE).latest("id")
    assert event.opportunity_id == OPP.id and event.record_count == 1
    # A hash, never the SQL: a query can hold a name or an answer as a literal.
    assert "sql" not in event.metadata
    assert event.metadata["sql_sha256"] == hashlib.sha256(b"SELECT COUNT(*) FROM visits").hexdigest()


def test_tools_are_registered_gated_and_resolved():
    from connect_labs.labs.canopy import PAGE_SCOPES, SCOPE_TOOLS
    from connect_labs.mcp import token_scopes, visit_access
    from connect_labs.mcp.tool_registry import _REGISTRY

    for name in ("explorer_describe", "explorer_query"):
        assert name in _REGISTRY
        assert name in token_scopes.USERVISIT_DATA_TOOLS
        assert name in token_scopes.GENERATED_ONLY_TOOLS
        assert visit_access.RESOLVERS[name](None, {"opportunity_ids": [5, "6"]}) == [5, 6]
        assert name in SCOPE_TOOLS["explorer:read"]
    assert visit_access.RESOLVERS["explorer_query"](None, {}) is None
    assert PAGE_SCOPES["explorer:index"] == ("explorer:read",)


def test_page_and_api(client, django_user_model, forms):
    user = django_user_model.objects.create_user(username="explorer-test", password="x")
    client.force_login(user)
    with mock.patch.object(scope, "directory", return_value={OPP.id: OPP}):
        page = client.get(f"/labs/explorer/?opps={OPP.id}")
    assert page.status_code == 200
    assert b"SQL explorer" in page.content and b"Opp F" in page.content

    with mock.patch.object(scope, "resolve", return_value=[OPP]):
        ok = client.post(
            "/labs/explorer/api/query/",
            json.dumps({"opportunity_ids": [OPP.id], "sql": "SELECT COUNT(*) AS n FROM visits"}),
            content_type="application/json",
        )
        bad = client.post(
            "/labs/explorer/api/query/",
            json.dumps({"opportunity_ids": [OPP.id], "sql": "SELECT * FROM users_user"}),
            content_type="application/json",
        )
    assert ok.status_code == 200 and ok.json()["rows"] == [[40]]
    assert bad.status_code == 400 and bad.json()["error"] == "table_not_allowed"

    with mock.patch.object(scope, "resolve", side_effect=ScopeError("not_held", "no")):
        denied = client.post(
            "/labs/explorer/api/describe/", json.dumps({"opportunity_ids": [1]}), content_type="application/json"
        )
    assert denied.status_code == 403
