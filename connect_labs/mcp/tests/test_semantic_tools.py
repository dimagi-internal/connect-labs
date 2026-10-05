"""Tests for the semantic_registry_* MCP tools.

The one these exist for is `semantic_registry_explain`. Asked for every
indicator it used to return ~1.17M characters for the KMC registry -- 37
indicators, each carrying its own copy of one 27KB compiled statement -- so the
obvious first call from a second engine ("explain all of your logic") failed
outright against every client's tool-result limit
(dimagi-internal/connect-labs#1743).

Mirrors the pipeline tool tests: mock the data access, drive the real tool
in-process through `call_tool`.
"""

from __future__ import annotations

import copy
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

from connect_labs.labs.models import UserConnectToken
from connect_labs.mcp.models import MCPAccessToken
from connect_labs.mcp.testing import call_tool
from connect_labs.mcp.tool_registry import MCPToolError
from connect_labs.semantic.seed import registry_payload
from connect_labs.users.models import User

#: What a client will take in one tool result, roughly: Claude Code's default cap
#: is 25k tokens. Characters are the stable thing to assert on, at ~3.5 per token.
_CLIENT_RESULT_LIMIT_CHARS = 25_000 * 3


@pytest.fixture
def auth_user(db):
    user = User.objects.create(username="semantictest")
    _, raw = MCPAccessToken.create_token(user, name="t")
    UserConnectToken.objects.create(
        user=user,
        access_token="connect-tok",
        expires_at=timezone.now() + timedelta(hours=1),
    )
    return user, raw


@pytest.fixture
def kmc_record():
    """A registry record holding the real KMC documents, straight off disk."""
    payload = registry_payload("kmc")
    return MagicMock(
        id=5500,
        version=11,
        properties_doc=payload["properties"],
        indicators_doc=payload["indicators"],
        deployment=payload["deployment"],
    )


def _explain(raw, kmc_record, arguments):
    with patch("connect_labs.mcp.tools.semantic.SemanticRegistryDataAccess") as access_cls:
        access_cls.return_value.get_registry.return_value = kmc_record
        return call_tool(raw, "semantic_registry_explain", arguments)


@pytest.mark.django_db
def test_explaining_everything_returns_an_index_a_client_can_actually_receive(auth_user, kmc_record):
    _, raw = auth_user

    data = _explain(raw, kmc_record, {"registry_id": 5500})

    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]
    assert len(json.dumps(content)) < _CLIENT_RESULT_LIMIT_CHARS, "the index is too big to return in one result"
    # Every top-level indicator is named, so nothing is hidden by the summary.
    named = {row["indicator"] for row in content["indicators"]}
    assert {"total_cases", "mortality", "pct_impossible_weight_changes"} <= named
    # ...as one line each, not a chain, and with no compiled statement per row.
    row = next(r for r in content["indicators"] if r["indicator"] == "mortality")
    assert row["title"] == "Mortality"
    # Both readings: the authored wording, and the one rendered from the SQL so it
    # cannot drift from the number.
    assert "deaths" in row["plain"].lower()
    assert "died" in row["definition"].lower()
    assert "properties" not in row and "compiled_sql" not in row
    assert "Call again with `indicators`" in content["detail"]


@pytest.mark.django_db
def test_naming_indicators_returns_their_full_chain(auth_user, kmc_record):
    _, raw = auth_user

    data = _explain(raw, kmc_record, {"registry_id": 5500, "indicators": ["mortality"]})

    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]
    explanation = content["indicators"][0]
    assert explanation["indicator"] == "mortality"
    # The chain a second engine needs to reproduce the number.
    assert explanation["expression"]["compiled"]
    assert [c["name"] for c in explanation["components"]] == ["mortality_numerator", "mortality_denominator"]
    assert {p["name"] for p in explanation["properties"]} >= {"died", "eligible_28d", "outcome_known"}
    assert explanation["constants"]["MATURITY_OUTCOME_DAYS"] == 28


