"""MBW Auditing V5 snapshot contract: derived aggregates, never raw rows.

A real MBW opp has 100k+ visits; capturing pipelines verbatim produced a
112 MB snapshot that OOM-killed a web worker (opp 765, 2026-06-11). The
contract is therefore: the render freezes the rendered dashboard into
`concluded_*` state keys at conclude time, and the manifest captures only
state + workers — zero pipeline rows.
"""

from commcare_connect.workflow.templates.mbw_auditing_v5 import RENDER_CODE, SNAPSHOT_INPUTS, SNAPSHOT_SCHEMA


class TestV5SnapshotContract:
    def test_manifest_captures_no_pipeline_rows(self):
        assert SNAPSHOT_INPUTS["pipelines"] == []

    def test_manifest_captures_concluded_dashboard_keys(self):
        for key in ("concluded_summaries", "concluded_prev_categories", "concluded_tab2"):
            assert key in SNAPSHOT_INPUTS["state_keys"]
            assert f"state.{key}" in SNAPSHOT_SCHEMA["keys"]

    def test_schema_bumped_past_raw_pipeline_version(self):
        assert SNAPSHOT_SCHEMA["version"] >= 2
        assert not any(k.startswith("pipelines.") for k in SNAPSHOT_SCHEMA["keys"])

    def test_render_freezes_dashboard_at_conclude(self):
        # The conclude state write carries the frozen dashboard…
        assert "concluded_summaries: concludedSummaries" in RENDER_CODE
        assert "concluded_tab2: concludedTab2" in RENDER_CODE
        # …and the completed view reads it back (with the legacy
        # snapshot-pipelines path retained for schema-v1 completed runs).
        assert "savedState.concluded_summaries" in RENDER_CODE
        assert "view.state.concluded_tab2" in RENDER_CODE
