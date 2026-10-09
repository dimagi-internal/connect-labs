"""Case coaching: the story a case's visits tell, the facts behind it, the case briefing
(a byte-exact contract with the coach bot), and the per-worker finder view.

The cases are the synthetic KMC demo cases of opportunity 10042, as their visits read
on 2026-10-09 (``explorer_query``)."""

import datetime as dt

from connect_labs.workflow import case_coaching as cc
from connect_labs.workflow import case_finder

CONFIG = cc.KMC_CASE_COACHING


def visit(
    day,
    *,
    weight=None,
    reg_weight=None,
    form="Record Visit Details",
    hours=None,
    q=None,
    labels=None,
    referred=None,
    checklist=True,
    case="c1",
    name="Baby",
    user="flw_001",
    opp=10042,
    bw=None,
):
    return {
        "opportunity_id": opp,
        "visit_id": f"{case}-{day}-{form[:3]}",
        "visit_date": day,
        "username": user,
        "entity_name": name,
        "status": "approved",
        "form_name": form,
        "case_id": case,
        "weight": weight,
        "registration_weight": reg_weight,
        "birth_weight": bw,
        "skin_to_skin": hours,
        "referred": referred,
        "has_checklist": checklist,
        "danger_questions": q or {},
        "danger_labels": labels or {},
    }


REG = "Child Registration Form"


def steady_gain():
    """KMC Demo -- Steady Gain: 1,350 -> 2,285 g over three weeks."""
    return [
        visit(
            "2026-05-17", form=REG, reg_weight=1350, weight=None, bw=1250, name="KMC Demo — Steady Gain", referred=None
        ),
        visit("2026-05-18", weight=1350, reg_weight=1350, bw=1250, name="KMC Demo — Steady Gain", referred="no"),
        visit(
            "2026-05-25", weight=1635, reg_weight=1350, hours=20, bw=1250, name="KMC Demo — Steady Gain", referred="no"
        ),
        visit(
            "2026-06-01", weight=1915, reg_weight=1350, hours=40, bw=1250, name="KMC Demo — Steady Gain", referred="no"
        ),
        visit(
            "2026-06-08", weight=2285, reg_weight=1350, hours=40, bw=1250, name="KMC Demo — Steady Gain", referred="no"
        ),
    ]


def transcription_error():
    n = "KMC Demo — Transcription Error"
    kw = dict(case="c2", name=n, user="flw_007", bw=1315)
    return [
        visit("2026-05-17", form=REG, reg_weight=1415, **kw),
        visit("2026-05-18", weight=1910.25, reg_weight=1415, referred="no", **kw),
        visit("2026-05-25", weight=2504.25, reg_weight=1415, hours=22, referred="no", **kw),
        visit("2026-06-01", weight=2848.5, reg_weight=1415, hours=39, referred="no", **kw),
        visit("2026-06-08", weight=3192.75, reg_weight=1415, hours=39, referred="no", **kw),
    ]


def faltering(hours=(16, 8)):
    kw = dict(case="c3", name="Beneficiary 694", user="flw_005")
    return [
        visit("2026-08-19", form=REG, reg_weight=2000, **kw),
        visit("2026-08-26", weight=2150, referred="no", **kw),
        visit("2026-09-02", weight=2200, hours=hours[0], referred="no", **kw),
        visit("2026-09-09", weight=2200, hours=hours[1], referred="no", **kw),
    ]


def danger():
    kw = dict(case="c4", name="Beneficiary 538", user="flw_018")
    return [
        visit("2026-08-20", form=REG, reg_weight=1500, referred="yes", **kw),
        visit("2026-08-27", weight=1700, referred="no", **kw),
        visit(
            "2026-09-08",
            weight=1900,
            referred="no",
            q={"noisy_breathing": "yes", "jaundice": "no"},
            labels={"fast_breathing": "OK", "jaundice": "OK"},  # jaundice answered no: ignored
            **kw,
        ),
    ]


def one(rows):
    [case] = cc.cases_from_rows(rows, CONFIG)
    return case


# ---------------------------------------------------------------------------
# Stories
# ---------------------------------------------------------------------------


def test_steady_gain_is_thriving():
    [s] = cc.classify(one(steady_gain()))
    assert s.key == cc.THRIVING
    assert s.facts == (
        "Weight rose from 1,350 g on 17 May to 2,285 g on 8 Jun (+69%), about 27 g/kg/day on average; "
        "healthy growth is 15–20 g/kg/day."
    )