@pytest.mark.django_db
def test_the_compiled_statement_comes_back_once_not_once_per_indicator(auth_user, kmc_record):
    """It is the whole scope's SELECT, identical for every indicator asked for.

    Returning a copy inside each one is what made asking for several cost
    megabytes.
    """
    _, raw = auth_user

    data = _explain(
        raw,
        kmc_record,
        {"registry_id": 5500, "indicators": ["mortality", "lost_by_day_28", "pct_impossible_weight_changes"]},
    )

    content = data["result"]["structuredContent"]
    assert "pipeline_visit_rows" in content["compiled_sql"]
    assert content["layer1"].startswith("Visit rows come from")
    assert len(content["indicators"]) == 3
    for explanation in content["indicators"]:
        assert "compiled_sql" not in explanation, "the statement is repeated per indicator again"


@pytest.mark.django_db
def test_the_scope_still_decides_what_the_statement_groups_by(auth_user, kmc_record):
    _, raw = auth_user

    data = _explain(raw, kmc_record, {"registry_id": 5500, "indicators": ["mortality"], "scope": "llo"})

    content = data["result"]["structuredContent"]
    assert content["scope"] == "llo"
    assert "GROUP BY props.llo" in content["compiled_sql"]


@pytest.mark.django_db
def test_an_unknown_indicator_is_still_a_clean_not_found(auth_user, kmc_record):
    _, raw = auth_user

    data = _explain(raw, kmc_record, {"registry_id": 5500, "indicators": ["no_such_indicator"]})

    assert data["result"]["isError"] is True
    assert data["result"]["structuredContent"]["error"]["code"] == "NOT_FOUND"


