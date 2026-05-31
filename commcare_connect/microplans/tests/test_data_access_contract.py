"""Contract tests for the microplans data-access layer.

These tests exercise the REAL ProgramPlanDataAccess class
against a faithful in-memory fake of LabsRecordAPIClient. The key invariant under
test: any value written via a DA method must survive the full
    DA.save → LocalLabsRecord.to_api_dict() → ProxyModel(api_data) → .property
round-trip, exactly as the production JSON round-trip would treat it.

The fake stores data as json.loads(json.dumps(data)) so tuples→lists and any
non-JSON-native value surfaces exactly as the real production API would return it.
This is what test_plan.py's hand-constructed LocalLabsRecord fixtures do NOT catch.
"""

from __future__ import annotations

import json
from typing import Any

from commcare_connect.labs.models import LocalLabsRecord
from commcare_connect.microplans.core import plan as plan_lib
from commcare_connect.microplans.core.data_access import ProgramPlanDataAccess

# ---------------------------------------------------------------------------
# In-memory fake of LabsRecordAPIClient
# ---------------------------------------------------------------------------


class FakeLabsRecordAPIClient:
    """Faithful in-memory fake of LabsRecordAPIClient.

    Stores records exactly as the production API would echo them back:
    data is round-tripped through JSON so tuples→lists, None vs missing keys,
    and other Python-vs-JSON differences surface in the same tests.

    Does NOT require Django settings / HTTP / OAuth.
    """

    def __init__(
        self,
        opportunity_id: int | None = None,
        organization_id: int | None = None,
        program_id: int | None = None,
    ):
        self.opportunity_id = opportunity_id
        self.organization_id = organization_id
        self.program_id = program_id
        self._store: dict[int, dict] = {}
        self._next_id = 1

    def _json_round_trip(self, value: Any) -> Any:
        """Simulate what the production API does: serialize+deserialize through JSON.
        Tuples become lists; non-JSON-native values raise immediately.
        """
        return json.loads(json.dumps(value))

    def _make_echo(
        self,
        record_id: int,
        experiment: str,
        type: str,
        data: dict,
        username: str | None = None,
        program_id: int | None = None,
        labs_record_id: int | None = None,
        public: bool = False,
        opportunity_id: int | None = None,
        organization_id: int | None = None,
    ) -> dict:
        """Build the server-echo dict shape that production returns."""
        return {
            "id": record_id,
            "experiment": experiment,
            "type": type,
            "data": self._json_round_trip(data),
            "username": username,
            "opportunity_id": opportunity_id if opportunity_id is not None else self.opportunity_id,
            "organization_id": organization_id if organization_id is not None else self.organization_id,
            "program_id": program_id if program_id is not None else self.program_id,
            "labs_record_id": labs_record_id,
            "public": public,
        }

    def create_record(
        self,
        experiment: str,
        type: str,
        data: dict,
        username: str | None = None,
        program_id: int | None = None,
        labs_record_id: int | None = None,
        public: bool = False,
    ) -> LocalLabsRecord:
        record_id = self._next_id
        self._next_id += 1
        echo = self._make_echo(
            record_id=record_id,
            experiment=experiment,
            type=type,
            data=data,
            username=username,
            program_id=program_id,
            labs_record_id=labs_record_id,
            public=public,
        )
        self._store[record_id] = echo
        return LocalLabsRecord(echo)

    def update_record(
        self,
        record_id: int,
        experiment: str,
        type: str,
        data: dict,
        username: str | None = None,
        program_id: int | None = None,
        labs_record_id: int | None = None,
        public: bool | None = None,
        current_record: LocalLabsRecord | None = None,
    ) -> LocalLabsRecord:
        existing = self._store.get(record_id)
        if existing is None:
            raise KeyError(f"FakeLabsRecordAPIClient: record {record_id} not found")

        # Merge: keep existing metadata, overwrite data
        updated = dict(existing)
        updated["data"] = self._json_round_trip(data)
        if program_id is not None:
            updated["program_id"] = program_id
        if public is not None:
            updated["public"] = public
        self._store[record_id] = updated
        return LocalLabsRecord(updated)

    def get_record_by_id(
        self,
        record_id: int,
        experiment: str | None = None,
        type: str | None = None,
        model_class=None,
    ) -> LocalLabsRecord | None:
        echo = self._store.get(record_id)
        if echo is None:
            return None
        cls = model_class if model_class is not None else LocalLabsRecord
        return cls(echo)

    def get_records(
        self,
        experiment: str | None = None,
        type: str | None = None,
        program_id: int | None = None,
        model_class=None,
        **kwargs,
    ) -> list:
        cls = model_class if model_class is not None else LocalLabsRecord
        results = []
        for echo in self._store.values():
            if experiment is not None and echo["experiment"] != experiment:
                continue
            if type is not None and echo["type"] != type:
                continue
            if program_id is not None and echo.get("program_id") != program_id:
                continue
            results.append(cls(echo))
        return results

    def delete_record(self, record_id: int) -> None:
        self._store.pop(record_id, None)

    def delete_records(self, record_ids: list[int]) -> None:
        for rid in record_ids:
            self._store.pop(rid, None)


