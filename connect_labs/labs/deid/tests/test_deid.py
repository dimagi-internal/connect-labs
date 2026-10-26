"""The shared de-identification rules. Every name, phone and place here is invented."""

from collections import Counter

import pytest

from connect_labs.labs.deid import classify, fieldscan, leaks, transforms

KEY = b"k" * 32


# --- fieldscan --------------------------------------------------------------


def test_walk_yields_leaves_with_repeat_groups_and_skips_meta():
    form = {
        "form": {
            "@xmlns": "x",
            "meta": {"username": "worker_a", "location": "1 2"},
            "child": {"weight": "2500"},
            "visits": [{"muac": "11"}, {"muac": "12"}],
        }
    }
    leaves = list(fieldscan.walk(form))
    assert (("form", "child", "weight"), "2500") in leaves
    assert (("form", "visits", "[]", "muac"), "11") in leaves
    assert not any("meta" in p or "@xmlns" in p for p, _ in leaves)


def test_values_at_reads_through_repeat_groups():
    form = {"form": {"visits": [{"muac": "11"}, {"muac": "12"}, {}]}}
    assert fieldscan.values_at(form, fieldscan.split("form.visits.[].muac")) == ["11", "12"]
    assert fieldscan.values_at(form, ("form", "missing")) == []


def test_normalise_merges_case_and_spacing_not_renames():
    assert fieldscan.normalise("form.Danger.Lethargy") == fieldscan.normalise("form.danger.lethargy ")
    assert fieldscan.normalise("form.ds_lethargy") != fieldscan.normalise("form.danger_lethargy")


def test_app_build_reads_the_build_number():
    form = {"form": {"meta": {"appVersion": 'CommCare Android, version "2.53.1"(4600). App Version: 123. CC: 12'}}}
    assert fieldscan.app_build(form) == 123
    assert fieldscan.app_build({"form": {}}) is None


# --- classify ---------------------------------------------------------------


@pytest.mark.parametrize(
    "path, values, kind",
    [
        ("form.mother_name", ["Ada Obi", "Bola Eze"], classify.IDENTIFIER),
        ("form.contact.phone_number", ["0801234567"], classify.IDENTIFIER),
        ("form.case.@case_id", ["c1"], classify.CASE_ID),
        ("form.child_case_id", ["c1"], classify.CASE_ID),
        ("form.gps", ["9.08 7.49 0 10"], classify.GPS),
        ("form.home_point", ["9.081 7.492 0 10", "9.07 7.40 0 5"], classify.GPS),
        ("form.danger_sign_other", ["feverish all night"], classify.FREE_TEXT),
        ("form.photo_child", ["1700000000.jpg"], classify.MEDIA),
        ("form.record_id", ["R-1"], classify.ID),
        ("form.reg_date", ["2026-01-02", "2026-02-03"], classify.DATE),
        ("form.place_of_delivery", ["home"] * 6 + ["hospital"] * 6, classify.CATEGORICAL),
        ("form.weight_g", [str(2000 + i) for i in range(40)], classify.NUMBER),
        ("form.nothing", [], classify.EMPTY),
    ],
)
def test_classify(path, values, kind):
    assert classify.classify(path, Counter(values)) == kind


def test_identifier_tokens_match_whole_segments_only():
    assert not classify.is_identifier_path("form.kmc_positioning_checklist")
    assert not classify.is_identifier_path("form.valid")
    assert classify.is_identifier_path("form.mother_name")


def test_a_long_digit_run_is_an_identifier_whatever_it_is_called():
    assert classify.classify("form.number_given", Counter(["08012345678", "08087654321"])) == classify.IDENTIFIER


def test_many_distinct_words_are_free_text():
    values = Counter({f"word{i} and more" for i in range(80)})
    assert classify.classify("form.answer", values) == classify.FREE_TEXT


def test_defaults_drop_what_identifies_and_cut_birth_dates_to_the_month():
    assert classify.default_setting("form.mother_name", classify.IDENTIFIER)[0] == "drop"
    assert classify.default_setting("form.case.@case_id", classify.CASE_ID) == ("pseudonymise", {"namespace": "case"})
    assert classify.default_setting("form.child_dob", classify.DATE)[0] == "month"
    assert classify.default_setting("form.visit_date", classify.DATE)[0] == "keep"


# --- transforms -------------------------------------------------------------