class TestSetIndicatorMeta:
    """A one-key registry edit must not require round-tripping the whole
    indicators document — tens of thousands of tokens each way for a one-word
    change, every byte a chance to corrupt a live registry by transcription."""

    @staticmethod
    def _doc():
        return {
            "cube": "kmc_case",
            "version": 1,
            "measures": [
                {
                    "name": "mean_early_growth_rate",
                    "type": "number",
                    "title": "Growth",
                    "sql": "{mean_early_growth_rate_numerator}",
                    "meta": {"indicator": "mean_early_growth_rate", "unit": "g/kg/d", "category": "Growth quality"},
                },
                {"name": "mean_early_growth_rate_numerator", "type": "avg", "sql": "{CUBE}.early_velocity"},
                {
                    "name": "lost_by_day_28",
                    "type": "number",
                    "title": "LTFU",
                    "sql": "{lost_by_day_28_numerator}",
                    "meta": {"indicator": "lost_by_day_28", "unit": "%", "category": "Follow-up"},
                },
                {"name": "lost_by_day_28_numerator", "type": "count"},
            ],
        }

    def _patched(self, monkeypatch, patches):
        from connect_labs.mcp.tools import semantic as tools

        captured = {}
        record = SimpleNamespace(
            indicators_doc=self._doc(),
            version=3,
            id=1,
            name="r",
            description="",
            properties_doc={},
            deployment={},
            is_shared=True,
        )

        class _Access:
            def get_registry(self, rid):
                return record

            def update_registry(self, rid, indicators=None, **kw):
                captured["indicators"] = indicators
                return SimpleNamespace(**{**record.__dict__, "indicators_doc": indicators, "version": 4})

            def close(self):
                pass

        monkeypatch.setattr(tools, "_access", lambda *a, **k: _Access())
        monkeypatch.setattr(tools, "_summary", lambda r: {"id": 1})
        out = tools.semantic_registry_set_indicator_meta(user=None, registry_id=1, patches=patches)
        return out, captured["indicators"]

    def _meta(self, doc, indicator):
        return next(m["meta"] for m in doc["measures"] if (m.get("meta") or {}).get("indicator") == indicator)

    def test_it_sets_the_key_and_touches_nothing_else(self, monkeypatch):
        _, doc = self._patched(monkeypatch, {"mean_early_growth_rate": {"benchmarkable": True}})
        assert self._meta(doc, "mean_early_growth_rate")["benchmarkable"] is True
        # every other key on the same indicator survives...
        assert self._meta(doc, "mean_early_growth_rate")["unit"] == "g/kg/d"
        # ...and so does every other indicator, untouched.
        assert self._meta(doc, "lost_by_day_28") == {
            "indicator": "lost_by_day_28",
            "unit": "%",
            "category": "Follow-up",
        }
        assert len(doc["measures"]) == 4

    def test_a_null_removes_a_key(self, monkeypatch):
        _, doc = self._patched(monkeypatch, {"mean_early_growth_rate": {"unit": None}})
        assert "unit" not in self._meta(doc, "mean_early_growth_rate")

    def test_an_unknown_indicator_is_refused_not_created(self, monkeypatch):
        """A typo in an indicator id would otherwise write a meta block that
        nothing reads, and report success."""
        with pytest.raises(MCPToolError) as exc:
            self._patched(monkeypatch, {"C99": {"benchmarkable": True}})
        assert exc.value.code == "NOT_FOUND"
        assert "C99" in str(exc.value)

    def test_it_does_not_mutate_the_record_it_read(self, monkeypatch):
        """The patch is applied to a COPY. Mutating the loaded record in place
        would leave a half-applied edit behind if validation then rejected it."""
        from connect_labs.mcp.tools import semantic as tools

        record = SimpleNamespace(indicators_doc=self._doc(), version=3)
        original = copy.deepcopy(record.indicators_doc)

        class _Access:
            def get_registry(self, rid):
                return record

            def update_registry(self, rid, indicators=None, **kw):
                return SimpleNamespace(indicators_doc=indicators, version=4)

            def close(self):
                pass

        monkeypatch.setattr(tools, "_access", lambda *a, **k: _Access())
        monkeypatch.setattr(tools, "_summary", lambda r: {"id": 1})
        tools.semantic_registry_set_indicator_meta(
            user=None, registry_id=1, patches={"mean_early_growth_rate": {"benchmarkable": True}}
        )
        assert record.indicators_doc == original, "the record it read was mutated in place"


