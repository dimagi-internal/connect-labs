"""A showcase case has to be openable, longitudinal, and honest about its outcome.

The probabilistic image knobs answer "does the audit find anything". These pin the
other question — "show me the faltering-growth case" — which a spread cannot answer
because it contains no case you can name.
"""

import datetime as dt

import pytest

from connect_labs.labs.synthetic.generator.fixtures import corpus_manifest as cm
from connect_labs.labs.synthetic.generator.fixtures.manifest import ImageConfig
from connect_labs.labs.synthetic.generator.fixtures.showcase import ShowcaseError, build_showcase_visits

CORPUS = "kmc-scale"
WEIGHT = "form.anthropometric.child_weight_visit"
PHOTO = "form.anthropometric.upload_weight_image"


def _cfg(**over):
    kw = dict(
        question_path=PHOTO,
        corpus=CORPUS,
        measurement_field_match="weight",
        reading_path=WEIGHT,
        probability=0.0,
        good_image_count=83,
        bad_image_count=10,
    )
    kw.update(over)
    return ImageConfig(**kw)


def _weight(v):
    return v["form_json"]["form"]["anthropometric"]["child_weight_visit"]


def _weighings(visits):
    """The photographed visits -- every case now opens with a registration form
    that carries neither a weighing nor a photo (see test_a_case_opens_with_...)."""
    return [v for v in visits if v["images"]]


def _build(cases, **kw):
    return build_showcase_visits(_cfg(showcase=cases), opportunity_id=10015, start_date=dt.date(2026, 3, 2), **kw)


def test_no_showcase_declared_changes_nothing():
    assert build_showcase_visits(_cfg(), opportunity_id=1, start_date=dt.date(2026, 1, 1)) == []


def test_a_case_replays_a_real_trajectory_with_a_photo_on_every_visit():
    """Longitudinal by construction — the demo needs a series, not one frame."""
    visits = _weighings(_build([{"name": "Steady Gain", "trajectory": "normal_02", "flw": "flw_001"}]))
    series = cm.trajectories(CORPUS)["normal_02"]
    assert len(visits) == len(series)
    assert all(len(v["images"]) == 1 for v in visits)
    assert [_weight(v) for v in visits] == [p["reading_grams"] for p in series]
    # and it is a rising series, which is the story being shown
    assert _weight(visits[-1]) > _weight(visits[0])


def test_good_and_insufficient_gain_are_real_source_properties():
    """Not fabricated: the contrast comes from which real infant is replayed."""
    good = _weighings(_build([{"name": "Steady Gain", "trajectory": "normal_02", "flw": "flw_001"}]))
    poor = _weighings(_build([{"name": "Faltering", "trajectory": "slow_03", "flw": "flw_001"}]))
    good_gain = _weight(good[-1]) - _weight(good[0])
    poor_gain = _weight(poor[-1]) - _weight(poor[0])
    assert good_gain > 500, good_gain
    assert poor_gain < 150, poor_gain


def test_a_passing_case_enters_exactly_what_its_photo_shows():
    visits = _weighings(_build([{"name": "Clean", "trajectory": "normal_05", "flw": "flw_001", "outcome": "pass"}]))
    readings = cm.readings_for(CORPUS)
    for v in visits:
        assert _weight(v) == readings[v["images"][0]["blob_id"]]


def test_fail_number_keeps_a_readable_photo_but_enters_the_wrong_value():
    """The payment-integrity case: right infant, readable frame, wrong number."""
    visits = _weighings(
        _build([{"name": "Typo", "trajectory": "normal_05", "flw": "flw_007", "outcome": "fail_number"}])
    )
    readings = cm.readings_for(CORPUS)
    for v in visits:
        blob = v["images"][0]["blob_id"]
        assert "-good-" in blob, "a wrong NUMBER still needs a readable photo"
        assert _weight(v) != readings[blob]


