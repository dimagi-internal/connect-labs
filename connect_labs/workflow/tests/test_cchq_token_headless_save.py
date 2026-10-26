"""The owner's CommCare HQ token reaches every step of a headless periodic save.

A scheduled run mints the owner's CommCare HQ token (run_scheduled_workflow) and hands
it to run_default_for_definition. Templates with their own run_default use it; the
framework's generic periodic save used to drop it, so the cache warm built its
pipelines with no HQ token and every cchq_forms / cchq_cases slot failed with
CCHQHeadlessError outside a browser. A generic indicator report whose registry looks
up CommCare HQ data (registrations, visit records) could then only ever be saved by
someone signed in to HQ in a browser. These tests pin each hand-off of the token.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

from connect_labs.workflow import history_rebuild, templates, visit_cache

TOKEN = "hq-token"


class _Definition:
    def __init__(self):
        self.id = 7
        self.opportunity_ids = [765]
        self.pipeline_sources = [{"pipeline_id": 11, "alias": "visit_records"}]
        self.data = {}
        self.program_id = 60
        self.opportunity_id = None


class _DAO:
    access_token = "connect-token"

    def __init__(self, definition):
        self._definition = definition

    def get_definition(self, definition_id):
        return self._definition


class TestRunDefault:
    def test_the_generic_periodic_save_receives_the_token(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(templates, "_definition_template_key", lambda d: None)
        monkeypatch.setattr(templates, "definition_saves_periods", lambda d: True)
        monkeypatch.setattr(templates, "scheduled_period", lambda d: "weekly")
        monkeypatch.setattr(history_rebuild, "save_latest_period", lambda d, **kw: (seen.update(kw), {"ok": 1})[1])

        templates.run_default_for_definition(
            _Definition(), access_token="connect-token", request=None, cchq_access_token=TOKEN, program_id=60
        )

        assert seen["cchq_access_token"] == TOKEN

    def test_no_token_still_saves_exactly_as_before(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(templates, "_definition_template_key", lambda d: None)
        monkeypatch.setattr(templates, "definition_saves_periods", lambda d: True)
        monkeypatch.setattr(templates, "scheduled_period", lambda d: "weekly")
        monkeypatch.setattr(history_rebuild, "save_latest_period", lambda d, **kw: (seen.update(kw), {"ok": 1})[1])

        templates.run_default_for_definition(_Definition(), access_token="connect-token", program_id=60)

        assert seen["cchq_access_token"] is None


class TestSaveLatestPeriod:
    def test_the_rebuild_receives_the_token(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(
            history_rebuild, "rebuild_history", lambda dao, did, **kw: (seen.update(kw), {"created": 1, "runs": []})[1]
        )

        report = history_rebuild.save_latest_period(
            _Definition(),
            access_token="connect-token",
            program_id=60,
            today=date(2026, 10, 20),
            data_access=MagicMock(),
            cchq_access_token=TOKEN,
        )

        assert report["status"] == "saved"
        assert seen["cchq_access_token"] == TOKEN


class TestEnsureCache:
    def test_a_sync_hands_the_token_to_the_cache_warm(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(history_rebuild, "extend_if_held", lambda *a, **kw: None)
        monkeypatch.setattr(
            history_rebuild,
            "ensure_visit_cache",
            lambda *a, **kw: (seen.update(kw), {"failed": [], "hold_until": "x"})[1],
        )

        history_rebuild._ensure_cache(
            MagicMock(),
            7,
            opportunity_id=None,
            program_id=60,
            progress=None,
            hold_minutes=90,
            cchq_access_token=TOKEN,
        )

        assert seen["cchq_access_token"] == TOKEN

    def test_preview_as_of_hands_the_token_to_the_cache_warm(self, monkeypatch):
        seen = {}

        def stop_after_cache(*a, **kw):
            seen.update(kw)
            raise RuntimeError("stop here: only the cache hand-off is under test")

        monkeypatch.setattr(history_rebuild, "eligibility", lambda d: (True, None))
        monkeypatch.setattr(history_rebuild, "_ensure_cache", stop_after_cache)

        with pytest.raises(RuntimeError, match="stop here"):
            history_rebuild.preview_as_of(
                _DAO(_Definition()), 7, as_of=date(2026, 10, 19), program_id=60, cchq_access_token=TOKEN
            )

        assert seen["cchq_access_token"] == TOKEN


class TestVisitCache:
    def test_the_default_slot_is_built_with_the_token(self, monkeypatch):
        seen = {}

        def fake_default_slot(access_token, owner_scope, sources=None, *, cchq_access_token=None):
            seen["cchq_access_token"] = cchq_access_token
            raise RuntimeError("stop here: only the hand-off is under test")

        monkeypatch.setattr(visit_cache, "_default_slot", fake_default_slot)

        with pytest.raises(RuntimeError, match="stop here"):
            visit_cache.ensure_visit_cache(_DAO(_Definition()), 7, program_id=60, cchq_access_token=TOKEN)

        assert seen["cchq_access_token"] == TOKEN

    def test_a_caller_supplied_slot_factory_keeps_its_three_argument_shape(self):
        calls = []

        def factory(access_token, owner_scope, sources=None):
            calls.append((access_token, owner_scope))
            raise RuntimeError("stop here")

        with pytest.raises(RuntimeError, match="stop here"):
            visit_cache.ensure_visit_cache(
                _DAO(_Definition()), 7, program_id=60, slot_factory=factory, cchq_access_token=TOKEN
            )

        assert calls == [("connect-token", {"program_id": 60})]

    def test_the_analysis_and_every_pipeline_run_carry_the_token(self, monkeypatch):
        import connect_labs.labs.analysis.pipeline as pipeline_module
        import connect_labs.workflow.data_access as data_access_module

        built = {}

        class FakeAnalysis:
            def __init__(self, request=None, access_token=None, cchq_access_token=None):
                built["analysis"] = cchq_access_token

        class FakePDA:
            def __init__(self, access_token=None, cchq_access_token=None, **scope):
                self.cchq_access_token = cchq_access_token

            def use_sources(self, sources):
                return self

            def execute_pipeline(self, pipeline_id, opp):
                built["pipeline_run"] = self.cchq_access_token
                return {"rows": [], "metadata": {}}

            def close(self):
                pass

        monkeypatch.setattr(pipeline_module, "AnalysisPipeline", FakeAnalysis)
        monkeypatch.setattr(data_access_module, "PipelineDataAccess", FakePDA)

        _manager, _fetch_raw, run_pipeline = visit_cache._default_slot(
            "connect-token", {"program_id": 60}, None, cchq_access_token=TOKEN
        )
        run_pipeline(765, 11)

        assert built == {"analysis": TOKEN, "pipeline_run": TOKEN}


class TestPipelineExecution:
    def test_a_headless_pipeline_run_builds_its_engine_with_the_token(self, monkeypatch):
        import connect_labs.labs.analysis.pipeline as pipeline_module
        from connect_labs.workflow.data_access import PipelineDataAccess

        built = {}

        class FakeAnalysis:
            def __init__(self, request=None, access_token=None, cchq_access_token=None):
                built["cchq_access_token"] = cchq_access_token

            def stream_analysis_ignore_events(self, config, opportunity_id):
                raise RuntimeError("stop here: only the engine construction is under test")

        monkeypatch.setattr(pipeline_module, "AnalysisPipeline", FakeAnalysis)
        pda = PipelineDataAccess.__new__(PipelineDataAccess)
        pda.request = None
        pda.access_token = "connect-token"
        pda.cchq_access_token = TOKEN
        definition = MagicMock()
        definition.schema = {"fields": []}
        monkeypatch.setattr(PipelineDataAccess, "get_definition", lambda self, i: definition)
        monkeypatch.setattr(PipelineDataAccess, "_schema_to_config", lambda self, s, i: object())

        result = pda.execute_pipeline(11, 765)

        assert built["cchq_access_token"] == TOKEN
        assert "stop here" in result["metadata"]["error"]


def _hq_pipeline_config(pipeline_id):
    from connect_labs.labs.analysis.config import AnalysisPipelineConfig, DataSourceConfig, FieldComputation

    return AnalysisPipelineConfig(
        grouping_key="case_id",
        fields=[FieldComputation(name="mother_case_id", path="case.properties.mother_case_id", aggregation="first")],
        data_source=DataSourceConfig(type="cchq_cases", case_type="visit"),
        pipeline_id=pipeline_id,
    )


def _patch_pipeline_reads(monkeypatch, config_for):
    import connect_labs.workflow.data_access as data_access_module

    class FakePDA:
        def __init__(self, access_token=None, cchq_access_token=None, **scope):
            pass

        def use_sources(self, sources):
            return self

        def get_definition(self, pipeline_id):
            definition = MagicMock()
            definition.schema = {"fields": []}
            return definition

        def _schema_to_config(self, schema, pipeline_id):
            return config_for(pipeline_id)

        def close(self):
            pass

    monkeypatch.setattr(data_access_module, "PipelineDataAccess", FakePDA)


class TestEachPipelineUsesItsOwnRawSlot:
    """ACE review LABS-0003: a manager built without a config counts and holds the shared
    user_visits slot, so a CommCare HQ slot was checked and held through the wrong rows."""

    def test_a_commcare_hq_pipeline_is_counted_in_its_own_slot(self, monkeypatch):
        from connect_labs.labs.analysis.config import USER_VISITS_RAW_SLOT

        _patch_pipeline_reads(monkeypatch, _hq_pipeline_config)
        manager, _fetch_raw, _run = visit_cache._default_slot("connect-token", {"program_id": 60}, None)

        assert manager(765, 11).raw_slot_id == 11
        assert manager(765, 11).raw_slot_id != USER_VISITS_RAW_SLOT

    def test_a_pipeline_whose_definition_cannot_be_read_keeps_the_visits_slot(self, monkeypatch):
        from connect_labs.labs.analysis.config import USER_VISITS_RAW_SLOT

        def unreadable(pipeline_id):
            raise RuntimeError("upstream 404")

        _patch_pipeline_reads(monkeypatch, unreadable)
        manager, _fetch_raw, _run = visit_cache._default_slot("connect-token", {"program_id": 60}, None)

        assert manager(765, 11).raw_slot_id == USER_VISITS_RAW_SLOT
        assert manager(765, 11).slot_resolved is False

    @pytest.mark.django_db
    def test_an_unresolvable_slot_is_never_called_held(self, monkeypatch):
        """ACE review LABS-0003b: the visits-slot fallback must not let extend_if_held
        skip the sync, or an HQ pipeline whose definition read failed is called held."""
        from django.utils import timezone

        from connect_labs.labs.analysis.backends.sql.models import RawVisitCache
        from connect_labs.labs.analysis.config import USER_VISITS_RAW_SLOT

        def unreadable(pipeline_id):
            raise RuntimeError("upstream 404")

        _patch_pipeline_reads(monkeypatch, unreadable)
        future = timezone.now() + timezone.timedelta(hours=1)
        for i in range(5):
            RawVisitCache.objects.create(
                opportunity_id=765,
                pipeline_id=USER_VISITS_RAW_SLOT,
                visit_count=5,
                expires_at=future,
                visit_id=f"v-{i}",
                username="flw",
                visit_date="2026-10-01",
                status="approved",
            )

        assert visit_cache.extend_if_held(_DAO(_Definition()), 7, program_id=60) is None

    @pytest.mark.django_db
    @pytest.mark.parametrize("hq_rows, expect_held", [(0, False), (3, True)])
    def test_an_empty_hq_slot_is_not_called_held_so_the_sync_with_the_token_runs(
        self, monkeypatch, hq_rows, expect_held
    ):
        from django.utils import timezone

        from connect_labs.labs.analysis.backends.sql.models import RawVisitCache
        from connect_labs.labs.analysis.config import USER_VISITS_RAW_SLOT

        _patch_pipeline_reads(monkeypatch, _hq_pipeline_config)
        future = timezone.now() + timezone.timedelta(hours=1)

        def seed(slot, n):
            for i in range(n):
                RawVisitCache.objects.create(
                    opportunity_id=765,
                    pipeline_id=slot,
                    visit_count=n,
                    expires_at=future,
                    visit_id=f"{slot}-{i}",
                    username="flw",
                    visit_date="2026-10-01",
                    status="approved",
                )

        # The Connect visits were fetched (no HQ token needed) ...
        seed(USER_VISITS_RAW_SLOT, 5)
        # ... and the CommCare HQ slot is either still empty (an earlier call had no token) or filled.
        seed(11, hq_rows)

        report = visit_cache.extend_if_held(_DAO(_Definition()), 7, program_id=60)

        assert (report is not None) is expect_held


class TestASlotFilledByThePipelineRunIsHeld:
    """A CommCare HQ slot is filled by the pipeline run, not by the visits fetch, so the
    hold taken before the run found nothing; it is held again once the run filled it."""

    def _factory(self, raw, calls):
        def factory(access_token, owner_scope, sources=None):
            class Mgr:
                def __init__(self, opp, pid):
                    self.key = (opp, pid)

                def get_raw_visit_count(self):
                    return raw.get(self.key, 0)

                def whole_raw_visit_count(self):
                    return raw.get(self.key, 0)

                def extend_raw_cache_ttl(self, minutes):
                    calls.append(("hold_raw", self.key))

                def hold_computed_caches(self, minutes):
                    calls.append(("hold_computed", self.key))

            def fetch(opp, pid):
                return 0  # the visits fetch does not fill another source's slot

            def run(opp, pid):
                raw[(opp, pid)] = 40  # the pipeline run fills the HQ slot
                calls.append(("run", (opp, pid)))
                return {"rows": [1], "metadata": {}}

            return Mgr, fetch, run

        return factory

    def test_a_cold_slot_is_held_after_the_run(self):
        raw, calls = {}, []
        visit_cache.ensure_visit_cache(
            _DAO(_Definition()), 7, program_id=60, slot_factory=self._factory(raw, calls), cchq_access_token=TOKEN
        )

        assert [c[0] for c in calls] == ["hold_raw", "run", "hold_raw", "hold_computed"]

    def test_a_warm_slot_is_held_once(self):
        raw, calls = {(765, 11): 40}, []
        visit_cache.ensure_visit_cache(
            _DAO(_Definition()), 7, program_id=60, slot_factory=self._factory(raw, calls), cchq_access_token=TOKEN
        )

        assert [c[0] for c in calls] == ["hold_raw", "run", "hold_computed"]
