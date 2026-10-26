"""Every KMC danger sign is read under each spelling an app generation used.

Older builds capitalise the question (``Floppy_or_lethargic``) and some its group
(``form.Danger_Signs_Checklist``); a field that reads one spelling misses a whole
generation's answers. Paths here are the app's question names, not data.
"""

from connect_labs.workflow.templates.kmc_programme_metrics import DANGER_SIGN_FIELDS


def _paths(name):
    return next(f["paths"] for f in DANGER_SIGN_FIELDS if f["name"] == name)


def test_lethargy_is_read_in_both_cases_under_every_group():
    paths = _paths("ds_lethargic")
    for spelling in (
        "form.danger_signs_checklist.Floppy_or_lethargic",
        "form.danger_signs_checklist.floppy_or_lethargic",
        "form.child_details.Danger_Signs_Checklist.Floppy_or_lethargic",
        "form.Danger_Signs_Checklist.Floppy_or_lethargic",
        "form.danger_signs_checklist.lethargic_grp.floppy_or_lethargic",
    ):
        assert spelling in paths


def test_convulsions_is_read_lower_case_too():
    assert "form.danger_signs_checklist.convulsions_or_seizures" in _paths("ds_convulsions")
    assert "form.danger_signs_checklist.Convulsions_or_seizures" in _paths("ds_convulsions")


def test_the_newest_spelling_still_comes_first_and_none_repeats():
    for f in DANGER_SIGN_FIELDS:
        assert len(f["paths"]) == len(set(f["paths"]))
    assert _paths("ds_lethargic")[0] == "form.danger_signs_checklist.lethargic_grp.floppy_or_lethargic"
