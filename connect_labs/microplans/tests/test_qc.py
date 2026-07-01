"""Tests for the QC validation cascade (pure; no network/DB)."""

from __future__ import annotations

import pandas as pd

from connect_labs.microplans.qc.cascade import (
    apply_cascade,
    cascade_report_by,
    older_services_violations,
    validation_cascade,
)


def _households():
    # H1 clean reported→validated; H2 fails confidence; H3 fails phone;
    # H4 fails older-services; H5 did not report a visit (excluded from reported).
    return pd.DataFrame(
        [
            dict(
                household_id="H1",
                ward="Gwange",
                flw_visit="yes",
                confidence_in_vita_visit="other_campaign",
                worker_used_phone="yes",
                fail_older_services=False,
            ),
            dict(
                household_id="H2",
                ward="Gwange",
                flw_visit="yes",
                confidence_in_vita_visit="no_other_campaign",
                worker_used_phone="yes",
                fail_older_services=False,
            ),
            dict(
                household_id="H3",
                ward="Gwange",
                flw_visit="yes",
                confidence_in_vita_visit="other_campaign",
                worker_used_phone="no",
                fail_older_services=False,
            ),
            dict(
                household_id="H4",
                ward="Tsaki",
                flw_visit="yes",
                confidence_in_vita_visit="other_campaign",
                worker_used_phone="yes",
                fail_older_services=True,
            ),
            dict(
                household_id="H5",
                ward="Tsaki",
                flw_visit="no",
                confidence_in_vita_visit="",
                worker_used_phone="",
                fail_older_services=False,
            ),
        ]
    )


def test_validation_cascade_counts():
    res = validation_cascade(_households())
    assert res.n_reported == 4  # H1-H4 reported, H5 did not
    assert res.n_validated == 1  # only H1 survives all three filters
    assert res.per_rule == {"older_services": 1, "confidence": 1, "phone": 1}


def test_per_rule_flags_only_apply_to_reported():
    res = validation_cascade(_households())
    df = res.households.set_index("household_id")
    assert df.loc["H2", "fail_confidence"]
    assert df.loc["H3", "fail_phone"]
    assert df.loc["H4", "fail_older_services"]
    assert not df.loc["H5", "reported_visit"]
    assert not df.loc["H5", "visit_validated"]


def test_cascade_report_by_ward():
    res = validation_cascade(_households())
    rep = cascade_report_by(res, "ward").set_index("ward")
    # Gwange: 3 reported (H1-H3), 1 validated (H1)
    assert rep.loc["Gwange", "reported"] == 3
    assert rep.loc["Gwange", "validated"] == 1
    # Tsaki: H4 reported but fails older-services → 0 validated; H5 not reported
    assert rep.loc["Tsaki", "reported"] == 1
    assert rep.loc["Tsaki", "validated"] == 0


def test_older_services_violations_from_members():
    members = pd.DataFrame(
        [
            dict(household_id="H1", member_age=3, member_vita="yes"),  # under-5 vita: fine
            dict(household_id="H4", member_age=42, member_ors="yes"),  # adult got ORS: violation
            dict(household_id="H7", member_age=9, member_muac="yes"),  # 9yo MUAC: violation
        ]
    )
    bad = older_services_violations(members)
    assert bad == {"H4", "H7"}


def test_generic_engine_supports_flag_severity():
    from connect_labs.microplans.qc.cascade import FilterRule

    df = pd.DataFrame([dict(flw_visit="yes", x=1), dict(flw_visit="yes", x=0)])
    rules = [FilterRule("x_flag", "x is zero", lambda d: d["x"].eq(0), severity="flag")]
    res = apply_cascade(df, rules)
    # flag severity annotates but does not drop from validated
    assert res.n_validated == 2
    assert res.per_rule["x_flag"] == 1