def test_fail_photo_uses_an_unusable_frame_and_leaves_the_weight_alone():
    visits = _weighings(
        _build([{"name": "Blurred", "trajectory": "slow_10", "flw": "flw_007", "outcome": "fail_photo"}])
    )
    series = cm.trajectories(CORPUS)["slow_10"]
    assert [_weight(v) for v in visits] == [p["reading_grams"] for p in series]
    assert all("-bad-" in v["images"][0]["blob_id"] for v in visits)
    # a multi-visit case must not repeat one frame
    assert len({v["images"][0]["blob_id"] for v in visits}) == len(visits)


def test_a_case_is_findable_and_stable_across_runs():
    """The whole point: hand someone a name, and it is the same case next week."""
    a = _build([{"name": "KMC Demo — Faltering Growth", "trajectory": "slow_03", "flw": "flw_001"}])
    b = _build([{"name": "KMC Demo — Faltering Growth", "trajectory": "slow_03", "flw": "flw_001"}])
    assert {v["entity_name"] for v in a} == {"KMC Demo — Faltering Growth"}
    assert len({v["entity_id"] for v in a}) == 1, "one case, one entity"
    assert [v["entity_id"] for v in a] == [v["entity_id"] for v in b], "ids must be reproducible"


def test_outcomes_group_by_worker_so_an_FLW_reads_clean_or_suspect():
    visits = _build(
        [
            {"name": "Clean A", "trajectory": "normal_02", "flw": "flw_001", "outcome": "pass"},
            {"name": "Clean B", "trajectory": "slow_03", "flw": "flw_001", "outcome": "pass"},
            {"name": "Bad A", "trajectory": "normal_05", "flw": "flw_007", "outcome": "fail_number"},
        ]
    )
    readings = cm.readings_for(CORPUS)
    by_flw = {}
    for v in _weighings(visits):
        blob = v["images"][0]["blob_id"]
        agrees = _weight(v) == readings.get(blob)
        by_flw.setdefault(v["username"], []).append(agrees)
    assert all(by_flw["flw_001"]), "the clean worker's whole caseload agrees"
    assert not any(by_flw["flw_007"]), "the suspect worker's does not"


def test_showcase_visits_are_ordinary_approved_work():
    """over_limit / rejected visits never reach review — a demo case must be
    reviewable, so the AI reviewer (not Connect's status) decides its fate."""
    visits = _build([{"name": "Clean", "trajectory": "normal_02", "flw": "flw_001"}])
    assert all(v["status"] == "approved" and not v["flagged"] for v in visits)


def test_a_case_opens_with_a_registration_form_the_pipeline_can_read():
    """A cohort case is a mirror of a real infant and opens with the Child
    Registration Form; a showcase case used to be a bare weight and photo per
    visit. The KMC case-properties pipeline then saw no registration at all:
    the worker review's record rail read as dashes, the growth chart fell back
    to "days since first weighing", and the growth class could not be graded
    for want of a birthweight band (run 5620, 2026-09-10). The registration is
    written at the pipeline's own extraction paths."""
    visits = _build([{"name": "Steady Gain", "trajectory": "normal_02", "flw": "flw_001"}])
    reg, weighings = visits[0], _weighings(visits)
    assert len(visits) == len(weighings) + 1
    assert reg["images"] == [] and "anthropometric" not in reg["form_json"]["form"]
    assert reg["visit_date"] < weighings[0]["visit_date"], "registered before the first weighing"
    upd = reg["form_json"]["form"]["subcase_0"]["case"]["update"]
    assert reg["form_json"]["form"]["@name"] == "Child Registration Form"
    assert upd["reg_date"] == reg["visit_date"]
    assert upd["child_DOB"] < upd["date_hospital_discharge"] < upd["reg_date"]
    assert 0 < upd["child_weight_birth"] < _weight(weighings[0])
    assert upd["child_weight_reg"] == _weight(weighings[0])
    assert upd["child_weight_reg"] != upd["child_weight_birth"], "not a birth-copy"
    assert 20 <= upd["gestational_age_at_birth_lmp"] <= 45
    assert upd["child_gender"] in ("Female", "Male")
    assert reg["form_json"]["form"]["case"]["@case_id"] == reg["entity_id"]
    for v in weighings:
        assert v["form_json"]["form"]["@name"] == "Record Visit Details"
        assert v["form_json"]["form"]["case"]["@case_id"] == v["entity_id"]
        assert v["form_json"]["form"]["child_alive"] == "yes"
    # ids stay stable and distinct from the weighings' ids
    assert reg["entity_id"] == weighings[0]["entity_id"]
    assert len({v["xform_id"] for v in visits}) == len(visits)


