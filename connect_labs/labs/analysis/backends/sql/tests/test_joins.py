"""End-to-end Postgres execution test for cross-pipeline JOINs.

Seeds a small visits set into `labs_raw_visit_cache` and a small
registrations set into `labs_computed_visit_cache` (under a synthetic
config_hash), runs `build_flw_aggregation_query` against a config that
JOINs the two, and asserts the joined fields participate correctly in
aggregations through the `joined.<alias>.<field>` JSONB path.

The test exercises:
- The wrapped-source pattern (subquery aliased as `labs_raw_visit_cache`).
- DISTINCT-ON pre-aggregation when multiple registration rows share the
  same join key.
- COUNT/MAX/MIN on a joined field.
- Two-pass `pre_aggregate_by` over a joined field (via mode_share).
"""

import pytest
from django.utils import timezone

from connect_labs.labs.analysis.backends.sql.models import ComputedVisitCache, RawVisitCache
from connect_labs.labs.analysis.backends.sql.query_builder import build_flw_aggregation_query
from connect_labs.labs.analysis.config import AnalysisPipelineConfig, FieldComputation, JoinConfig


@pytest.mark.django_db
class TestCrossPipelineJoins:
    """Verify that JOIN-aware build_flw_aggregation_query produces correct results."""

    def _seed_visits(self, opp_id: int, rows: list[tuple[str, str]]) -> None:
        """Insert visits with `form.parents.parent.case.@case_id = <mid>`."""
        future = timezone.now() + timezone.timedelta(days=1)
        for i, (username, mother_id) in enumerate(rows):
            RawVisitCache.objects.create(
                opportunity_id=opp_id,
                visit_count=len(rows),
                expires_at=future,
                visit_id=str(30000 + i),
                username=username,
                form_json={"form": {"parents": {"parent": {"case": {"@case_id": mother_id}}}}},
                visit_date="2024-01-15",
                status="approved",
            )

    def _seed_registrations_cache(
        self,
        opp_id: int,
        config_hash: str,
        rows: list[tuple[str, dict]],
    ) -> None:
        """Insert pre-extracted registration rows into computed_visit_cache.

        Each row is (mother_case_id, computed_fields_extra). `computed_fields`
        always includes `mother_case_id` since that's what the JOIN matches on.
        """
        future = timezone.now() + timezone.timedelta(days=1)
        for i, (mid, extra) in enumerate(rows):
            ComputedVisitCache.objects.create(
                opportunity_id=opp_id,
                config_hash=config_hash,
                visit_count=len(rows),
                expires_at=future,
                visit_id=f"reg-{i}",
                username="",
                computed_fields={"mother_case_id": mid, **extra},
            )

    def _execute(self, sql: str) -> list[dict]:
        from django.db import connection

        with connection.cursor() as cur:
            cur.execute(sql)
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def test_joined_field_count(self, db):
        """COUNT over a joined field reads from the joined registration row."""
        opp_id = 9701
        # 3 visits across 2 mothers for FLW 'a'; 1 visit for FLW 'b'.
        self._seed_visits(
            opp_id,
            [("a", "M1"), ("a", "M1"), ("a", "M2"), ("b", "M3")],
        )
        # Registration cache: M1 has phone "111", M2 has phone "222",
        # M3 has phone NULL (missing field). M4 is registered but never visited.
        reg_hash = "reghash01"
        self._seed_registrations_cache(
            opp_id,
            reg_hash,
            [
                ("M1", {"phone_number": "111"}),
                ("M2", {"phone_number": "222"}),
                ("M3", {"phone_number": None}),
                ("M4", {"phone_number": "444"}),
            ],
        )

        config = AnalysisPipelineConfig(
            grouping_key="username",
            fields=[
                FieldComputation(
                    name="phone_count",
                    path="joined.registrations.phone_number",
                    aggregation="count",
                ),
            ],
            joins=[
                JoinConfig(
                    from_alias="registrations",
                    local_key="form.parents.parent.case.@case_id",
                    remote_key_field="mother_case_id",
                    fields=[{"name": "phone_number", "from": "phone_number"}],
                    resolved_config_hash=reg_hash,
                )
            ],
        )

        sql = build_flw_aggregation_query(config, opp_id)
        results = {row["username"]: row for row in self._execute(sql)}

        # 'a': 2 visits to M1 (phone='111') + 1 visit to M2 (phone='222') = 3 with phone
        assert results["a"]["phone_count"] == 3
        # 'b': 1 visit to M3 (phone=NULL) → COUNT excludes nulls
        assert results["b"]["phone_count"] == 0

    def test_distinct_on_picks_one_when_multiple_registrations_match(self, db):
        """When multiple registrations share a mother_case_id, only one row joins."""
        opp_id = 9702
        self._seed_visits(opp_id, [("a", "M1"), ("a", "M1")])
        # Two registrations for the same mother; DISTINCT ON should keep one,
        # NOT multiply visit rows.
        reg_hash = "reghash02"
        self._seed_registrations_cache(
            opp_id,
            reg_hash,
            [
                ("M1", {"phone_number": "111"}),
                ("M1", {"phone_number": "112"}),
            ],
        )

        config = AnalysisPipelineConfig(
            grouping_key="username",
            fields=[
                FieldComputation(
                    name="visit_count_seen",
                    path="form.parents.parent.case.@case_id",
                    aggregation="count",
                ),
                FieldComputation(
                    name="phone_count",
                    path="joined.registrations.phone_number",
                    aggregation="count",
                ),
            ],
            joins=[
                JoinConfig(
                    from_alias="registrations",
                    local_key="form.parents.parent.case.@case_id",
                    remote_key_field="mother_case_id",
                    fields=[{"name": "phone_number", "from": "phone_number"}],
                    resolved_config_hash=reg_hash,
                )
            ],
        )

        sql = build_flw_aggregation_query(config, opp_id)
        results = {row["username"]: row for row in self._execute(sql)}

        # Visit count must stay 2 (the JOIN should not blow up rows).
        assert results["a"]["visit_count_seen"] == 2
        # Phone count: 2 visits each get the picked-one phone → 2.
        assert results["a"]["phone_count"] == 2

    def test_attribute_to_last_username_routes_shared_mothers(self, db):
        """Mothers visited by multiple FLWs are attributed to the LAST-visit FLW.

        Fixture: mother M1 visited by FLW 'a' on day 1, then FLW 'b' on day 2.
        Phone "111" duplicates with M2 (only visited by 'a').
        With pre_aggregate_attribute_to=last_username:
          - 'a' owns {M2} only — phone "111" alone in 'a's pool, no dup.
          - 'b' owns {M1} only — phone "111" alone in 'b's pool, no dup.
        Without (default): both 'a' and 'b' would see M1's phone "111", and
        'a' would also see M2's phone "111", inflating dup signals.
        """
        from datetime import date

        from connect_labs.labs.analysis.backends.sql.models import RawVisitCache

        opp_id = 9704
        # Hand-build to set explicit visit_dates per row (the helper only sets one date).
        future = timezone.now() + timezone.timedelta(days=1)
        for i, (username, mid, vdate) in enumerate(
            [
                ("a", "M1", date(2024, 1, 1)),
                ("b", "M1", date(2024, 1, 2)),  # last visit to M1 is by 'b'
                ("a", "M2", date(2024, 1, 3)),  # M2 only visited by 'a'
            ]
        ):
            RawVisitCache.objects.create(
                opportunity_id=opp_id,
                visit_count=3,
                expires_at=future,
                visit_id=f"{40000 + i}",
                username=username,
                form_json={"form": {"parents": {"parent": {"case": {"@case_id": mid}}}}},
                visit_date=vdate,
                status="approved",
            )
        reg_hash = "reghash04"
        # Both mothers have the SAME phone — would dup under default attribution
        # but NOT under last_username (each FLW owns one mother, no dup pool).
        self._seed_registrations_cache(
            opp_id,
            reg_hash,
            [
                ("M1", {"phone_number": "111"}),
                ("M2", {"phone_number": "111"}),
            ],
        )

        config = AnalysisPipelineConfig(
            grouping_key="username",
            fields=[
                FieldComputation(
                    name="phone_dup_share",
                    path="joined.registrations.phone_number",
                    aggregation="dup_share",
                    pre_aggregate_by="form.parents.parent.case.@case_id",
                    pre_aggregation="first",
                    pre_aggregate_attribute_to="last_username",
                ),
            ],
            joins=[
                JoinConfig(
                    from_alias="registrations",
                    local_key="form.parents.parent.case.@case_id",
                    remote_key_field="mother_case_id",
                    fields=[{"name": "phone_number", "from": "phone_number"}],
                    resolved_config_hash=reg_hash,
                )
            ],
        )

        sql = build_flw_aggregation_query(config, opp_id)
        results = {row["username"]: row for row in self._execute(sql)}

        # 'a' owns only M2 (M1 was last visited by 'b'). One phone in pool, no dup.
        assert results["a"]["phone_dup_share"] == pytest.approx(0.0, abs=0.01)
        # 'b' owns only M1. One phone in pool, no dup.
        assert results["b"]["phone_dup_share"] == pytest.approx(0.0, abs=0.01)

    def test_two_pass_dup_share_over_joined_field(self, db):
        """`pre_aggregate_by` mother + `dup_share` over a joined field works."""
        opp_id = 9703
        # FLW 'a' has 4 mothers; M1 and M2 share phone "111" (dup);
        # M3 has unique phone; M4 has unique phone.
        self._seed_visits(
            opp_id,
            [
                ("a", "M1"),
                ("a", "M2"),
                ("a", "M3"),
                ("a", "M4"),
            ],
        )
        reg_hash = "reghash03"
        self._seed_registrations_cache(
            opp_id,
            reg_hash,
            [
                ("M1", {"phone_number": "111"}),
                ("M2", {"phone_number": "111"}),
                ("M3", {"phone_number": "222"}),
                ("M4", {"phone_number": "333"}),
            ],
        )

        config = AnalysisPipelineConfig(
            grouping_key="username",
            fields=[
                FieldComputation(
                    name="phone_dup_share",
                    path="joined.registrations.phone_number",
                    aggregation="dup_share",
                    pre_aggregate_by="form.parents.parent.case.@case_id",
                    pre_aggregation="first",
                ),
            ],
            joins=[
                JoinConfig(
                    from_alias="registrations",
                    local_key="form.parents.parent.case.@case_id",
                    remote_key_field="mother_case_id",
                    fields=[{"name": "phone_number", "from": "phone_number"}],
                    resolved_config_hash=reg_hash,
                )
            ],
        )

        sql = build_flw_aggregation_query(config, opp_id)
        results = {row["username"]: row for row in self._execute(sql)}
        # Per-mother phones: ["111", "111", "222", "333"]
        # Dup count = 2 (the two "111"s). Total = 4. Share = 0.5
        assert results["a"]["phone_dup_share"] == pytest.approx(0.5, abs=0.01)

    def test_per_mother_cte_filtered_field_attributed(self, db):
        """Attributed field with a per-row filter (e.g. parity ANC-only)
        applies the filter inside the per-mother CTE and only counts
        ANC-matching values per mother. Catches the regression where the
        per-mother CTE refactor would silently apply the filter at the
        wrong stage and cross-contaminate non-ANC visits' parity.
        """
        opp_id = 9705
        # FLW 'a' has 2 mothers. M1: 1 ANC visit with parity G2P1, 1 non-ANC.
        # M2: 1 ANC with parity G3P2, 1 non-ANC with garbage value.
        future = timezone.now() + timezone.timedelta(days=1)
        rows = [
            ("a", "M1", "ANC Visit", "G2P1", 50001),
            ("a", "M1", "1 Week Visit", "GARBAGE_NON_ANC", 50002),
            ("a", "M2", "ANC Visit", "G3P2", 50003),
            ("a", "M2", "1 Week Visit", "OTHER_GARBAGE", 50004),
        ]
        for username, mid, form_name, parity, vid in rows:
            RawVisitCache.objects.create(
                opportunity_id=opp_id,
                visit_count=len(rows),
                expires_at=future,
                visit_id=str(vid),
                username=username,
                form_json={
                    "form": {
                        "@name": form_name,
                        "parents": {"parent": {"case": {"@case_id": mid}}},
                        "confirm_visit_information": {
                            "parity__of_live_births_or_stillbirths_after_24_weeks": parity,
                        },
                    }
                },
                visit_date="2024-01-15",
                status="approved",
            )

        config = AnalysisPipelineConfig(
            grouping_key="username",
            fields=[
                FieldComputation(
                    name="parity_dup_share",
                    path="form.confirm_visit_information.parity__of_live_births_or_stillbirths_after_24_weeks",
                    aggregation="dup_share",
                    pre_aggregate_by="form.parents.parent.case.@case_id",
                    pre_aggregation="last",
                    pre_aggregate_attribute_to="last_username",
                    filter_path="form.@name",
                    filter_value="ANC Visit",
                ),
                FieldComputation(
                    name="parity_mode_share",
                    path="form.confirm_visit_information.parity__of_live_births_or_stillbirths_after_24_weeks",
                    aggregation="mode_share",
                    pre_aggregate_by="form.parents.parent.case.@case_id",
                    pre_aggregation="last",
                    pre_aggregate_attribute_to="last_username",
                    filter_path="form.@name",
                    filter_value="ANC Visit",
                ),
                FieldComputation(
                    name="parity_mode_value",
                    path="form.confirm_visit_information.parity__of_live_births_or_stillbirths_after_24_weeks",
                    aggregation="mode",
                    pre_aggregate_by="form.parents.parent.case.@case_id",
                    pre_aggregation="last",
                    pre_aggregate_attribute_to="last_username",
                    filter_path="form.@name",
                    filter_value="ANC Visit",
                ),
            ],
        )

        sql = build_flw_aggregation_query(config, opp_id)
        results = {row["username"]: row for row in self._execute(sql)}

        # Filter must isolate ANC values only:
        # M1's per-mother parity = "G2P1" (ANC), NOT "GARBAGE_NON_ANC"
        # M2's per-mother parity = "G3P2" (ANC), NOT "OTHER_GARBAGE"
        # Per-FLW parity values: ["G2P1", "G3P2"] — both unique, no dups, no mode dominance.
        # If the filter leaked: per-mother LAST would pick "GARBAGE_*" (visit_id higher)
        # and parity_dup_share would change.
        assert results["a"]["parity_dup_share"] == pytest.approx(0.0, abs=0.01), (
            f"parity_dup_share={results['a']['parity_dup_share']} — filter likely leaked, "
            f"non-ANC visits contributed parity values"
        )
        # mode_share: 1/2 = 0.5 (both values appear once, max group size = 1, total = 2)
        assert results["a"]["parity_mode_share"] == pytest.approx(0.5, abs=0.01)
        # mode_value: ties broken by Postgres (G2P1 or G3P2), but never garbage values
        assert results["a"]["parity_mode_value"] in (
            "G2P1",
            "G3P2",
        ), f"mode_value={results['a']['parity_mode_value']} — filter leaked"

    def test_per_mother_cte_shares_across_multiple_attributed_fields(self, db):
        """Multiple attributed fields with the SAME pre_aggregate_by share
        one CTE — the actual optimization. Verifies the shared CTE emits
        correct values for all fields simultaneously, not just the first
        one declared.
        """
        opp_id = 9706
        self._seed_visits(opp_id, [("a", "M1"), ("a", "M2"), ("a", "M3")])
        reg_hash = "reghash06"
        self._seed_registrations_cache(
            opp_id,
            reg_hash,
            [
                # All 3 mothers share phone "111" — dup_share = 1.0
                # 2 of 3 mothers have age "30" — mode_share = 2/3 ≈ 0.67
                ("M1", {"phone_number": "111", "age": "30"}),
                ("M2", {"phone_number": "111", "age": "30"}),
                ("M3", {"phone_number": "111", "age": "25"}),
            ],
        )
        config = AnalysisPipelineConfig(
            grouping_key="username",
            fields=[
                FieldComputation(
                    name="phone_dup_share",
                    path="joined.registrations.phone_number",
                    aggregation="dup_share",
                    pre_aggregate_by="form.parents.parent.case.@case_id",
                    pre_aggregation="first",
                    pre_aggregate_attribute_to="last_username",
                ),
                FieldComputation(
                    name="age_mode_share",
                    path="joined.registrations.age",
                    aggregation="mode_share",
                    pre_aggregate_by="form.parents.parent.case.@case_id",
                    pre_aggregation="first",
                    pre_aggregate_attribute_to="last_username",
                ),
                FieldComputation(
                    name="age_mode_value",
                    path="joined.registrations.age",
                    aggregation="mode",
                    pre_aggregate_by="form.parents.parent.case.@case_id",
                    pre_aggregation="first",
                    pre_aggregate_attribute_to="last_username",
                ),
            ],
            joins=[
                JoinConfig(
                    from_alias="registrations",
                    local_key="form.parents.parent.case.@case_id",
                    remote_key_field="mother_case_id",
                    fields=[
                        {"name": "phone_number", "from": "phone_number"},
                        {"name": "age", "from": "age"},
                    ],
                    resolved_config_hash=reg_hash,
                )
            ],
        )
        sql = build_flw_aggregation_query(config, opp_id)
        # Each attributed field reads from the SAME CTE — exactly one
        # _per_mother_form_parents_parent_case_case_id alias appears in
        # the WITH clause (definition), referenced N times in SELECT.
        cte_def_count = sql.count("_per_mother_form_parents_parent_case_case_id AS MATERIALIZED")
        cte_ref_count = sql.count("FROM _per_mother_form_parents_parent_case_case_id")
        assert cte_def_count == 1, f"Expected 1 CTE definition, got {cte_def_count}"
        assert cte_ref_count == 3, f"Expected 3 references (one per attributed field), got {cte_ref_count}"

        results = {row["username"]: row for row in self._execute(sql)}
        # All 3 phones equal → all duplicate. dup_share = 1.0
        assert results["a"]["phone_dup_share"] == pytest.approx(1.0, abs=0.01)
        # Ages: ["30", "30", "25"] — mode "30" with count 2, total 3, share = 0.667
        assert results["a"]["age_mode_share"] == pytest.approx(2 / 3, abs=0.01)
        assert results["a"]["age_mode_value"] == "30"
