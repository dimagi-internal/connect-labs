"""A field typed select whose answers are not a closed list is treated as free text.

Categorical values are copied into the manifest and, in mirror mode, into every
replayed case. So a "select" that really holds names, hand-typed places or ids must
contribute none of its values anywhere, while a genuine select keeps working.
"""

import uuid

import yaml

from connect_labs.labs.synthetic.generator.fixtures.manifest import Manifest
from connect_labs.labs.synthetic.generator.fixtures.profiler import (
    _MAX_CATEGORY_VALUES,
    _profile_categorical,
    _profile_repeat_groups,
    profile,
)

_SELECTS = ("sex", "village", "case_ref", "signs")


def _app():
    return {
        "learn_app": None,
        "deliver_app": {
            "modules": [
                {
                    "forms": [
                        {
                            "questions": [
                                {"value": f"/data/{name}", "type": "Select", "options": []} for name in _SELECTS
                            ]
                            + [{"value": "/data/weight", "type": "Decimal", "options": []}]
                        }
                    ]
                }
            ]
        },
    }


def _visits(n_entities=_MAX_CATEGORY_VALUES + 10):
    visits = []
    for e in range(n_entities):
        for i, day in enumerate(("2026-01-01", "2026-01-08")):
            visits.append(
                {
                    "username": "asha" if e % 2 else "ben",
                    "visit_date": day,
                    "status": "approved",
                    "entity_id": f"ent_{e}",
                    "form_json": {
                        "form": {
                            "sex": "m" if e % 3 else "f",
                            "village": f"Village number {e}",  # one per case: a place name, not an option
                            "case_ref": str(uuid.UUID(int=e + 1)) if e < 3 else "none",
                            "signs": "fever cough" if e % 2 else "none",
                            "weight": 1500.0 + 10 * e + 50 * i,
                        }
                    },
                }
            )
    return visits


def _profiled(mirror=True):
    return yaml.safe_load(
        profile(
            opportunity_id=10_050,
            user_visits=_visits(),
            user_data=[],
            opportunity_detail={"name": "X"},
            app_structure=_app(),
            mirror=mirror,
        )
    )


def test_a_select_with_too_many_distinct_answers_is_not_profiled():
    dists = _profiled(mirror=False)["beneficiary_cohorts"][0]["field_distributions"]
    assert "form.village" not in dists
    assert "form.sex" in dists and set(dists["form.sex"]["values"]) == {"m", "f"}
    # a multiselect's joined short codes are still a category
    assert set(dists["form.signs"]["values"]) == {"fever cough", "none"}


def test_a_select_whose_answers_look_like_ids_is_not_profiled():
    dists = _profiled(mirror=False)["beneficiary_cohorts"][0]["field_distributions"]
    assert "form.case_ref" not in dists


def test_free_text_selects_never_reach_the_transplant_pool():
    data = _profiled(mirror=True)
    Manifest.from_yaml(yaml.safe_dump(data))
    pool = data["beneficiary_cohorts"][0]["longitudinal"]["transplant_pool"]
    replayed = {path for series in pool for v in series["visits"] for path in (v.get("cats") or {})}
    assert "form.sex" in replayed and "form.signs" in replayed
    assert "form.village" not in replayed and "form.case_ref" not in replayed
    assert "Village number" not in yaml.safe_dump(data)


def test_the_cap_is_on_distinct_values_not_on_volume():
    visits = [{"form_json": {"form": {"s": str(i % _MAX_CATEGORY_VALUES)}}} for i in range(1000)]
    assert len(_profile_categorical(visits, "form.s")) == _MAX_CATEGORY_VALUES
    visits.append({"form_json": {"form": {"s": "one more"}}})
    assert _profile_categorical(visits, "form.s") == {}


def test_a_free_text_repeat_child_gets_no_distribution():
    visits = [
        {"form_json": {"form": {"kids": [{"name": f"Child {i}", "sex": "m" if i % 2 else "f"}]}}} for i in range(80)
    ]
    fields = _profile_repeat_groups(visits)["form.kids"]["field_distributions"]
    assert "name" not in fields
    assert set(fields["sex"]["values"]) == {"m", "f"}
