"""One visit per form, following the app's case flow (connect-labs#2620).

The app is the shape of the one the issue was found on: six modules, three paid
forms each under its own deliver unit, three unpaid forms, and a weekly survey.
Phones are handed over on the unpaid Follow-up Contact, only to adults with no
working phone whose ID was confirmed.
"""

from __future__ import annotations

from collections import Counter, defaultdict

import pytest

from connect_labs.labs.synthetic.generator.fixtures.engine import generate
from connect_labs.labs.synthetic.generator.fixtures.form_flow import CONNECT_DELIVER_XMLNS, FormFlowError
from connect_labs.labs.synthetic.generator.fixtures.manifest import Manifest, ManifestValidationError
from connect_labs.labs.synthetic.generator.fixtures.schema_loader import (
    parse_form_schema_from_app_json,
    parse_form_schemas_from_app_json,
)

XMLNS = "http://openrosa.org/formdesigner/"


def _q(path, kind="Text", options=()):
    return {"value": f"/data/{path}", "type": kind, "options": [{"value": o} for o in options]}


def _deliver_block():
    return {
        "value": "/data/connect_deliver",
        "type": "Group",
        "children": [_q("connect_deliver/deliver/name"), _q("connect_deliver/deliver/entity_id")],
    }


def _form(name, slug, questions, paid=False):
    return {
        "name": {"en": name},
        "xmlns": XMLNS + slug,
        "questions": questions + ([_deliver_block()] if paid else []),
    }


APP = {
    "deliver_app": {
        "modules": [
            {"forms": [_form("Household Registration", "hh-reg", [_q("hh_size", "Int"), _q("village")])]},
            {
                "forms": [
                    _form(
                        "Adult Enrolment",
                        "adult-enrol",
                        [
                            _q("has_working_phone", "Select", ["yes", "no"]),
                            _q("has_id", "Select", ["yes", "no"]),
                        ],
                        paid=True,
                    )
                ]
            },
            {"forms": [_form("ID Confirmation", "id-confirm", [_q("id_number")], paid=True)]},
            {"forms": [_form("Household Update", "hh-update", [_q("hh_size", "Int")])]},
            {
                "forms": [
                    _form(
                        "Follow-up Contact",
                        "followup",
                        [_q("phone_obtained", "Select", ["handed_over_today", "voucher_redeemed", "not_yet"])],
                    )
                ]
            },
            {"forms": [_form("Completion Confirmation", "complete", [_q("completed_ok")], paid=True)]},
            {"forms": [_form("Phone Stock Count", "stock", [_q("phones_in_stock", "Int")])]},
        ]
    }
}

DETAIL = {
    "id": 10950,
    "name": "Phones",
    "deliver_units": [
        {"id": 7317, "slug": "adult_enrolled", "name": "Adult Enrolment"},
        {"id": 7318, "slug": "id_obtained", "name": "ID Confirmation"},
        {"id": 7319, "slug": "completed", "name": "Completion Confirmation"},
    ],
    "payment_units": [
        {"id": 1, "name": "Enrolled", "deliver_units": [7317]},
        {"id": 2, "name": "ID", "deliver_units": [7318]},
        {"id": 3, "name": "Done", "deliver_units": [7319]},
    ],
}

PAID = {"Adult Enrolment": 7317, "ID Confirmation": 7318, "Completion Confirmation": 7319}

FORM_FLOW = """
form_flow:
  registration_weeks: 4
  forms:
    - name: Household Registration
      role: registration
    - name: Adult Enrolment
      deliver_unit: adult_enrolled
      delay_days: {min: 0, max: 3}
    - name: ID Confirmation
      after: [Adult Enrolment]
      deliver_unit: 7318
      when: {form.has_id: "yes"}
    - name: Household Update
      count: 2
    - name: Follow-up Contact
      after: [ID Confirmation]
      delay_days: {min: 3, max: 10}
      count: 2
      answers:
        - path: form.phone_obtained
          value: handed_over_today
          share: 0.65
          when: {form.has_working_phone: "no"}
          otherwise: not_yet
    - name: Completion Confirmation
      after: [Follow-up Contact]
      deliver_unit: {slug: completed}
    - name: Phone Stock Count
      role: survey
      per_week: 1
"""