def test_the_registration_weight_copied_onto_follow_ups_is_not_their_weight():
    """Follow-up forms repeat child_weight_reg; read as each visit's weight it made 371
    of 606 BERI babies look as if they had lost 10%."""
    case = one(steady_gain())
    assert [w.grams for w in cc.weighings(case)] == [1350, 1350, 1635, 1915, 2285]


def test_a_495_g_jump_in_a_day_is_a_weight_check_with_the_programmes_own_rule():
    [s] = cc.classify(one(transcription_error()))
    assert s.key == cc.WEIGHT_CHECK
    # Per kg of the pair's MEAN weight per day, outside -20..45 (KMC registry,
    # pct_impossible_weight_changes).
    assert s.facts == (
        "Weight rose 495 g in 1 day between 17 and 18 May, about 298 g/kg/day; healthy growth is "
        "15–20 g/kg/day, and the programme counts more than 45 as impossible."
    )
    assert s.detail["label"] == "+495 g in 1 day?"


def test_the_registrys_own_thresholds_are_used_when_given():
    rows = transcription_error()
    lenient = cc.cases_from_rows(rows, {**CONFIG, "constants": {"IMPOSSIBLE_HI": 400}})[0]
    assert [s.key for s in cc.classify(lenient)] != [cc.WEIGHT_CHECK]


def test_a_weight_check_is_the_only_story_of_a_series_with_one():
    rows = danger()
    rows[1]["weight"] = 250  # not a baby's weight
    assert [s.key for s in cc.classify(one(rows))] == [cc.WEIGHT_CHECK]


def test_the_same_weight_three_times_running_is_a_weight_check():
    rows = steady_gain()
    rows[2]["weight"] = rows[3]["weight"] = 1350
    rows[4]["weight"] = 1400
    s = cc.classify(one(rows))[0]
    assert s.key == cc.WEIGHT_CHECK and "exactly 1,350 g on 4 weighings in a row" in s.facts


def test_stalled_weight_and_falling_skin_to_skin_is_faltering():
    [s] = cc.classify(one(faltering()))
    assert s.key == cc.FALTERING
    assert "Skin-to-skin fell from 16 h on 2 Sep to 8 h on 9 Sep." in s.facts


def test_stalled_weight_with_rising_skin_to_skin_is_not_faltering():
    """The 10042 'Faltering Growth' demo case: its skin-to-skin ROSE (20 then 40 h)."""
    assert [s.key for s in cc.classify(one(faltering(hours=(8, 16))))] == []


def test_a_danger_sign_not_referred_outranks_growth():
    stories = cc.classify(one(danger()))
    assert stories[0].key == cc.DANGER
    assert stories[0].facts == (
        "On 8 Sep 2026 the visit recorded fast breathing and noisy breathing, and the baby was not referred."
    )


def test_a_referred_danger_sign_is_no_story():
    rows = danger()
    rows[2]["referred"] = "yes"
    assert cc.DANGER not in [s.key for s in cc.classify(one(rows))]


def test_a_story_asked_for_must_be_one_the_visits_support():
    case = one(steady_gain())
    assert cc.story_of(case, cc.THRIVING).key == cc.THRIVING
    assert cc.story_of(case, cc.DANGER) is None


# ---------------------------------------------------------------------------
# The briefing: a contract with the coach bot (ACE lib/coach-briefing.ts renderCaseBriefing)
# ---------------------------------------------------------------------------

STEADY_GAIN_BRIEFING = """BRIEFING (system text — do not show to the worker)
Programme: Kangaroo Mother Care
Worker: Asha Banda
Case: KMC Demo — Steady Gain
About this case: Birth weight 1,250 g; registered 17 May 2026; 5 visits, the last on 8 Jun 2026.
Topic: Baby is growing well [CASE_THRIVING]
What the data shows: {facts}
Visits, oldest first:
- 17 May 2026: weight 1,350 g; skin-to-skin not recorded; danger signs: none; referred: not asked
- 18 May 2026: weight 1,350 g; skin-to-skin not recorded; danger signs: none; referred: no
- 25 May 2026: weight 1,635 g; skin-to-skin 20 h in the last 24 h; danger signs: none; referred: no
- 1 Jun 2026: weight 1,915 g; skin-to-skin 40 h in the last 24 h; danger signs: none; referred: no
- 8 Jun 2026: weight 2,285 g; skin-to-skin 40 h in the last 24 h; danger signs: none; referred: no
Follow your conversation steps from the opening.""".format(
    facts=(
        "Weight rose from 1,350 g on 17 May to 2,285 g on 8 Jun (+69%), about 27 g/kg/day on average; "
        "healthy growth is 15–20 g/kg/day."
    )
)