def test_pseudonym_is_stable_keyed_and_letters_only():
    a = transforms.pseudonym("case-1", KEY, "case")
    assert a == transforms.pseudonym("case-1", KEY, "case")
    assert a != transforms.pseudonym("case-1", b"other" * 7, "case")
    assert a != transforms.pseudonym("case-1", KEY, "worker")
    assert a.isalpha() and len(a) == 12


def test_round_month_shift_coarsen():
    assert transforms.round_to("2512", 25) == "2500"
    assert transforms.round_to("2513", 25) == "2525"
    assert transforms.round_to("x", 25) is transforms.DROP
    assert transforms.round_to("NaN", 25) is transforms.DROP
    assert transforms.round_to("Infinity", 25) is transforms.DROP
    assert transforms.month("2026-03-14") == "2026-03"
    assert transforms.shift("2026-03-14", -3) == "2026-03-11"
    assert transforms.coarsen("9.08123 7.49456 300 4", 2) == "9.08 7.49"


def test_shift_is_the_same_for_every_date_of_one_case():
    one = transforms.apply("shift", {"max_days": 30}, "2026-03-14", key=KEY, case="c1")
    two = transforms.apply("shift", {"max_days": 30}, "2026-03-20", key=KEY, case="c1")
    from datetime import date

    assert (date.fromisoformat(two) - date.fromisoformat(one)).days == 6
    assert transforms.apply("shift", {}, "2026-03-14", key=KEY, case=None) is transforms.DROP


def test_scrub_removes_numbers_emails_and_known_words():
    out = transforms.scrub("Call Ada on 0801 234 5678 or ada@example.org", frozenset({"ada"}))
    assert "0801" not in out and "example" not in out and "Ada" not in out


def test_apply_drops_unknown_settings_and_validate_params_checks():
    assert transforms.apply("bogus", {}, "1", key=KEY) is transforms.DROP
    assert transforms.apply("drop", {}, "1", key=KEY) is transforms.DROP
    for bad in ({"step": 0}, {"step": "nan"}, {"step": "inf"}):
        with pytest.raises(ValueError):
            transforms.validate_params("round", bad)
    with pytest.raises(ValueError):
        transforms.validate_params("shift", {"max_days": None})
    assert transforms.validate_params("coarsen", {"decimals": 2}) == {"decimals": 2}
    with pytest.raises(ValueError):
        transforms.validate_params("coarsen", {"decimals": 3})  # ~100 m: a home, not an area
    assert transforms.round_to("12345678901234567890123456789012", 25) is transforms.DROP
    assert transforms.round_to("1e999999999", 25) is transforms.DROP


def test_stricter():
    assert transforms.stricter("keep", "drop") == "drop"
    assert transforms.stricter("round", "pseudonymise") == "pseudonymise"


# --- leaks ------------------------------------------------------------------


def test_leak_scanner_finds_known_names_and_shapes():
    scan = leaks.LeakScanner()
    scan.learn("Ada Obi")
    scan.learn("worker_ada")
    scan.learn("baby")  # a stopword is not a name
    assert "known_name" in scan.scan("seen by obi today")
    assert "known_value" in scan.scan("worker_ada")
    assert scan.scan("baby is well") == set()
    assert "phone" in scan.scan("08012345678")
    assert "email" in scan.scan("x@y.org")
    assert "gps" in scan.scan("9.0812 7.4945")
    assert "case_id" in scan.scan("1f0e2d3c-4b5a-6978-8a9b-0c1d2e3f4a5b")


def test_a_pseudonym_never_trips_the_scan():
    scan = leaks.LeakScanner()
    assert scan.scan(transforms.pseudonym("08012345678", KEY, "x")) == set()


def test_identity_terms_and_leaks():
    terms = leaks.identity_terms([("Ada Obi", "ada_o")], "")
    assert leaks.identity_leaks("ask ada_o", terms) == ["ada_o"]


def test_a_possessive_or_hyphenated_name_is_still_found_and_scrubbed():
    scan = leaks.LeakScanner()
    scan.learn("Amina Wanjiru")
    assert "known_name" in scan.scan("Visited Amina's house")
    assert "known_name" in scan.scan("Amina-Wanjiru came")
    assert "Amina" not in transforms.scrub("Visited Amina's house", frozenset(scan.words))
    assert "Wanjiru" not in transforms.scrub("Amina-Wanjiru came", frozenset(scan.words))
