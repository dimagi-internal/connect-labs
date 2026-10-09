"""Invented mother and baby names for synthetic cases: deterministic per case id,
locale-appropriate, written at the app's own name questions."""

import pytest
import yaml

from connect_labs.labs.synthetic.cohort import CohortSpec
from connect_labs.labs.synthetic.generator.fixtures.person_names import (
    LOCALES,
    PersonNamesConfig,
    apply_person_names,
    config_for_source,
    names_for,
)

PATHS = {
    "mother": ["form.mothers_details.mother_name"],
    "child": ["form.child_details.child_name"],
    "sex": ["form.child_details.child_gender"],
}


def test_a_case_gets_the_same_names_every_time_and_cases_differ():
    a = names_for("0b8ecea6-331d-0202-606f-82de3f15119a", "ng_hausa", "Male")
    assert a == names_for("0b8ecea6-331d-0202-606f-82de3f15119a", "ng_hausa", "Male")
    assert len({names_for(f"case-{n}", "ng_hausa") for n in range(40)}) > 30
    child, mother = a
    assert child in LOCALES["ng_hausa"]["boy"]
    first, family = mother.split(" ", 1)
    assert first in LOCALES["ng_hausa"]["mother"] and family in LOCALES["ng_hausa"]["family"]


def test_a_girl_gets_a_girls_name():
    for n in range(20):
        assert names_for(f"c{n}", "ke", "Female")[0] in LOCALES["ke"]["girl"]


def test_names_go_on_every_visit_of_a_case_at_the_configured_paths():
    visits = [
        {"entity_id": "x1", "form_json": {"form": {"child_details": {"child_gender": "Female"}}}},
        {"entity_id": "x1", "form_json": {"form": {}}},
        {"entity_id": "x2", "form_json": {"form": {"mothers_details": {"mother_age": 30}}}},
    ]
    config = PersonNamesConfig(
        locale="ug", mother_paths=PATHS["mother"], child_paths=PATHS["child"], sex_paths=PATHS["sex"]
    )
    assert apply_person_names(visits, config) == 2
    first, second, other = (v["form_json"]["form"] for v in visits)
    assert first["child_details"]["child_name"] == second["child_details"]["child_name"]
    assert first["child_details"]["child_name"] in LOCALES["ug"]["girl"]  # the sex on ANY visit decides
    assert first["mothers_details"]["mother_name"] == second["mothers_details"]["mother_name"]
    assert other["mothers_details"]["mother_age"] == 30  # the rest of the group is kept


def test_the_cohort_spec_chooses_the_locale_per_source_opportunity():
    spec = {"paths": PATHS, "locale_by_source": {1236: "ng_hausa", "675": "ke"}}
    assert config_for_source(spec, 1236).locale == "ng_hausa"
    assert config_for_source(spec, 675).locale == "ke"
    assert config_for_source(spec, 999) is None
    assert config_for_source({**spec, "default_locale": "ng"}, 999).locale == "ng"
    with pytest.raises(ValueError):
        PersonNamesConfig(locale="xx")


def test_the_kmc_cohort_names_every_source_and_round_trips():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[3] / "cohorts" / "kmc.yaml").read_text()
    spec = CohortSpec.from_yaml(text)
    assert spec.person_names
    for source in spec.opportunity_ids:
        config = config_for_source(spec.person_names, source)
        assert config is not None and config.mother_paths and config.child_paths, source
    assert config_for_source(spec.person_names, 1236).locale == "ng_hausa"  # EHA, Kano
    assert yaml.safe_load(spec.to_yaml())["person_names"] == spec.person_names