class TestItemLevelUpdate:
    """`semantic_registry_update` item edits (#2224): a small change names the
    items it touches instead of resending a ~50 KB document, and is validated
    against the MERGED registry exactly like a whole-document write."""

    NEW_TRIO = [
        {
            "name": "registered_share",
            "title": "Registered share",
            "type": "number",
            "sql": "{registered_share_numerator} / NULLIF({registered_share_denominator}, 0)",
        },
        {"name": "registered_share_numerator", "type": "count", "filters": [{"sql": "{CUBE}.registered"}]},
        {"name": "registered_share_denominator", "type": "count"},
    ]

    @staticmethod
    def _access(version=7):
        """The REAL data access -- validation, merge and version bump included --
        over a mocked labs API holding the KMC registry."""
        from connect_labs.workflow.data_access import SemanticRegistryDataAccess, SemanticRegistryRecord

        payload = registry_payload("kmc")
        record = SemanticRegistryRecord(
            {
                "id": 77,
                "experiment": "semantic",
                "type": "semantic_registry",
                "data": {
                    "name": "kmc test",
                    "description": "",
                    "version": version,
                    "properties": payload["properties"],
                    "indicators": payload["indicators"],
                    "deployment": payload["deployment"],
                    "is_shared": False,
                },
                "opportunity_id": None,
            }
        )
        access = SemanticRegistryDataAccess.__new__(SemanticRegistryDataAccess)
        access.labs_api = MagicMock()
        access.labs_api.get_record_by_id.return_value = record
        access.opportunity_id = None
        access.close = lambda: None
        return access, record

    def _update(self, monkeypatch, access, **kwargs):
        from connect_labs.mcp.tools import semantic as tools

        monkeypatch.setattr(tools, "_access", lambda *a, **k: access)
        return tools.semantic_registry_update(user=None, registry_id=77, **kwargs)

    @staticmethod
    def _written(access):
        assert access.labs_api.update_record.call_count == 1
        return access.labs_api.update_record.call_args.kwargs["data"]

    @staticmethod
    def _names(items):
        return [i["name"] for i in items]

    def test_upserting_a_new_measure_appends_it_and_bumps_the_version(self, monkeypatch):
        access, record = self._access()
        before = self._names(record.indicators_doc["measures"])

        out = self._update(monkeypatch, access, upsert_measures=self.NEW_TRIO)

        data = self._written(access)
        names = self._names(data["indicators"]["measures"])
        assert names == before + ["registered_share", "registered_share_numerator", "registered_share_denominator"]
        assert data["version"] == 8
        assert out["_version_before"] == 7 and out["_version_after"] == 8
        assert out["items_changed"]["measures"]["upserted"][0] == "registered_share"
        # the other document is written back untouched
        assert data["properties"] == record.properties_doc

    def test_the_payload_for_one_indicator_is_small(self):
        assert len(json.dumps({"registry_id": 77, "upsert_measures": self.NEW_TRIO})) < 2048

    def test_upserting_an_existing_name_replaces_it_in_place(self, monkeypatch):
        access, record = self._access()
        before = self._names(record.indicators_doc["measures"])
        replacement = {**record.indicators_doc["measures"][3], "title": "Registered babies"}

        self._update(monkeypatch, access, upsert_measures=[replacement], insert_after="total_cases")

        measures = self._written(access)["indicators"]["measures"]
        assert self._names(measures) == before, "a replacement moved or duplicated a measure"
        assert measures[3]["title"] == "Registered babies"

    def test_insert_after_places_new_items_beside_their_neighbour_in_order(self, monkeypatch):
        access, record = self._access()

        self._update(monkeypatch, access, upsert_measures=self.NEW_TRIO, insert_after="registered_cases_denominator")

        names = self._names(self._written(access)["indicators"]["measures"])
        at = names.index("registered_cases_denominator")
        assert names[at + 1 : at + 4] == [
            "registered_share",
            "registered_share_numerator",
            "registered_share_denominator",
        ]

    def test_remove_drops_items_by_name(self, monkeypatch):
        access, record = self._access()
        self._update(monkeypatch, access, upsert_measures=self.NEW_TRIO)
        written = self._written(access)
        record.data.update(indicators=written["indicators"], version=written["version"])
        access.labs_api.update_record.reset_mock()

        out = self._update(monkeypatch, access, remove={"measures": self._names(self.NEW_TRIO)}, expected_version=8)

        data = self._written(access)
        assert "registered_share" not in self._names(data["indicators"]["measures"])
        assert data["version"] == 9
        assert out["items_changed"]["measures"]["removed"] == self._names(self.NEW_TRIO)

    def test_properties_and_aggregates_upsert_into_the_properties_document(self, monkeypatch):
        access, record = self._access()
        aggregate = {"name": "visit_count_again", "label": "Visits", "sql": "COUNT(*)"}
        prop = {"name": "many_visits", "label": "Many visits", "type": "bool", "sql": "visit_count_again >= 5"}

        self._update(
            monkeypatch,
            access,
            upsert_aggregates=[aggregate],
            upsert_properties=[prop],
            insert_after={"aggregates": "num_visits"},
        )

        props = self._written(access)["properties"]
        assert self._names(props["aggregates"])[1] == "visit_count_again"
        assert self._names(props["properties"])[-1] == "many_visits"
        assert props["aggregates"][0] == record.properties_doc["aggregates"][0]

    def test_a_version_conflict_is_refused_naming_the_current_version(self, monkeypatch):
        access, _ = self._access(version=7)

        with pytest.raises(MCPToolError) as exc:
            self._update(monkeypatch, access, upsert_measures=self.NEW_TRIO, expected_version=6)

        assert exc.value.code == "VERSION_CONFLICT"
        assert "version 7" in str(exc.value)
        access.labs_api.update_record.assert_not_called()

    def test_a_merged_result_that_fails_validation_saves_nothing(self, monkeypatch):
        access, record = self._access()
        original = copy.deepcopy(record.data)
        broken = {"name": "broken", "title": "Broken", "type": "number", "sql": "{no_such_measure}"}

        with pytest.raises(MCPToolError) as exc:
            self._update(monkeypatch, access, upsert_measures=[broken])

        assert exc.value.code == "INVALID_SCHEMA"
        access.labs_api.update_record.assert_not_called()
        assert record.data == original, "the record it read was mutated"

    def test_removing_a_property_something_still_uses_is_refused(self, monkeypatch):
        """Validation runs on the MERGED registry: a removal that orphans a
        measure is caught even though the measure itself was not touched."""
        access, _ = self._access()

        with pytest.raises(MCPToolError) as exc:
            self._update(monkeypatch, access, remove={"properties": ["registered"]})

        assert exc.value.code == "INVALID_SCHEMA"
        access.labs_api.update_record.assert_not_called()

    @pytest.mark.parametrize(
        "kwargs, code",
        [
            ({"remove": {"measures": ["no_such_measure"]}}, "NOT_FOUND"),
            ({"upsert_measures": NEW_TRIO, "insert_after": "no_such_measure"}, "NOT_FOUND"),
            ({"upsert_measures": [{"title": "no name"}]}, "INVALID_SCHEMA"),
            (
                {"upsert_properties": [{"name": "p", "type": "bool", "sql": "TRUE"}], "insert_after": "total_cases"},
                "INVALID_SCHEMA",
            ),
            ({"upsert_measures": NEW_TRIO, "insert_after": {"aggregates": "num_visits"}}, "INVALID_SCHEMA"),
            ({"upsert_measures": [NEW_TRIO[1], NEW_TRIO[1]]}, "INVALID_SCHEMA"),
            ({"upsert_measures": NEW_TRIO, "indicators_doc": {"measures": []}}, "INVALID_SCHEMA"),
        ],
    )
    def test_ambiguous_or_mistyped_edits_are_refused_before_any_write(self, monkeypatch, kwargs, code):
        access, _ = self._access()

        with pytest.raises(MCPToolError) as exc:
            self._update(monkeypatch, access, **kwargs)

        assert exc.value.code == code
        access.labs_api.update_record.assert_not_called()

    def test_the_whole_document_path_is_unchanged(self, monkeypatch):
        access, record = self._access()
        doc = copy.deepcopy(record.indicators_doc)
        doc["measures"][0]["title"] = "All babies"

        out = self._update(monkeypatch, access, indicators_doc=doc)

        data = self._written(access)
        assert data["indicators"] == doc
        assert data["version"] == 8
        assert "items_changed" not in out

    @pytest.mark.django_db
    def test_the_published_schema_accepts_item_edits(self, auth_user):
        _, raw = auth_user
        access, _ = self._access()

        with patch("connect_labs.mcp.tools.semantic.SemanticRegistryDataAccess", return_value=access):
            data = call_tool(
                raw,
                "semantic_registry_update",
                {
                    "registry_id": 77,
                    "upsert_measures": self.NEW_TRIO,
                    "insert_after": "total_cases_denominator",
                    "expected_version": 7,
                },
            )

        assert data["result"]["isError"] is False, data
        content = data["result"]["structuredContent"]
        assert content["version"] == 8
        assert content["items_changed"]["measures"]["upserted"][0] == "registered_share"