def _manifest_yaml(form_flow: str = FORM_FLOW, size: int = 300) -> str:
    return f"""
opportunity_id: 10950
opportunity_name: Phones
random_seed: 7
timeline:
  start_date: 2026-01-05
  end_date: 2026-04-26
  weeks: 16
  visit_cadence_per_week_per_flw: {{mean: 6, stddev: 1}}
flw_personas:
  - id: flw_a
    archetype: steady
    accuracy_distribution: {{mean: 0.9, stddev: 0.05}}
    completeness_distribution: {{mean: 0.95, stddev: 0.03}}
    flag_rate: 0.0
  - id: flw_b
    archetype: rockstar
    accuracy_distribution: {{mean: 0.9, stddev: 0.05}}
    completeness_distribution: {{mean: 0.95, stddev: 0.03}}
    flag_rate: 0.0
beneficiary_cohorts:
  - id: adults
    size: {size}
    progression: flat
    field_distributions:
      form.has_working_phone: {{distribution: categorical, values: {{"yes": 0.3, "no": 0.7}}}}
      form.has_id: {{distribution: categorical, values: {{"yes": 0.8, "no": 0.2}}}}
      form.hh_size: {{distribution: normal, mean: 5, stddev: 1, lo: 1, integer: true}}
      form.phone_obtained:
        distribution: categorical
        values: {{handed_over_today: 0.5, voucher_redeemed: 0.2, not_yet: 0.3}}
      form.phones_in_stock: {{distribution: normal, mean: 20, stddev: 4, lo: 0, integer: true}}
kpi_config:
  - kpi: enrolled
    field_path: form.has_id
    aggregation: count
    threshold_underperform: 1
{form_flow}
"""


def _generate(form_flow: str = FORM_FLOW, size: int = 300, detail=DETAIL, app=APP):
    manifest = Manifest.from_yaml(_manifest_yaml(form_flow, size))
    return generate(
        manifest=manifest,
        opportunity_detail=detail,
        form_schema=parse_form_schema_from_app_json(app),
        app_structure=app,
    )


@pytest.fixture(scope="module")
def out():
    return _generate()


def _form_of(visit):
    return visit["form_json"]["form"]["@name"]


def _by_entity(visits):
    out = defaultdict(list)
    for v in visits:
        if v["entity_id"]:
            out[v["entity_id"]].append(v)
    return out


def test_the_app_is_read_form_by_form():
    forms = parse_form_schemas_from_app_json(APP)
    assert [f.name for f in forms][:2] == ["Household Registration", "Adult Enrolment"]
    enrol = forms[1]
    assert enrol.xmlns == XMLNS + "adult-enrol"
    assert enrol.deliver_path == "form.connect_deliver.deliver"
    assert {q.json_path for q in enrol.schema.questions} >= {"form.has_working_phone", "form.has_id"}
    assert forms[0].deliver_path is None  # unpaid: no <deliver> block


def test_every_visit_names_its_form_and_xmlns(out):
    xmlns = {f.name: f.xmlns for f in parse_form_schemas_from_app_json(APP)}
    seen = Counter(_form_of(v) for v in out["user_visits"])
    assert set(seen) == set(xmlns)  # every form of the flow was visited
    for v in out["user_visits"]:
        assert v["form_json"]["form"]["@xmlns"] == xmlns[_form_of(v)]
        assert v["form_json"]["xmlns"] == xmlns[_form_of(v)]


def test_paid_forms_are_filed_under_their_own_deliver_unit(out):
    for v in out["user_visits"]:
        name = _form_of(v)
        block = v["form_json"]["form"].get("connect_deliver", {}).get("deliver")
        if name in PAID:
            assert v["deliver_unit_id"] == PAID[name]
            assert v["deliver_unit"] == str(PAID[name])
            assert block["@xmlns"] == CONNECT_DELIVER_XMLNS
            assert block["@id"] in {"adult_enrolled", "id_obtained", "completed"}
            assert block["entity_id"] == v["entity_id"]
        else:
            assert v["deliver_unit_id"] is None and v["deliver_unit"] == ""
            assert block is None