def test_an_unknown_trajectory_fails_loudly_and_names_what_exists():
    with pytest.raises(ShowcaseError, match="does not carry"):
        _build([{"name": "Nope", "trajectory": "normal_99", "flw": "flw_001"}])


def test_the_same_case_gets_a_DIFFERENT_id_in_each_opportunity():
    """The same showcase block is applied to every clone in a cohort.

    Keyed on the name alone, one demo case carries ONE id across all eleven KMC
    opportunities, and cross-opp analysis — which is what the growth-curve work
    exists to do — would see four infants with ~40 visits across ten sites in
    three countries. That reads as data corruption, not as a demo.
    """
    case = [{"name": "KMC Demo — Steady Gain", "trajectory": "normal_02", "flw": "flw_001"}]
    a = build_showcase_visits(_cfg(showcase=case), opportunity_id=10015, start_date=dt.date(2026, 3, 2))
    b = build_showcase_visits(_cfg(showcase=case), opportunity_id=10022, start_date=dt.date(2026, 3, 2))
    assert a[0]["entity_name"] == b[0]["entity_name"], "same case, same name"
    assert a[0]["entity_id"] != b[0]["entity_id"], "same case, different opp -> different id"
    # ...and still reproducible within one opportunity
    again = build_showcase_visits(_cfg(showcase=case), opportunity_id=10015, start_date=dt.date(2026, 3, 2))
    assert [v["entity_id"] for v in a] == [v["entity_id"] for v in again]
    assert len({v["xform_id"] for v in a} & {v["xform_id"] for v in b}) == 0, "xform ids must not collide either"


# ---------------------------------------------------------------------------
# Showcase cases must land in the SAME field as the population (#1602).
#
# A showcase visit is built from nothing, so it has no existing value to resolve
# a multi-candidate reading_path against and would default to the first. For a
# cohort using the second candidate that splits the demo cases into a field the
# audit does not read -- which is exactly what KMC opp 675 looked like before
# this: 16 showcase visits at `child_weight_visit`, 489 population visits at
# `child_weight`.
# ---------------------------------------------------------------------------


def test_showcase_writes_the_path_the_cohort_resolved_to():
    import datetime as dt

    from connect_labs.labs.synthetic.generator.fixtures.manifest import ImageConfig, ShowcaseCase
    from connect_labs.labs.synthetic.generator.fixtures.showcase import build_showcase_visits

    cfg = ImageConfig(
        question_path="form.anthropometric.upload_weight_image",
        corpus="kmc-scale",
        measurement_field_match="weight",
        probability=0.0,
        good_image_count=83,
        bad_image_count=10,
        reading_path=[
            "form.anthropometric.child_weight_visit",
            "form.anthropometric.child_weight",
        ],
        showcase=[ShowcaseCase(name="Demo", trajectory="normal_02", flw="flw_001", outcome="pass")],
    )

    followed = build_showcase_visits(
        cfg,
        opportunity_id=675,
        start_date=dt.date(2026, 5, 4),
        reading_path="form.anthropometric.child_weight",
    )
    assert followed, "the case should produce visits"
    for v in _weighings(followed):
        anthro = v["form_json"]["form"]["anthropometric"]
        assert "child_weight" in anthro
        assert "child_weight_visit" not in anthro, "must not split off into the unused candidate"

    # Control: with no override it falls back to the first candidate, which is
    # correct for the ten opportunities that do use it.
    default = build_showcase_visits(cfg, opportunity_id=874, start_date=dt.date(2026, 5, 4))
    for v in _weighings(default):
        assert "child_weight_visit" in v["form_json"]["form"]["anthropometric"]


