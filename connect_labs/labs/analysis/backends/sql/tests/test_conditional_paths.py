"""`conditional_paths`: a field whose correct path depends on WHICH FORM the row is.

The motivating case is the KMC baby's case id, and it cannot be expressed as a
single "first present" path list, because both candidate paths exist on both
registration designs and mean opposite things:

    design A  "Register KMC Beneficiary"   form.case.@case_id  = the BABY
                                            subcase_0.@case_id = another case
    design B  "Child Registration Form"    form.case.@case_id  = the MOTHER
                                            subcase_0.@case_id = the BABY
    visits    "Record Visit Details"       kmc_beneficiary_case_id (A) /
                                            child_case_id (B)  = the BABY,
                                            and a fresh subcase_0 per visit

Observed on production data 2026-09-11 (opps 523 and 675 for design A, 1487 and
1790 for design B). Every ordering of one list fixes one design and splits the
other: registration and visits land on different ids, so no single baby carries
both a birthweight and a weight series. On 675 that meant zero qualifying
babies; on BERI it doubled the case count and blanked the discharge metric.

The fixtures below are those shapes, not invented ones. Both engines are held to
the same answer on every row, because two engines disagreeing about a transform
is exactly the class that already exists here (`kg_to_g` multiplies by 1000 in
Python and not at all in SQL).
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.utils import timezone

from connect_labs.labs.analysis.backends.sql.models import RawVisitCache
from connect_labs.labs.analysis.backends.sql.query_builder import _field_value_sql
from connect_labs.labs.analysis.computations import _extract_field_value
from connect_labs.labs.analysis.config import FieldComputation

BABY_A, OTHER_A = "a57fb7ad-baby-A", "706dc2f9-other-A"
MOTHER_B, BABY_B = "afca8b7b-mother-B", "cbe51554-baby-B"
VISIT_SUBCASE = "479a642b-per-visit"

ROWS = [
    # (label, form_json, expected baby id)
    (
        "design A registration: form.case is the baby, subcase_0 is not",
        {
            "form": {
                "@name": "Register KMC Beneficiary",
                "case": {"@case_id": BABY_A},
                "subcase_0": {"case": {"@case_id": OTHER_A}},
            }
        },
        BABY_A,
    ),
    (
        "design A visit: kmc_beneficiary_case_id is the baby",
        {
            "form": {
                "@name": "Record Visit Details",
                "kmc_beneficiary_case_id": BABY_A,
                "case": {"@case_id": BABY_A},
                "subcase_0": {"case": {"@case_id": VISIT_SUBCASE}},
            }
        },
        BABY_A,
    ),
    (
        "design B registration: subcase_0 is the baby, form.case is the mother",
        {
            "form": {
                "@name": "Child Registration Form",
                "case": {"@case_id": MOTHER_B},
                "subcase_0": {"case": {"@case_id": BABY_B}},
            }
        },
        BABY_B,
    ),
    (
        "design B visit: child_case_id is the baby, form.case is the mother",
        {
            "form": {
                "@name": "Record Visit Details",
                "child_case_id": BABY_B,
                "case": {"@case_id": MOTHER_B},
                "subcase_0": {"case": {"@case_id": BABY_B}},
            }
        },
        BABY_B,
    ),
    (
        "design B registration missing its subcase falls back to the default paths",
        {"form": {"@name": "Child Registration Form", "case": {"@case_id": MOTHER_B}}},
        MOTHER_B,
    ),
]


def _baby_field() -> FieldComputation:
    return FieldComputation(
        name="baby_case_id",
        paths=["form.kmc_beneficiary_case_id", "form.child_case_id", "form.case.@case_id"],
        conditional_paths=[
            {
                "when_path": "form.@name",
                "when_value": "Child Registration Form",
                "paths": ["form.subcase_0.case.@case_id"],
            }
        ],
    )


# ---------------------------------------------------------------------------
# Execution: the SQL actually runs in Postgres and picks the right id per row.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSqlPicksTheBabyOnEveryForm:
    OPP = 987654

    def _seed(self):
        future = timezone.now() + timezone.timedelta(days=1)
        for i, (_label, form_json, _want) in enumerate(ROWS):
            RawVisitCache.objects.create(
                opportunity_id=self.OPP,
                visit_count=len(ROWS),
                expires_at=future,
                visit_id=str(50000 + i),
                username="flw",
                form_json=form_json,
                visit_date="2026-09-01",
            )

    def _values(self, field):
        table = RawVisitCache._meta.db_table
        with connection.cursor() as cur:
            cur.execute(
                f"SELECT visit_id, {_field_value_sql(field)} FROM {table} "
                f"WHERE opportunity_id = %s ORDER BY visit_id",
                [self.OPP],
            )
            return [row[1] for row in cur.fetchall()]

    def test_each_form_design_resolves_to_its_own_baby(self):
        self._seed()
        got = self._values(_baby_field())
        for (label, _fj, want), value in zip(ROWS, got):
            assert value == want, label

    def test_without_the_condition_one_design_is_always_wrong(self):
        # The control: the same default list with no condition keys design B's
        # registration onto the mother -- the split this capability exists to end.
        self._seed()
        plain = FieldComputation(
            name="baby_case_id",
            paths=["form.kmc_beneficiary_case_id", "form.child_case_id", "form.case.@case_id"],
        )
        got = self._values(plain)
        assert got[2] == MOTHER_B, "design B registration lands on the mother without the condition"

    def test_a_list_of_values_matches_any_of_them(self):
        self._seed()
        field = FieldComputation(
            name="baby_case_id",
            paths=["form.kmc_beneficiary_case_id", "form.child_case_id", "form.case.@case_id"],
            conditional_paths=[
                {
                    "when_path": "form.@name",
                    "when_value": ["Child Registration Form", "Register Child"],
                    "paths": ["form.subcase_0.case.@case_id"],
                }
            ],
        )
        assert self._values(field)[2] == BABY_B


# ---------------------------------------------------------------------------
# Engine parity: the Python extractor gives the same answer on every row.
# ---------------------------------------------------------------------------


class TestPythonMatchesSql:
    @pytest.mark.parametrize("label,form_json,want", ROWS, ids=[r[0] for r in ROWS])
    def test_python_extraction_picks_the_same_baby(self, label, form_json, want):
        assert _extract_field_value(form_json, _baby_field()) == want


# ---------------------------------------------------------------------------
# Declaration: validated at construction, parsed from a pipeline schema, and
# part of the cache key.
# ---------------------------------------------------------------------------


class TestDeclaration:
    def test_conditional_paths_alone_satisfy_the_path_requirement(self):
        FieldComputation(
            name="x", conditional_paths=[{"when_path": "form.@name", "when_value": "F", "paths": ["form.a"]}]
        )

    @pytest.mark.parametrize(
        "entry,needle",
        [
            ({"when_value": "F", "paths": ["form.a"]}, "when_path"),
            ({"when_path": "form.@name", "paths": ["form.a"]}, "when_value"),
            ({"when_path": "form.@name", "when_value": "F", "paths": []}, "paths"),
            ({"when_path": "form.@name", "when_value": "F"}, "paths"),
        ],
    )
    def test_a_malformed_entry_is_refused_by_name(self, entry, needle):
        with pytest.raises(ValueError) as e:
            FieldComputation(name="x", paths=["form.a"], conditional_paths=[entry])
        assert needle in str(e.value)

    def test_the_schema_parser_carries_conditional_paths_through(self):
        from connect_labs.workflow.data_access import PipelineDataAccess

        cond = [
            {
                "when_path": "form.@name",
                "when_value": "Child Registration Form",
                "paths": ["form.subcase_0.case.@case_id"],
            }
        ]
        schema = {
            "data_source": {"type": "connect_csv"},
            "grouping_key": "username",
            "terminal_stage": "visit_level",
            "fields": [{"name": "baby_case_id", "paths": ["form.case.@case_id"], "conditional_paths": cond}],
        }
        config = PipelineDataAccess.__new__(PipelineDataAccess)._schema_to_config(schema, 1)
        field = next(f for f in config.fields if f.name == "baby_case_id")
        assert field.conditional_paths == cond

    def test_changing_the_condition_changes_the_cache_key(self):
        # Otherwise editing a condition keeps serving rows computed under the old one
        # for the life of the cache -- a wrong baby id that looks cached, not broken.
        from connect_labs.labs.analysis.config import AnalysisPipelineConfig
        from connect_labs.labs.analysis.utils import get_config_hash

        def key(value):
            field = FieldComputation(
                name="baby_case_id",
                paths=["form.case.@case_id"],
                conditional_paths=[
                    {"when_path": "form.@name", "when_value": value, "paths": ["form.subcase_0.case.@case_id"]}
                ],
            )
            return get_config_hash(AnalysisPipelineConfig(grouping_key="username", fields=[field]))

        assert key("Child Registration Form") != key("Something Else")


# ---------------------------------------------------------------------------
# The path the METRICS use. `semantic/layer1.py` builds the visit SQL behind every
# KMC indicator from `generate_sql_preview`'s field expressions -- so this is the
# site that decides the numbers, and the one whose rewrite first shipped with an
# undefined name. Nothing else here executes it.
# ---------------------------------------------------------------------------


class TestTheSemanticLayerReadsTheCondition:
    def _config(self):
        from connect_labs.labs.analysis.config import AnalysisPipelineConfig

        return AnalysisPipelineConfig(grouping_key="username", fields=[_baby_field()])

    def test_generate_sql_preview_carries_the_conditional_expression(self):
        from connect_labs.labs.analysis.backends.sql.query_builder import generate_sql_preview

        expr = generate_sql_preview(self._config(), 1)["field_expressions"]["baby_case_id"]
        assert "Child Registration Form" in expr["transformed_sql"]
        assert "subcase_0" in expr["transformed_sql"]
        assert expr["paths"] == ["form.kmc_beneficiary_case_id", "form.child_case_id", "form.case.@case_id"]
        assert expr["conditional_paths"][0]["when_value"] == "Child Registration Form"

    @pytest.mark.django_db
    def test_the_expression_layer1_uses_executes_and_picks_the_babies(self):
        # Execute the very `transformed_sql` Layer 1 splices in, on the production shapes.
        from connect_labs.labs.analysis.backends.sql.query_builder import generate_sql_preview

        TestSqlPicksTheBabyOnEveryForm()._seed()
        sql = generate_sql_preview(self._config(), 1)["field_expressions"]["baby_case_id"]["transformed_sql"]
        with connection.cursor() as cur:
            cur.execute(
                f"SELECT {sql} FROM {RawVisitCache._meta.db_table} WHERE opportunity_id = %s ORDER BY visit_id",
                [TestSqlPicksTheBabyOnEveryForm.OPP],
            )
            got = [r[0] for r in cur.fetchall()]
        assert got == [want for _label, _fj, want in ROWS]