def test_only_paid_forms_become_completed_work(out):
    by_id = {v["id"]: v for v in out["user_visits"]}
    assert out["completed_works"]
    unit_to_pu = {7317: 1, 7318: 2, 7319: 3}
    for w in out["completed_works"]:
        visit = by_id[int(w["id"].removesuffix("-cw"))]
        assert w["payment_unit_id"] == unit_to_pu[visit["deliver_unit_id"]]


def test_a_beneficiary_keeps_one_entity_id_from_its_registration(out):
    entities = _by_entity(out["user_visits"])
    assert len(entities) == 300
    for visits in entities.values():
        visits.sort(key=lambda v: v["visit_date"])
        assert _form_of(visits[0]) == "Household Registration"
        assert sum(_form_of(v) == "Household Registration" for v in visits) == 1
        assert len({v["entity_name"] for v in visits}) == 1
        assert len({v["username"] for v in visits}) == 1  # one worker owns a beneficiary


def test_follow_ups_come_after_the_forms_they_follow(out):
    for visits in _by_entity(out["user_visits"]).values():
        first = {}
        for v in sorted(visits, key=lambda v: v["visit_date"]):
            first.setdefault(_form_of(v), v["visit_date"])
        if "Follow-up Contact" in first:
            assert first["ID Confirmation"] <= first["Follow-up Contact"]
        if "Completion Confirmation" in first:
            assert first["Follow-up Contact"] <= first["Completion Confirmation"]


def test_a_visit_fills_its_own_forms_questions_only(out):
    for v in out["user_visits"]:
        form = v["form_json"]["form"]
        name = _form_of(v)
        if name != "Follow-up Contact":
            assert "phone_obtained" not in form
        if name != "Adult Enrolment":
            assert "has_working_phone" not in form and "has_id" not in form
        if name not in {"Household Registration", "Household Update"}:
            assert "hh_size" not in form


def test_the_merged_schema_puts_every_forms_answers_on_every_visit():
    # The control: without form_flow, the old generator fills a follow-up's answer on
    # registrations too -- the failure the tests above would catch.
    out = _generate(form_flow="")
    assert all("phone_obtained" in v["form_json"]["form"] for v in out["user_visits"])
    assert all("@name" not in v["form_json"]["form"] for v in out["user_visits"])
    assert len({v["deliver_unit_id"] for v in out["user_visits"]}) == 1


def test_a_form_happens_only_when_its_condition_holds(out):
    for visits in _by_entity(out["user_visits"]).values():
        has_id = next(v["form_json"]["form"]["has_id"] for v in visits if _form_of(v) == "Adult Enrolment")
        confirmed = any(_form_of(v) == "ID Confirmation" for v in visits)
        if has_id == "no":
            assert not confirmed
    assert any(_form_of(v) == "ID Confirmation" for v in out["user_visits"])


def test_a_conditional_answer_lands_on_the_share_of_eligible_beneficiaries_at_most_once(out):
    eligible = handed = 0
    for visits in _by_entity(out["user_visits"]).values():
        enrol = next(v["form_json"]["form"] for v in visits if _form_of(v) == "Adult Enrolment")
        contacts = [v["form_json"]["form"]["phone_obtained"] for v in visits if _form_of(v) == "Follow-up Contact"]
        got = contacts.count("handed_over_today")
        assert got <= 1  # at most once
        assert set(contacts) <= {"handed_over_today", "not_yet"}  # otherwise: not_yet, never a stray draw
        if enrol["has_working_phone"] == "yes":
            assert got == 0  # not eligible: has a phone
        elif contacts:
            eligible += 1
            handed += got
    assert eligible > 50
    assert 0.5 < handed / eligible < 0.8  # share 0.65


def test_without_otherwise_the_answer_never_appears_off_the_rule():
    flow = FORM_FLOW.replace("          otherwise: not_yet\n", "")
    out = _generate(flow)
    for visits in _by_entity(out["user_visits"]).values():
        enrol = next(v["form_json"]["form"] for v in visits if _form_of(v) == "Adult Enrolment")
        contacts = [v["form_json"]["form"].get("phone_obtained") for v in visits if _form_of(v) == "Follow-up Contact"]
        assert contacts.count("handed_over_today") <= (0 if enrol["has_working_phone"] == "yes" else 1)
    # The field's own draws still land where the rule does not fire.
    values = {v["form_json"]["form"].get("phone_obtained") for v in out["user_visits"]}
    assert "voucher_redeemed" in values


