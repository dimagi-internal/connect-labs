from connect_labs.semantic.maturity import settle_after_days, settle_problems
from connect_labs.semantic.runtime import load_registry


def _props(*properties, constants=None, aggregates=None):
    return {
        "constants": constants or {},
        "aggregates": list(aggregates or [{"name": "first_visit", "sql": "MIN(visit_date)"}]),
        "properties": list(properties),
    }


DAYS = {
    "name": "days_since_first_visit",
    "type": "int",
    "sql": "FLOOR(EXTRACT(EPOCH FROM ((:as_of)::timestamp - first_visit::timestamp)) / 86400)::int",
}


def _gate(name, const):
    return {"name": name, "type": "bool", "sql": f"days_since_first_visit >= :{const}"}


def _measure(sql):
    return {"measures": [{"name": "m", "sql": "COUNT(*)", "filters": [{"sql": sql}]}]}


class TestTheKmcRegistry:
    def test_settles_42_days_after_the_anchor(self):
        # eligible_42d (growth, visits per baby) is the longest gate an indicator
        # reads. eligible_90d exists but nothing reads it yet.
        props, inds = load_registry("kmc")
        assert settle_after_days(props, inds) == 42

    def test_has_no_date_dependence_the_window_cannot_read(self):
        props, inds = load_registry("kmc")
        assert settle_problems(props, inds) == []


class TestTheWindowFollowsTheDefinitions:
    CONSTANTS = {"SHORT": 28, "LONG": 90}

    def test_an_unread_gate_moves_nothing(self):
        props = _props(
            DAYS, _gate("eligible_short", "SHORT"), _gate("eligible_long", "LONG"), constants=self.CONSTANTS
        )
        assert settle_after_days(props, _measure("{CUBE}.eligible_short")) == 28

    def test_reading_a_longer_gate_lengthens_the_window(self):
        props = _props(
            DAYS, _gate("eligible_short", "SHORT"), _gate("eligible_long", "LONG"), constants=self.CONSTANTS
        )
        inds = {
            "measures": [
                {"name": "a", "sql": "COUNT(*)", "filters": [{"sql": "{CUBE}.eligible_short"}]},
                {"name": "b", "sql": "COUNT(*)", "filters": [{"sql": "{CUBE}.eligible_long"}]},
            ]
        }
        assert settle_after_days(props, inds) == 90

    def test_a_gate_reached_through_another_property_counts(self):
        props = _props(
            DAYS,
            _gate("eligible_long", "LONG"),
            {"name": "qualifying", "type": "bool", "sql": "eligible_long AND first_visit IS NOT NULL"},
            constants=self.CONSTANTS,
        )
        assert settle_after_days(props, _measure("{CUBE}.qualifying")) == 90

    def test_a_registry_that_never_reads_the_date_settles_at_once(self):
        props = _props({"name": "seen", "type": "bool", "sql": "first_visit IS NOT NULL"})
        assert settle_after_days(props, _measure("{CUBE}.seen")) == 0


class TestWhatCannotSettleIsReported:
    def test_a_date_dependent_value_read_directly_never_settles(self):
        props = _props(DAYS)
        inds = {"measures": [{"name": "mean_age", "sql": "AVG({CUBE}.days_since_first_visit)"}]}
        assert settle_problems(props, inds) == [
            "days_since_first_visit: an indicator reads this date-dependent value directly, "
            "so its figures never settle"
        ]

    def test_a_gate_on_a_missing_constant_is_reported(self):
        props = _props(DAYS, _gate("eligible", "NOPE"))
        assert settle_problems(props, _measure("{CUBE}.eligible")) == [
            "eligible: gate constant :NOPE is not a whole number of days (None)"
        ]


class TestWhatASnapshotRecords:
    """`meta.settles` is what the benchmark publisher ends each trend line with."""

    def test_the_kmc_report_records_its_anchor_and_window(self):
        from connect_labs.workflow.snapshot_builders import settles_meta
        from connect_labs.workflow.templates.kmc_programme_metrics import SNAPSHOT_INPUTS

        props, inds = load_registry("kmc")
        assert settles_meta(SNAPSHOT_INPUTS, props, inds) == {"after_days": 42, "anchor": "first_visit_date"}

    def test_a_spec_with_no_anchor_records_none(self):
        from connect_labs.workflow.snapshot_builders import settles_meta

        props, inds = load_registry("kmc")
        assert settles_meta({}, props, inds) == {"after_days": 42, "anchor": None}
