"""DDD 003 batch 7: the overview's order row says which round it came from and the bare
stage; a tender's last change is the round's own; the chain's evaluation reads tender by tender."""

from connect_labs.supply_chain.standing import standing_rows
from connect_labs.supply_chain.templatetags.supply_chain_extras import (
    deliver_stages,
    evaluation_value,
    evaluation_words,
    quotation_words,
)
from connect_labs.supply_chain.tests import test_tracking_reality as reality
from connect_labs.supply_chain.tests.test_tracking_reality import _held_on_our_form_m

da = reality.da
world = reality.world


def _rows():
    return standing_rows(reality.PROGRAM, reality.TODAY)


class TestTheOrderRow:
    def test_it_says_the_round_it_came_from_and_the_stage_only(self, da, world):
        contract, _ = _held_on_our_form_m(da, world)
        row = next(r for r in _rows() if r.contract_id == contract["id"])
        assert row.origin == "RUTF round"
        assert row.stage.endswith("at customs, held")
        assert "waiting on us" not in row.stage


class TestTheEvaluationCaption:
    """Finding #3/#19 (DDD unanswered-round 2026-10-03): the headline counts open rounds only,
    says what it counts, and lists awarded rounds apart."""

    def test_one_open_round_reads_as_its_own_comparison_page(self):
        evaluation = {"provisional": True, "by_tender": [{"label": "R", "comparable": 1, "of": 3, "awarded": False}]}
        assert evaluation_value(evaluation) == "1 of 3"
        assert evaluation_words(evaluation) == "quotes comparable on R"

    def test_an_awarded_round_is_listed_apart_not_added_in(self):
        evaluation = {
            "provisional": True,
            "by_tender": [
                {"label": "RUTF round 2", "comparable": 1, "of": 3, "awarded": False},
                {"label": "RUTF round 1", "comparable": 1, "of": 1, "awarded": True},
            ],
        }
        assert evaluation_value(evaluation) == "1 of 3"
        words = evaluation_words(evaluation)
        assert words == "quotes comparable on RUTF round 2 · awarded: RUTF round 1"
        assert "provisional" not in words

    def test_several_open_rounds_read_one_by_one(self):
        evaluation = {
            "by_tender": [
                {"label": "R3", "comparable": 0, "of": 2, "awarded": False},
                {"label": "R2", "comparable": 1, "of": 3, "awarded": False},
            ]
        }
        assert evaluation_value(evaluation) == "1 of 5"
        assert evaluation_words(evaluation) == "R3: 0 of 2 · R2: 1 of 3 quotes comparable"


class TestTheQuotationCaption:
    def test_live_quotes_break_down_by_tender(self):
        words = quotation_words({"by_tender": [{"label": "R2", "live": 3}, {"label": "R1", "live": 1}]})
        assert words == "live: 3 on R2 · 1 on R1"

    def test_one_tender_names_it(self):
        assert quotation_words({"by_tender": [{"label": "R2", "live": 3}]}) == "live on R2"


class TestTheChainLabels:
    def test_plain_labels_and_units(self):
        cells = deliver_stages(
            {
                "network": {"supply_points": 0, "user_held": 0, "never_reported": 0},
                "on_hand": None,
                "in_transit": None,
                "distributions": {"runs": 0},
                "consumed": None,
                "cover": {},
            }
        )
        labels = [c["label"] for c in cells]
        assert "Below reorder level" in labels and "Under its band" not in labels
        assert cells[0]["sub"].startswith("supply points")