# ---------------------------------------------------------------------------
# Helpers for constructing DA instances without a real HTTP request
# ---------------------------------------------------------------------------

OPP_ID = 999
PROGRAM_ID = 133


def _make_program_da() -> ProgramPlanDataAccess:
    """Construct ProgramPlanDataAccess and swap its labs_api for the in-memory fake."""
    da = ProgramPlanDataAccess(program_id=PROGRAM_ID, access_token="test-token-stub")
    da.labs_api = FakeLabsRecordAPIClient(program_id=PROGRAM_ID)
    return da


# ---------------------------------------------------------------------------
# Minimal fixtures for frame/pins/hulls
# ---------------------------------------------------------------------------

_HULLS_2 = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[3.0, 6.0], [3.1, 6.0], [3.1, 6.1], [3.0, 6.1], [3.0, 6.0]]],
            },
            "properties": {
                "arm": "coverage",
                "cluster": "C0",
                "building_count": 80,
                "expected_visit_count": 80,
            },
        },
        {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[3.2, 6.0], [3.3, 6.0], [3.3, 6.1], [3.2, 6.1], [3.2, 6.0]]],
            },
            "properties": {
                "arm": "coverage",
                "cluster": "C1",
                "building_count": 60,
                "expected_visit_count": 60,
            },
        },
    ],
}

_PINS_2 = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [3.05, 6.05]},
            "properties": {
                "arm": "intervention",
                "cluster": "C0",
                "role": "primary",
                "order_in_cluster": 1,
            },
        },
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [3.25, 6.05]},
            "properties": {
                "arm": "intervention",
                "cluster": "C1",
                "role": "primary",
                "order_in_cluster": 1,
            },
        },
    ],
}

_EMPTY_FC = {"type": "FeatureCollection", "features": []}


# ===========================================================================
# ProgramPlanDataAccess contract tests
# ===========================================================================


