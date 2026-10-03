"""DDD 003 batch 7: the overview's order row says which round it came from and the bare
stage; a tender's last change is the round's own; the chain's evaluation reads tender by tender."""

from connect_labs.supply_chain.standing import standing_rows
from connect_labs.supply_chain.templatetags.supply_chain_extras import evaluation_words
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


class TestTheTenderRowsLastChange:
    def test_a_shipment_on_its_order_is_not_a_change_to_the_round(self, da, world):
        before = next(r for r in _rows() if r.tender_id == world["tender"]["id"] and r.kind == "tender")
        _held_on_our_form_m(da, world)
        after = next(r for r in _rows() if r.tender_id == world["tender"]["id"] and r.kind == "tender")
        assert after.last_change_at == before.last_change_at


class TestTheEvaluationCaption:
    def test_one_tender_reads_comparable(self):
        assert (
            evaluation_words(
                {"provisional": False, "by_tender": [{"label": "R", "comparable": 1, "of": 3, "awarded": False}]}
            )
            == "comparable"
        )

    def test_several_tenders_read_one_by_one(self):
        words = evaluation_words(
            {
                "provisional": False,
                "by_tender": [
                    {"label": "RUTF round 2", "comparable": 1, "of": 3, "awarded": False},
                    {"label": "RUTF round 1", "comparable": 1, "of": 1, "awarded": True},
                ],
            }
        )
        assert words == "RUTF round 2: 1 of 3 comparable · RUTF round 1: awarded"