# ---------------------------------------------------------------------------
# The clone path: a showcase case is a duplicate of a standard case in the same
# cohort with the showcase specifics applied, not a form built from nothing.
# ---------------------------------------------------------------------------


def _template_case(entity_id, username, n_followups, reg_date=dt.date(2026, 1, 20), extra=None):
    """A mirrored case as the engine holds it: a registration form followed by
    weighed follow-ups, every form carrying fields the showcase never names."""
    reg = {
        "id": 1,
        "xform_id": f"x-{entity_id}-reg",
        "opportunity_id": 999,
        "username": username,
        "entity_id": entity_id,
        "entity_name": "Beneficiary 7",
        "visit_date": reg_date.isoformat(),
        "status": "over_limit",
        "flagged": True,
        "flag_reason": "late",
        "images": [],
        "form_json": {
            "form": {
                "@name": "Child Registration Form",
                "case": {"@case_id": entity_id, "update": {"child_alive": "yes"}},
                "subcase_0": {
                    "case": {
                        "@case_id": entity_id,
                        "update": {
                            "reg_date": reg_date.isoformat(),
                            "child_DOB": (reg_date - dt.timedelta(days=9)).isoformat(),
                            "date_hospital_discharge": (reg_date - dt.timedelta(days=3)).isoformat(),
                            "child_gender": "Male",
                            "child_weight_birth": 2100.0,
                            "child_weight_reg": 2150.0,
                            "gestational_age_at_birth_lmp": 36.0,
                            "kmc_status": "enrolled",
                            "child_alive": "yes",
                        },
                    }
                },
                "mothers_details": {"mother_name": "Amina", "gestational_age_at_birth_lmp": 36.0},
                "child_details": {"birth_weight_group": {"child_weight_birth": 2100.0}, "some_other_field": "kept"},
                "meta": {"timeEnd": reg_date.isoformat() + "T10:15:00.000000Z"},
            }
        },
    }
    if extra:
        reg["form_json"]["form"].update(extra)
    followups = []
    for i in range(n_followups):
        d = reg_date + dt.timedelta(days=3 + 7 * i)
        followups.append(
            {
                "id": 100 + i,
                "xform_id": f"x-{entity_id}-{i}",
                "opportunity_id": 999,
                "username": username,
                "entity_id": entity_id,
                "entity_name": "Beneficiary 7",
                "visit_date": d.isoformat(),
                "status": "approved",
                "flagged": False,
                "flag_reason": "",
                "images": [{"blob_id": "cohort-photo", "name": "cohort.jpg"}],
                "form_json": {
                    "form": {
                        "@name": "Record Visit Details",
                        "case": {
                            "@case_id": entity_id,
                            "update": {
                                "child_alive": "yes",
                                "child_weight_last_visit": 2000 + i,
                                "kmc_status": "KMC visits in progress",
                            },
                        },
                        "child_alive": "yes",
                        "anthropometric": {
                            "child_weight_visit": 2000 + i,
                            "upload_weight_image": "cohort.jpg",
                            "muac": 11.5,
                        },
                        "feeding_checklist": {"direct_breastfeeding": "yes"},
                        "meta": {"timeEnd": d.isoformat() + "T09:00:00.000000Z"},
                    }
                },
            }
        )
    return [reg] + followups


def _clone_build(templates, case, **kw):
    cfg = _cfg(showcase=[case])
    return build_showcase_visits(
        cfg, opportunity_id=10015, start_date=dt.date(2026, 3, 2), template_visits=templates, **kw
    )


