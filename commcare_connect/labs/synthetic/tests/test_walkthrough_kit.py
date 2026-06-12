"""Tests for walkthrough_kit primitives — title grammar + visit-id namespace.

These guard the round-2 generation-realism fixes: human task titles (the
dev-style "[Reason] flw" bracket grammar is banned) and a single visit-id
grammar shared by seeded and live-recorded audits.
"""

from unittest.mock import MagicMock

from commcare_connect.labs.synthetic.walkthrough_kit import (
    LIVE_VISIT_ID_FLOOR,
    VisitIdSequence,
    apply_action_spec,
    compose_task_title,
    live_visit_id_base,
)


def test_compose_task_title_human_grammar():
    title = compose_task_title(flw_id="isha_n", reason="Gender split off threshold")
    assert title == "Coach isha_n — gender split off threshold"


def test_compose_task_title_preserves_acronym_casing():
    title = compose_task_title(flw_id="hawa_n", reason="Bad MUAC distribution")
    assert title == "Coach hawa_n — bad MUAC distribution"


def test_no_reason_label_starts_a_bracket_title():
    """Ban the dev-style "[Reason] flw" grammar for every reason label the
    PAR demo vocabulary can produce."""
    from commcare_connect.labs.synthetic.program_admin_demo import REASON_LABELS

    for label in list(REASON_LABELS.values()) + [None, "Repeat failure on prior coaching"]:
        title = compose_task_title(flw_id="x_n", reason=label)
        assert not title.startswith("["), f"bracket title leaked: {title!r}"
        assert title.startswith("Coach x_n — ")


def test_apply_action_spec_writes_human_title_and_reason_matched_transcript():
    tda = MagicMock()
    tda.labs_api.create_record.return_value = MagicMock(id=1)
    ada = MagicMock()

    apply_action_spec(
        tda=tda,
        ada=ada,
        spec={
            "reason_key": "gender_skew",
            "reason_label": "Gender split off threshold",
            "audit_archetype": None,
            "task_archetype": "closed_satisfactory",
        },
        workflow_run_id=5,
        opportunity_id=10000,
        opportunity_name="Northern Cluster",
        flw_id="isha_n",
        monday_iso="2026-05-04",
        creator_name="amani_nm",
        visit_id_seq=VisitIdSequence(),
    )

    ada.labs_api.create_record.assert_not_called()
    data = tda.labs_api.create_record.call_args.kwargs["data"]
    assert data["title"] == "Coach isha_n — gender split off threshold"
    transcript = " ".join(m["text"].lower() for m in data["ocs_conversation"])
    assert "boys" in transcript or "girls" in transcript
    assert "framing" not in transcript


def test_live_visit_id_base_shares_the_seeded_grammar():
    """Live manager-flow audits must mint visit ids in the same 8-digit
    9X XXX XXX shape as the seeded VisitIdSequence — the bulk-assessment
    page renders the raw id per photo card, and two different id grammars
    on the same UI was an iter3 judge finding."""
    base = live_visit_id_base(1_750_000_000)
    assert LIVE_VISIT_ID_FLOOR <= base < 9_900_000

    seeded_base = VisitIdSequence().next()
    # Per-photo visit ids are base*10 + photo_index — same digit count for both.
    assert len(str(base * 10 + 4)) == len(str(seeded_base * 10 + 4)) == 8
    # Disjoint sub-ranges: a live base can never collide with a seeded one.
    assert base > seeded_base


def test_live_visit_id_base_is_time_derived_and_unique_across_reruns():
    assert live_visit_id_base(1_750_000_000) != live_visit_id_base(1_750_000_001)