def test_the_case_briefing_is_byte_exact():
    case = one(steady_gain())
    text = cc.render_case_briefing(
        programme="Kangaroo Mother Care", worker="Asha Banda", case=case, story=cc.story_of(case)
    )
    assert text == STEADY_GAIN_BRIEFING


def test_a_follow_up_adds_the_earlier_coaching_line_after_the_facts():
    case = one(steady_gain())
    text = cc.render_case_briefing(
        programme="Kangaroo Mother Care",
        worker="Asha Banda",
        case=case,
        story=cc.story_of(case),
        earlier={"date": "2026-06-01", "label": "CASE_FALTERING", "agreed": "visit again on Friday"},
    )
    lines = text.splitlines()
    i = lines.index(next(ln for ln in lines if ln.startswith("What the data shows:")))
    assert lines[i + 1] == (
        "Earlier coaching on this case: 1 Jun 2026 — Weight has stalled and skin-to-skin time is falling; "
        "agreed: visit again on Friday"
    )
    assert lines[i + 2] == "Visits, oldest first:"
    no_step = cc.earlier_line({"date": "2026-06-01", "label": "A chat"})
    assert no_step == "Earlier coaching on this case: 1 Jun 2026 — A chat; agreed: none"


def test_missing_values_are_written_not_recorded_and_signs_are_listed():
    rows = danger()
    rows[2]["has_checklist"] = True
    case = one(rows)
    lines = cc.render_case_briefing(
        programme="Kangaroo Mother Care", worker="flw_018", case=case, story=cc.story_of(case)
    ).splitlines()
    assert lines[-2] == (
        "- 8 Sep 2026: weight 1,900 g; skin-to-skin not recorded; danger signs: fast breathing, noisy breathing; "
        "referred: no"
    )
    assert (
        lines[4]
        == "About this case: Birth weight not recorded; registered 20 Aug 2026; 3 visits, the last on 8 Sep 2026."
    )


def test_the_opening_greets_a_username_with_a_plain_hello():
    from connect_labs.workflow import coach_briefing

    case = one(steady_gain())
    text = cc.render_case_briefing(programme="KMC", worker="flw_001", case=case, story=cc.story_of(case))
    assert coach_briefing.is_briefing(text) and cc.is_case_briefing(text)
    assert coach_briefing.opening_message(text).startswith("Hello! This is a short")
    assert cc.case_briefing_summary(text)["story"] == cc.THRIVING


# ---------------------------------------------------------------------------
# The finder
# ---------------------------------------------------------------------------


def test_the_finder_lists_each_workers_recent_stories_one_per_case():
    cases = cc.cases_from_rows(steady_gain() + transcription_error() + faltering() + danger(), CONFIG)
    latest = {10042: dt.date(2026, 9, 10)}
    view = case_finder.worker_view(cases, latest_by_opp=latest, names={"10042::flw_018": "Femi"}, window_days=30)
    # Steady Gain and Transcription Error ended in June: outside 30 days of 10 Sep.
    assert view["counts"] == {cc.DANGER: 1, cc.WEIGHT_CHECK: 0, cc.FALTERING: 1, cc.THRIVING: 0}
    assert [w["key"] for w in view["workers"]] == ["10042::flw_018", "10042::flw_005"]
    first = view["workers"][0]
    assert first["name"] == "Femi"
    [best] = first["eligible"][cc.DANGER]["best"]
    assert (best["case_name"], best["evidence_date"], best["days_before_latest"]) == (
        "Beneficiary 538",
        "2026-09-08",
        2,
    )
    assert "planning" in view and set(view["stories"]) == set(cc.STORIES)

    wide = case_finder.worker_view(cases, latest_by_opp=latest, window_days=120, guide=False)
    assert wide["counts"][cc.THRIVING] == 1 and wide["counts"][cc.WEIGHT_CHECK] == 1
    assert "planning" not in wide