def test_a_showcase_case_is_a_duplicate_of_a_standard_case_with_the_specifics_applied():
    """Jon: "follow the clone path as much as possible (duplicate a standard case)
    and then apply the showcase specifics". Fields the showcase never names come
    along; identity, dates, weights and photos are the showcase's own."""
    import copy

    templates = _template_case("tmpl-1", "flw_001", 5)
    frozen = copy.deepcopy(templates)
    visits = _clone_build(templates, {"name": "Steady Gain", "trajectory": "normal_02", "flw": "flw_001"})
    assert templates == frozen, "the template must not be mutated"
    series = cm.trajectories(CORPUS)["normal_02"]
    reg, weighings = visits[0], _weighings(visits)
    assert len(weighings) == len(series)
    form = reg["form_json"]["form"]
    # the unnamed fields came along
    assert form["mothers_details"]["mother_name"] == "Amina"
    assert form["child_details"]["some_other_field"] == "kept"
    assert weighings[0]["form_json"]["form"]["anthropometric"]["muac"] == 11.5
    assert weighings[0]["form_json"]["form"]["feeding_checklist"]["direct_breastfeeding"] == "yes"
    # identity is the showcase's
    for v in visits:
        assert v["entity_id"] == reg["entity_id"] != "tmpl-1"
        assert v["entity_name"] == "Steady Gain"
        assert v["username"] == "flw_001"
        assert v["status"] == "approved" and not v["flagged"]
        assert v["form_json"]["form"]["case"]["@case_id"] == v["entity_id"], "no template id may leak"
    assert len({v["xform_id"] for v in visits}) == len(visits)
    # dates: registration the day before the first weighing, weighings weekly,
    # and the template's own offsets (birth -9, discharge -3) preserved
    assert reg["visit_date"] == "2026-03-01"
    assert [v["visit_date"] for v in weighings] == ["2026-03-02", "2026-03-09", "2026-03-16", "2026-03-23"]
    upd = form["subcase_0"]["case"]["update"]
    assert upd["reg_date"] == "2026-03-01"
    assert upd["child_DOB"] == "2026-02-20" and upd["date_hospital_discharge"] == "2026-02-26"
    assert form["meta"]["timeEnd"] == "2026-03-01T10:15:00.000000Z", "time suffixes survive the shift"
    # weights and photos are the trajectory's, at every place the template kept a weight
    for v, p in zip(weighings, series):
        f = v["form_json"]["form"]
        assert f["anthropometric"]["child_weight_visit"] == p["reading_grams"]
        assert f["case"]["update"]["child_weight_last_visit"] == p["reading_grams"]
        assert v["images"] == [{"blob_id": p["blob_id"], "name": f["anthropometric"]["upload_weight_image"]}]
    # birth / enrolment weight and gestational age fit the trajectory, not the template
    assert (
        upd["child_weight_birth"] == 1250.0
        and form["child_details"]["birth_weight_group"]["child_weight_birth"] == 1250.0
    )
    assert upd["child_weight_reg"] == 1350.0
    assert (
        upd["gestational_age_at_birth_lmp"] == 31.0 and form["mothers_details"]["gestational_age_at_birth_lmp"] == 31.0
    )
    assert reg["showcase"]["cloned_from"] == "tmpl-1"


def test_the_template_is_picked_deterministically_and_from_the_same_worker_when_possible():
    templates = (
        _template_case("tmpl-a", "flw_009", 5)
        + _template_case("tmpl-b", "flw_001", 5)
        + _template_case("tmpl-c", "flw_001", 2)  # too short for a 4-point trajectory
    )
    case = {"name": "Steady Gain", "trajectory": "normal_02", "flw": "flw_001"}
    a = _clone_build(templates, case)
    b = _clone_build(templates, case)
    assert a[0]["showcase"]["cloned_from"] == "tmpl-b", "the same worker's long-enough case wins"
    assert [v["xform_id"] for v in a] == [v["xform_id"] for v in b], "same pick every run"
    # a worker with no qualifying case borrows another worker's, and still gets the username
    other = _clone_build(
        templates, {"name": "Typo", "trajectory": "normal_05", "flw": "flw_007", "outcome": "fail_number"}
    )
    assert other[0]["showcase"]["cloned_from"] in {"tmpl-a", "tmpl-b"}, "any long-enough case, never the short one"
    assert {v["username"] for v in other} == {"flw_007"}