class TestProgramPlanDataAccessContract:
    """Round-trip tests for ProgramPlanDataAccess through the fake API."""

    def test_create_plan_properties_survive_round_trip(self):
        """create_plan → PlanRecord: status, region, work_areas, mode all accessible."""
        da = _make_program_da()
        plan_rec = da.create_plan(
            region="Kano North LGA",
            name="Kano Draft 1",
            mode="coverage",
            pins=_EMPTY_FC,
            hulls=_HULLS_2,
        )

        assert plan_rec.status == plan_lib.PLAN_DRAFT
        assert plan_rec.region == "Kano North LGA"
        assert plan_rec.name == "Kano Draft 1"
        assert plan_rec.mode == "coverage"
        # program_id from base LocalLabsRecord instance attr (not a property shadow)
        assert plan_rec.program_id == PROGRAM_ID
        # data["opportunity_id"] starts None
        assert plan_rec.data.get("opportunity_id") is None
        assert len(plan_rec.work_areas) == 2

    def test_create_plan_data_opportunity_id_is_none_not_missing(self):
        """data['opportunity_id'] is explicitly None (not absent) at creation — regression guard."""
        da = _make_program_da()
        plan_rec = da.create_plan(region="Test", name="T", mode="coverage", pins=_EMPTY_FC, hulls=_HULLS_2)
        # The data key must exist and be None, not KeyError
        assert "opportunity_id" in plan_rec.data
        assert plan_rec.data["opportunity_id"] is None

    def test_get_plan_returns_fetched_plan(self):
        """get_plan fetches the record by id, returns PlanRecord with proxy props."""
        da = _make_program_da()
        plan_rec = da.create_plan(region="Lagos Island", name="Lagos", mode="coverage", pins=_EMPTY_FC, hulls=_HULLS_2)

        fetched = da.get_plan(plan_rec.id)
        assert fetched.id == plan_rec.id
        assert fetched.region == "Lagos Island"
        assert fetched.work_areas == plan_rec.work_areas

    def test_regroup_plan_audit_survives_round_trip(self):
        """regroup_plan → work-area audits appended + work_area_group updated + persisted."""
        da = _make_program_da()
        plan_rec = da.create_plan(region="R", name="N", mode="coverage", pins=_EMPTY_FC, hulls=_HULLS_2)

        new_grouping = {"strategy": "bbox", "target_size": 1}

        updated = da.regroup_plan(plan_id=plan_rec.id, grouping=new_grouping, actor="planner")

        # After bbox grouping with target_size=1, each cell gets its own group
        # so groups should differ from each other
        groups = [w["work_area_group"] for w in updated.work_areas]
        assert all(g.startswith("group-") for g in groups)

        # Re-fetch and verify audit survived
        refetched = da.get_plan(plan_rec.id)
        for wa in refetched.work_areas:
            if wa.get("audit"):
                ev = wa["audit"][0]
                assert ev["action"] == "regroup"
                assert ev["phase"] == plan_lib.PLANNING_PHASE
                assert "work_area_group" in ev["changes"]
                # old→new is a 2-element list after JSON round-trip (not tuple)
                assert isinstance(ev["changes"]["work_area_group"], list)
                assert len(ev["changes"]["work_area_group"]) == 2

    def test_reassign_plan_opportunity_access_survives_round_trip(self):
        """reassign_plan → opportunity_access fields updated + audit persisted."""
        da = _make_program_da()
        plan_rec = da.create_plan(region="R", name="N", mode="coverage", pins=_EMPTY_FC, hulls=_HULLS_2)
        assignment = {"strategy": "round_robin", "workers": ["flw-alice", "flw-bob"]}

        updated = da.reassign_plan(plan_id=plan_rec.id, assignment=assignment, actor="manager")

        # After round_robin assignment with 2 workers, cells get opportunity_access set
        workers = [w.get("opportunity_access") for w in updated.work_areas]
        assert all(w in ("flw-alice", "flw-bob") for w in workers if w is not None)

        # Re-fetch and verify persisted
        refetched = da.get_plan(plan_rec.id)
        refetched_workers = [w.get("opportunity_access") for w in refetched.work_areas]
        assert refetched_workers == workers

    def test_transition_plan_draft_to_in_review_status_log_persists(self):
        """transition_plan(in_review) appends status_log entry + updates status."""
        da = _make_program_da()
        plan_rec = da.create_plan(region="R", name="N", mode="coverage", pins=_EMPTY_FC, hulls=_HULLS_2)
        assert plan_rec.status == plan_lib.PLAN_DRAFT

        updated = da.transition_plan(plan_id=plan_rec.id, to=plan_lib.PLAN_IN_REVIEW, actor="admin")
        assert updated.status == plan_lib.PLAN_IN_REVIEW
        assert len(updated.status_log) == 1
        assert updated.status_log[0]["to"] == plan_lib.PLAN_IN_REVIEW
        assert updated.status_log[0]["from"] == plan_lib.PLAN_DRAFT

    def test_transition_plan_deploy_binds_opportunity_id(self):
        """Deploying a plan sets data['opportunity_id'] to the provided opp id."""
        da = _make_program_da()
        plan_rec = da.create_plan(region="R", name="N", mode="coverage", pins=_EMPTY_FC, hulls=_HULLS_2)
        # Advance through planning states first
        da.transition_plan(plan_id=plan_rec.id, to=plan_lib.PLAN_IN_REVIEW, actor="admin")
        da.transition_plan(plan_id=plan_rec.id, to=plan_lib.PLAN_APPROVED, actor="admin")
        updated = da.transition_plan(
            plan_id=plan_rec.id, to=plan_lib.PLAN_DEPLOYED, actor="admin", opportunity_id=1882
        )

        assert updated.status == plan_lib.PLAN_DEPLOYED
        # The deploy-bound opp lives in data (not the base record-level opportunity_id)
        assert updated.data.get("opportunity_id") == 1882

        # Full round-trip: re-fetch and verify
        refetched = da.get_plan(plan_rec.id)
        assert refetched.status == plan_lib.PLAN_DEPLOYED
        assert refetched.data.get("opportunity_id") == 1882
        # status_log has 3 entries
        assert len(refetched.status_log) == 3
        assert refetched.status_log[-1]["phase"] == "deploy"

    def test_transition_plan_status_log_is_list_after_round_trip(self):
        """status_log entries are dicts in a list — JSON round-trip doesn't corrupt them."""
        da = _make_program_da()
        plan_rec = da.create_plan(region="R", name="N", mode="coverage", pins=_EMPTY_FC, hulls=_HULLS_2)
        da.transition_plan(plan_id=plan_rec.id, to=plan_lib.PLAN_IN_REVIEW, actor="admin")

        refetched = da.get_plan(plan_rec.id)
        log = refetched.status_log
        assert isinstance(log, list)
        assert isinstance(log[0], dict)
        # All required keys present
        for key in ("ts", "actor", "from", "to", "phase"):
            assert key in log[0], f"status_log entry missing key: {key!r}"

    def test_apply_plan_edits_program_scoped_audit_survives(self):
        """ProgramPlanDataAccess.apply_plan_edits: audit appended + persists on re-fetch."""
        da = _make_program_da()
        plan_rec = da.create_plan(region="R", name="N", mode="coverage", pins=_EMPTY_FC, hulls=_HULLS_2)

        first_wa_id = plan_rec.work_areas[0]["id"]
        da.apply_plan_edits(
            plan_id=plan_rec.id,
            wa_ids=[first_wa_id],
            action="resize",
            params={"expected_visit_count": 40},
            actor="reviewer",
        )

        refetched = da.get_plan(plan_rec.id)
        wa = next(w for w in refetched.work_areas if w["id"] == first_wa_id)
        assert wa["expected_visit_count"] == 40
        assert len(wa["audit"]) == 1
        assert wa["audit"][0]["actor"] == "reviewer"

    def test_list_plans_program_scoped_returns_typed_records(self):
        """list_plans returns program-scoped PlanRecord list with proxy props."""
        da = _make_program_da()
        da.create_plan(region="R1", name="Plan 1", mode="coverage", pins=_EMPTY_FC, hulls=_HULLS_2)
        da.create_plan(region="R2", name="Plan 2", mode="coverage", pins=_EMPTY_FC, hulls=_HULLS_2)

        plans = da.list_plans()
        assert len(plans) == 2
        regions = {p.region for p in plans}
        assert regions == {"R1", "R2"}
        for p in plans:
            assert p.program_id == PROGRAM_ID

    # ---- plan group round-trips ----

    def test_create_group_plan_ids_are_ints_after_round_trip(self):
        """create_group: plan_ids stored as ints (not strings) after JSON round-trip."""
        da = _make_program_da()
        grp = da.create_group(name="Hilltop Group", plan_ids=[1, 2, 3], offered_to="Hilltop Health")

        assert grp.name == "Hilltop Group"
        assert grp.plan_ids == [1, 2, 3]
        # Verify they're ints — the DA explicitly casts to int; check it survives
        assert all(isinstance(pid, int) for pid in grp.plan_ids)
        assert grp.offered_to == "Hilltop Health"
        assert grp.shared is False

    def test_get_group_round_trip(self):
        """get_group returns PlanGroupRecord with proxy properties."""
        da = _make_program_da()
        grp = da.create_group(name="Test Group", plan_ids=[10, 20])

        fetched = da.get_group(grp.id)
        assert fetched.id == grp.id
        assert fetched.name == "Test Group"
        assert fetched.plan_ids == [10, 20]

    def test_update_group_shared_toggle_persists(self):
        """update_group(shared=True) → fetched group shows shared=True."""
        da = _make_program_da()
        grp = da.create_group(name="G", plan_ids=[1])
        assert grp.shared is False

        updated = da.update_group(group_id=grp.id, shared=True)
        assert updated.shared is True

        refetched = da.get_group(grp.id)
        assert refetched.shared is True

    def test_update_group_plan_ids_coerced_to_int_after_round_trip(self):
        """update_group(plan_ids=[...]) coerces to int and survives JSON round-trip."""
        da = _make_program_da()
        grp = da.create_group(name="G", plan_ids=[1])

        updated = da.update_group(group_id=grp.id, plan_ids=[5, 6, 7])
        assert updated.plan_ids == [5, 6, 7]
        assert all(isinstance(pid, int) for pid in updated.plan_ids)

    def test_list_groups_returns_typed_records(self):
        """list_groups returns PlanGroupRecord instances with proxy props."""
        da = _make_program_da()
        da.create_group(name="Group A", plan_ids=[1])
        da.create_group(name="Group B", plan_ids=[2, 3])

        groups = da.list_groups()
        assert len(groups) == 2
        names = {g.name for g in groups}
        assert names == {"Group A", "Group B"}
        for g in groups:
            _ = g.plan_ids  # proxy property
            _ = g.shared  # proxy property

    def test_delete_group_removes_from_list(self):
        """delete_group removes the record; list_groups no longer returns it."""
        da = _make_program_da()
        grp = da.create_group(name="To Delete", plan_ids=[1])
        assert len(da.list_groups()) == 1

        da.delete_group(grp.id)
        assert len(da.list_groups()) == 0

    def test_delete_plan_removes_from_list(self):
        """delete_plan removes the record; list_plans no longer returns it."""
        da = _make_program_da()
        plan_rec = da.create_plan(region="R", name="N", mode="coverage", pins=_EMPTY_FC, hulls=_HULLS_2)
        assert len(da.list_plans()) == 1

        da.delete_plan(plan_rec.id)
        assert len(da.list_plans()) == 0

    # ---- grouping config round-trip ----

    def test_grouping_config_nested_dict_survives_round_trip(self):
        """grouping config dict stored in plan.data survives JSON round-trip intact."""
        da = _make_program_da()
        grouping = {"strategy": "bfs_adjacency", "max_buildings": 150, "buffer_distance_m": 50}
        plan_rec = da.create_plan(
            region="R", name="N", mode="coverage", pins=_EMPTY_FC, hulls=_HULLS_2, grouping=grouping
        )

        refetched = da.get_plan(plan_rec.id)
        stored_grouping = refetched.data.get("grouping")
        assert stored_grouping == grouping
        assert stored_grouping["max_buildings"] == 150

    # ---- schema_version round-trip ----

    def test_schema_version_in_data_survives_round_trip(self):
        """schema_version written to data is preserved (int, not stringified)."""
        from commcare_connect.microplans.core.data_access import SCHEMA_VERSION

        da = _make_program_da()
        plan_rec = da.create_plan(region="R", name="N", mode="coverage", pins=_EMPTY_FC, hulls=_HULLS_2)

        refetched = da.get_plan(plan_rec.id)
        assert refetched.data.get("schema_version") == SCHEMA_VERSION
        assert isinstance(refetched.data.get("schema_version"), int)