def test_a_survey_is_per_worker_per_week_and_about_no_beneficiary(out):
    surveys = [v for v in out["user_visits"] if _form_of(v) == "Phone Stock Count"]
    assert len(surveys) == 2 * 16
    assert all(v["entity_id"] == "" and v["deliver_unit_id"] is None for v in surveys)
    assert all(isinstance(v["form_json"]["form"]["phones_in_stock"], int) for v in surveys)


def test_form_flow_is_deterministic_under_seed():
    import json

    assert json.dumps(_generate(size=40), sort_keys=True) == json.dumps(_generate(size=40), sort_keys=True)


def test_without_an_app_structure_a_form_records_its_declared_fields():
    flow = FORM_FLOW.replace(
        "    - name: Household Registration\n      role: registration\n",
        "    - name: Household Registration\n      role: registration\n      fields: [form.hh_size]\n",
    )
    out = _generate(flow, size=20, app={})
    regs = [v for v in out["user_visits"] if _form_of(v) == "Household Registration"]
    assert regs and all(set(v["form_json"]["form"]) == {"hh_size", "@name", "@xmlns"} for v in regs)
    assert regs[0]["form_json"]["xmlns"] == "http://openrosa.org/formdesigner/synthetic/household-registration"
    # No app, so the <deliver> block goes where Connect's own test forms put it.
    enrol = next(v for v in out["user_visits"] if _form_of(v) == "Adult Enrolment")
    assert enrol["form_json"]["form"]["deliver"]["@id"] == "adult_enrolled"


def test_a_paid_form_whose_deliver_unit_the_opportunity_lacks_is_refused():
    with pytest.raises(FormFlowError, match="adult_enrolled"):
        _generate(size=5, detail={**DETAIL, "deliver_units": []})


def test_a_deliver_unit_given_by_id_needs_nothing_from_the_opportunity():
    flow = FORM_FLOW.replace("deliver_unit: adult_enrolled", "deliver_unit: {id: 7317, slug: adult_enrolled}").replace(
        "deliver_unit: {slug: completed}", "deliver_unit: {id: 7319, name: Completion Confirmation}"
    )
    out = _generate(flow, size=20, detail={**DETAIL, "deliver_units": []})
    done = next(v for v in out["user_visits"] if _form_of(v) == "Completion Confirmation")
    assert done["deliver_unit_id"] == 7319
    assert done["form_json"]["form"]["connect_deliver"]["deliver"]["@id"] == "completion-confirmation"


def test_a_form_the_app_does_not_have_is_refused():
    with pytest.raises(FormFlowError, match="Folow-up Contact"):
        _generate(FORM_FLOW.replace("Follow-up Contact", "Folow-up Contact"), size=5)


@pytest.mark.parametrize(
    "flow, message",
    [
        ("form_flow:\n  forms:\n    - name: A\n", "no registration form"),
        (
            "form_flow:\n  forms:\n    - {name: R, role: registration}\n    - {name: A, after: [B]}\n"
            "    - {name: B, after: [A]}\n",
            "cycle",
        ),
        ("form_flow:\n  forms:\n    - {name: R, role: registration}\n    - {name: A, after: [Z]}\n", "unknown form"),
        ("form_flow:\n  forms:\n    - {name: R, role: registration}\n    - {name: R}\n", "more than once"),
    ],
)
def test_a_malformed_flow_is_refused(flow, message):
    with pytest.raises(ManifestValidationError, match=message):
        Manifest.from_yaml(_manifest_yaml(flow))


def test_form_flow_cannot_ride_on_a_replayed_pool():
    yaml_text = _manifest_yaml().replace(
        "    progression: flat\n", "    progression: flat\n    longitudinal: {mode: mirror, transplant_pool: []}\n"
    )
    with pytest.raises(ManifestValidationError, match="mirror"):
        Manifest.from_yaml(yaml_text)
