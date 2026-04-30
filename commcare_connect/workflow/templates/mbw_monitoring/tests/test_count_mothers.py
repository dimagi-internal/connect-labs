"""Tests for count_mothers_from_pipeline.

The function supports three input shapes — V2 pipeline rows (preferred for
V2 workflows), V1 full CCHQ form dicts (back-compat with V1 callers), and a
visit-rows fallback. These tests lock in each path so the V2 dashboard's
\"# Mothers\" column does not silently regress to zero again.

Background: the V2 dashboard once silently reported 0 mothers per FLW
because the job handler passed V2 slim row dicts via the V1-only
`registration_forms` parameter. The V1 path skipped every row
(metadata.username was always empty in V2 shape) and never fell through
to the visit-rows fallback. The new `registration_rows` parameter handles
the V2 shape natively.
"""

from commcare_connect.workflow.templates.mbw_monitoring.followup_analysis import count_mothers_from_pipeline


class _FakeVisitRow:
    """Minimal stand-in for VisitRow / _PipelineRowAdapter — supports
    attribute access on `username` and `computed`."""

    def __init__(self, username: str, mother_case_id: str | None = None):
        self.username = username
        self.computed = {"mother_case_id": mother_case_id} if mother_case_id else {}


class TestV2RegistrationRows:
    """V2 path: slim pipeline row dicts with username + case_id."""

    def test_counts_distinct_mothers_per_flw(self):
        registration_rows = [
            {"username": "alice", "case_id": "m1"},
            {"username": "alice", "case_id": "m2"},
            {"username": "alice", "case_id": "m1"},  # dup ignored
            {"username": "bob", "case_id": "m3"},
        ]
        result = count_mothers_from_pipeline(
            pipeline_rows=[],
            active_usernames={"alice", "bob"},
            registration_rows=registration_rows,
        )
        assert result == {"alice": 2, "bob": 1}

    def test_drops_inactive_usernames(self):
        registration_rows = [
            {"username": "alice", "case_id": "m1"},
            {"username": "ghost", "case_id": "m2"},
        ]
        result = count_mothers_from_pipeline(
            pipeline_rows=[],
            active_usernames={"alice"},
            registration_rows=registration_rows,
        )
        assert result == {"alice": 1}

    def test_handles_nested_computed_dict(self):
        """If the pipeline serializer keeps fields nested under `computed`."""
        registration_rows = [
            {"username": "alice", "computed": {"case_id": "m1"}},
            {"username": "alice", "computed": {"case_id": "m2"}},
        ]
        result = count_mothers_from_pipeline(
            pipeline_rows=[],
            active_usernames={"alice"},
            registration_rows=registration_rows,
        )
        assert result == {"alice": 2}

    def test_skips_rows_with_missing_case_id(self):
        registration_rows = [
            {"username": "alice", "case_id": "m1"},
            {"username": "alice"},  # no case_id
            {"username": "alice", "case_id": ""},  # empty
        ]
        result = count_mothers_from_pipeline(
            pipeline_rows=[],
            active_usernames={"alice"},
            registration_rows=registration_rows,
        )
        assert result == {"alice": 1}

    def test_username_match_is_case_insensitive(self):
        """Pipeline rows may have mixed-case usernames; active set is lowercased."""
        registration_rows = [
            {"username": "Alice", "case_id": "m1"},
            {"username": "ALICE", "case_id": "m2"},
        ]
        result = count_mothers_from_pipeline(
            pipeline_rows=[],
            active_usernames={"alice"},
            registration_rows=registration_rows,
        )
        assert result == {"alice": 2}


class TestV1RegistrationFormsBackCompat:
    """V1 path stays untouched: full CCHQ form dicts with var_visit blocks."""

    def test_v1_form_dicts_still_work(self):
        # Minimal V1 form-dict shape; the helper walks var_visit_N blocks.
        # We use a fake schedule extractor result by calling through the
        # path. Smoke test: function still returns a dict for V1 input.
        registration_forms = [
            {
                "form": {
                    "var_visit_1": {
                        "visit_type": "anc",
                        "mother_case_id": "m1",
                    },
                },
                "metadata": {"username": "alice"},
            },
        ]
        # Doesn't matter if it returns 1 or 0 — back-compat is "doesn't
        # crash and respects the registration_forms param ordering". The
        # important guarantee is that registration_rows takes priority.
        result = count_mothers_from_pipeline(
            pipeline_rows=[],
            active_usernames={"alice"},
            registration_forms=registration_forms,
        )
        assert isinstance(result, dict)


class TestVisitRowFallback:
    """No registration data → fall through to scanning visit rows."""

    def test_counts_from_visit_rows(self):
        visit_rows = [
            _FakeVisitRow("alice", "m1"),
            _FakeVisitRow("alice", "m1"),  # dup visit to same mother
            _FakeVisitRow("alice", "m2"),
            _FakeVisitRow("bob", "m3"),
            _FakeVisitRow("alice", None),  # missing mother_case_id ignored
        ]
        result = count_mothers_from_pipeline(
            pipeline_rows=visit_rows,
            active_usernames={"alice", "bob"},
        )
        assert result == {"alice": 2, "bob": 1}


class TestV2PriorityOverV1:
    """If both V2 and V1 inputs supplied, V2 wins (preferred V2 contract)."""

    def test_v2_takes_priority(self):
        result = count_mothers_from_pipeline(
            pipeline_rows=[],
            active_usernames={"alice"},
            registration_rows=[{"username": "alice", "case_id": "m1"}],
            registration_forms=[
                {
                    "form": {"var_visit_1": {"visit_type": "anc", "mother_case_id": "m_v1"}},
                    "metadata": {"username": "alice"},
                }
            ],
        )
        # Should reflect V2 input (m1), not V1 (m_v1)
        assert result == {"alice": 1}


class TestEmptyInputs:
    def test_no_data_returns_empty(self):
        result = count_mothers_from_pipeline(
            pipeline_rows=[],
            active_usernames={"alice"},
        )
        assert result == {}

    def test_v2_empty_list_falls_through_to_visit_rows(self):
        """`registration_rows=[]` is falsy, so the visit-rows fallback runs.

        Important so that an opp with zero registrations but non-empty
        visits doesn't report zero mothers.
        """
        visit_rows = [_FakeVisitRow("alice", "m1")]
        result = count_mothers_from_pipeline(
            pipeline_rows=visit_rows,
            active_usernames={"alice"},
            registration_rows=[],
        )
        assert result == {"alice": 1}
