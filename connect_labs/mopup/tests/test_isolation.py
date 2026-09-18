"""Tests for Step 3's isolation filter (core/isolation.py) — pure
computation, no network/DB."""

from __future__ import annotations

from connect_labs.mopup.core import isolation


def _square_row(wa_id, lon, lat, size=0.0001, **extra):
    """A tiny square polygon centered at (lon, lat) — small enough that its
    centroid is effectively (lon, lat) itself, so tests can reason directly
    in coordinates without worrying about polygon-size skew."""
    row = {
        "wa_id": wa_id,
        "boundary": {
            "type": "Polygon",
            "coordinates": [
                [
                    [lon - size, lat - size],
                    [lon + size, lat - size],
                    [lon + size, lat + size],
                    [lon - size, lat + size],
                    [lon - size, lat - size],
                ]
            ],
        },
    }
    row.update(extra)
    return row


class TestIsolatedWorkAreaIds:
    def test_two_far_apart_are_both_isolated(self):
        # ~2.2km apart (0.02 degrees at the equator) -- well beyond a 1000m
        # threshold, so neither has any neighbor.
        rows = [_square_row("a", 0.0, 0.0), _square_row("b", 0.02, 0.02)]
        assert isolation.isolated_work_area_ids(rows, 1000) == {"a", "b"}

    def test_two_close_together_are_neither_isolated(self):
        # ~110-150m apart (0.001 degrees) -- within a 1000m threshold.
        rows = [_square_row("a", 0.0, 0.0), _square_row("b", 0.001, 0.001)]
        assert isolation.isolated_work_area_ids(rows, 1000) == set()

    def test_only_the_far_one_of_three_is_isolated(self):
        # a/b are close (each other's neighbor); c is ~2.2km from both.
        rows = [
            _square_row("a", 0.0, 0.0),
            _square_row("b", 0.001, 0.001),
            _square_row("c", 0.02, 0.02),
        ]
        assert isolation.isolated_work_area_ids(rows, 1000) == {"c"}

    def test_empty_input_returns_empty(self):
        assert isolation.isolated_work_area_ids([], 1000) == set()

    def test_single_row_is_isolated_by_definition(self):
        assert isolation.isolated_work_area_ids([_square_row("a", 0.0, 0.0)], 1000) == {"a"}

    def test_rows_without_boundary_are_skipped_entirely(self):
        # A boundary-less row can neither be isolated itself nor corroborate
        # a neighbor -- confirmed here by giving "b" no boundary right next
        # to "a": "a" still comes back isolated since "b" was never
        # positioned to begin with.
        rows = [_square_row("a", 0.0, 0.0), {"wa_id": "b", "boundary": None}]
        assert isolation.isolated_work_area_ids(rows, 1000) == {"a"}

    def test_building_count_is_irrelevant(self):
        # Wildly different building counts on both rows -- isolation is
        # purely centroid distance, never gated on building count (unlike
        # the unrelated dist_to_multi_m/roof_area_m2 single-building-noise
        # filter microplans' own review page already has).
        rows = [
            _square_row("a", 0.0, 0.0, building_count=1),
            _square_row("b", 0.001, 0.001, building_count=500),
        ]
        assert isolation.isolated_work_area_ids(rows, 1000) == set()

    def test_works_uniformly_across_candidate_and_gap_fill_row_shapes(self):
        # A real evaluate_run-style candidate row and a
        # gap_feature_to_candidate_row-style gap row -- both only need
        # wa_id/boundary for this function, so they mix freely.
        candidate_row = {
            "wa_id": "wa-real-1",
            "wa_name": "ABC123",
            "source": "existing_wa",
            "boundary": _square_row("_", 0.0, 0.0)["boundary"],
        }
        gap_row = {
            "wa_id": "mopup-kano-rano-sabon-gari-gap-C0",
            "wa_name": "",
            "source": "planning_gap",
            "boundary": _square_row("_", 0.02, 0.02)["boundary"],
        }
        result = isolation.isolated_work_area_ids([candidate_row, gap_row], 1000)
        assert result == {"wa-real-1", "mopup-kano-rano-sabon-gari-gap-C0"}