def test_a_dead_or_registration_less_case_is_never_a_template():
    dead = _template_case("tmpl-dead", "flw_001", 5)
    dead[3]["form_json"]["form"]["child_alive"] = "no"
    no_reg = _template_case("tmpl-noreg", "flw_001", 5)[1:]
    visits = _clone_build(dead + no_reg, {"name": "Steady Gain", "trajectory": "normal_02", "flw": "flw_001"})
    assert "cloned_from" not in visits[0]["showcase"], "falls back to synthesised forms"
    assert visits[0]["form_json"]["form"]["@name"] == "Child Registration Form"


def test_a_case_with_a_referral_or_danger_sign_is_never_a_template():
    """The template supplies everything the showcase does not say, so it must
    say nothing eventful: "Steady Gain" cloned from a referred infant read
    "Referrals 5" on the record rail."""
    referred = _template_case("tmpl-ref", "flw_001", 5)
    referred[2]["form_json"]["form"]["danger_signs_checklist"] = {
        "child_referred": "yes",
        "referral_status": "Referred",
    }
    danger = _template_case("tmpl-danger", "flw_001", 5)
    danger[4]["form_json"]["form"]["child_details"] = {"Danger_Signs_Checklist": {"jaundice_grp": {"jaundice": "yes"}}}
    clean = _template_case("tmpl-clean", "flw_009", 5)
    clean[1]["form_json"]["form"]["danger_signs_checklist"] = {
        "child_referred": "no",
        "referral_status": "",
        "conv_lbl": "OK",
        # procedural fields inside the event group answer yes on every visit
        "llo_consent": "yes",
        "equipment_image_capture_checklist": {"equipment_check": "yes", "live_equipment_capture_done_or_no": "Yes"},
    }
    visits = _clone_build(
        referred + danger + clean, {"name": "Steady Gain", "trajectory": "normal_02", "flw": "flw_001"}
    )
    assert visits[0]["showcase"]["cloned_from"] == "tmpl-clean", "the uneventful case wins even from another worker"


def test_a_young_cohort_still_clones_reusing_the_last_follow_up_form():
    """Opp 2166 was a month old when cloned: no case had four weighed follow-ups,
    so three of four demo cases fell back to synthesised forms. One weighed
    follow-up is enough; the last one is reused for the extra weighings."""
    short = _template_case("tmpl-short", "flw_001", 2)
    short[2]["form_json"]["form"]["anthropometric"]["muac"] = 12.25  # marks the LAST follow-up
    visits = _clone_build(short, {"name": "Steady Gain", "trajectory": "normal_02", "flw": "flw_001"})
    series = cm.trajectories(CORPUS)["normal_02"]
    weighings = _weighings(visits)
    assert visits[0]["showcase"]["cloned_from"] == "tmpl-short"
    assert len(weighings) == len(series) == 4
    assert [w["form_json"]["form"]["anthropometric"]["muac"] for w in weighings] == [11.5, 12.25, 12.25, 12.25]
    assert [_weight(w) for w in weighings] == [p["reading_grams"] for p in series]
    assert len({w["xform_id"] for w in weighings}) == 4
    # a full-length case is still preferred when one exists
    full = _template_case("tmpl-full", "flw_001", 5)
    both = _clone_build(short + full, {"name": "Steady Gain", "trajectory": "normal_02", "flw": "flw_001"})
    assert both[0]["showcase"]["cloned_from"] == "tmpl-full"


def test_without_templates_the_forms_are_synthesised_as_before():
    visits = _build([{"name": "Steady Gain", "trajectory": "normal_02", "flw": "flw_001"}])
    assert "cloned_from" not in visits[0]["showcase"]
    assert visits[0]["form_json"]["form"]["subcase_0"]["case"]["update"]["child_weight_birth"] == 1250.0
