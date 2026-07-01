"""End-to-end Postgres execution tests for the haversine_meters function.

Bounds the correctness of the Python mirror in
connect_labs/labs/analysis/backends/sql/tests/reference_aggregation.haversine_meters
against the actual SQL function defined in migration 0012.

The test runs once per scenario rather than per-data-point; its job is
"the SQL function exists, executes, and agrees with the mirror," not
exhaustive geographic coverage.
"""

import pytest
from django.db import connection

from connect_labs.labs.analysis.backends.sql.tests.reference_aggregation import haversine_meters as py_haversine


@pytest.mark.django_db
class TestHaversineSqlFunction:
    """Run haversine_meters() through Postgres and verify the result."""

    def _run(self, lat1, lon1, lat2, lon2) -> float | None:
        with connection.cursor() as cur:
            cur.execute(
                "SELECT haversine_meters(%s::float, %s::float, %s::float, %s::float)",
                [lat1, lon1, lat2, lon2],
            )
            return cur.fetchone()[0]

    def test_zero_distance(self, db):
        """Same point → 0 meters. Sanity check the function is callable."""
        assert self._run(40.0, -75.0, 40.0, -75.0) == pytest.approx(0.0)

    def test_known_distance_kampala_to_nairobi(self, db):
        """Kampala (~0.347, 32.582) to Nairobi (~-1.286, 36.817) — known
        spherical-haversine distance ~504.7 km. Tolerance ±1 km absorbs
        the choice of Earth radius (varies between 6357–6378 km depending
        on which spheroid) and small float differences.
        """
        result = self._run(0.3476, 32.5825, -1.2864, 36.8172)
        assert result == pytest.approx(504_700, abs=1_000)

    def test_short_distance_within_a_village(self, db):
        """~0.001 degrees apart at the equator — about 111 m. Useful as a
        non-trivial small-distance test (relevant for revisit-distance flagging
        which uses a 5km threshold).
        """
        result = self._run(-1.0000, 35.0000, -1.0010, 35.0000)
        # ~111 m; allow ±5m for radius/float
        assert result == pytest.approx(111, abs=5)

    def test_returns_null_on_any_null_input(self, db):
        """NULL on any coordinate must return NULL — required so window
        functions over sparse GPS rows propagate "no comparison possible"
        rather than emitting a phantom zero distance.
        """
        assert self._run(None, -75.0, 40.0, -75.0) is None
        assert self._run(40.0, None, 40.0, -75.0) is None
        assert self._run(40.0, -75.0, None, -75.0) is None
        assert self._run(40.0, -75.0, 40.0, None) is None

    def test_sql_agrees_with_python_mirror(self, db):
        """The Python mirror in runners.haversine_meters must agree with the
        SQL function on every test case. If either drifts in the future,
        this test fails and forces both to be fixed in lockstep.
        """
        cases = [
            # Same point
            (40.0, -75.0, 40.0, -75.0),
            # Known Kampala→Nairobi
            (0.3476, 32.5825, -1.2864, 36.8172),
            # Short
            (-1.0000, 35.0000, -1.0010, 35.0000),
            # Negative latitudes (southern hemisphere)
            (-1.2345, 35.6789, -1.2350, 35.6800),
            # Crossing the equator
            (1.0, 35.0, -1.0, 35.0),
            # Crossing the meridian (180° antimeridian is rare in MBW data;
            # test crossing 0° instead, which is what East African data does)
            (0.5, -1.0, 0.5, 1.0),
        ]
        for lat1, lon1, lat2, lon2 in cases:
            sql_result = self._run(lat1, lon1, lat2, lon2)
            py_result = py_haversine(lat1, lon1, lat2, lon2)
            assert sql_result == pytest.approx(py_result, abs=0.001), (
                f"SQL/Python drift at ({lat1},{lon1})→({lat2},{lon2}): " f"sql={sql_result} py={py_result}"
            )


class TestHaversinePythonMirror:
    """Pure-Python tests of the mirror — fast, no DB."""

    def test_zero_distance(self):
        assert py_haversine(40.0, -75.0, 40.0, -75.0) == pytest.approx(0.0)

    def test_returns_none_on_any_null(self):
        assert py_haversine(None, -75.0, 40.0, -75.0) is None
        assert py_haversine(40.0, None, 40.0, -75.0) is None
        assert py_haversine(40.0, -75.0, None, -75.0) is None
        assert py_haversine(40.0, -75.0, 40.0, None) is None

    def test_kampala_nairobi_known_distance(self):
        # ~504.7 km (spherical haversine with R=6371000)
        result = py_haversine(0.3476, 32.5825, -1.2864, 36.8172)
        assert result == pytest.approx(504_700, abs=1_000)

    def test_symmetric(self):
        """haversine(A, B) == haversine(B, A) for any pair."""
        d1 = py_haversine(0.3476, 32.5825, -1.2864, 36.8172)
        d2 = py_haversine(-1.2864, 36.8172, 0.3476, 32.5825)
        assert d1 == pytest.approx(d2)
