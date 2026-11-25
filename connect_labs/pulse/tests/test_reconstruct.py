"""The rules that turn the accounting ledger into reconstructed invoices (`pulse.reconstruct`). All data invented."""

import datetime as dt
from decimal import Decimal

from connect_labs.pulse import reconstruct as rc

D = Decimal


def _line(key, usd, cls="llo_fixed", org="solina", programme="GiveWell CHC", day=1):
    return rc.Line(key=key, usd=D(str(usd)), line_class=cls, org_slug=org, programme=programme,
                   date=dt.date(2025, 3, day))  # fmt: skip


def _opp(oid, org="solina", program=24, active=False, recorded=0, reached=0):
    return rc.Opp(
        id=oid, org_slug=org, program_id=program, active=active, recorded_usd=D(str(recorded)), reached=reached
    )


PROGRAMMES = {"GiveWell CHC": [24, 82], "Founders Pledge CHC": [59]}


def _by(result):
    return {(i.opportunity_id, i.kind): i for i in result.invoices}


class TestPairGap:
    def test_what_connect_missed_is_split_by_people_reached(self):
        lines = [_line("a", 6000, "llo_service"), _line("b", 4000, "llo_fixed", day=20)]
        opps = [_opp(1, recorded=3000, reached=300), _opp(2, program=82, recorded=3000, reached=100)]
        result = rc.build(lines, PROGRAMMES, opps)
        inv = _by(result)
        # Ledger paid 10,000; Connect recorded 6,000; 4,000 missed, split 3:1.
        assert inv[(1, rc.KIND_LLO)].amount_usd == D("3000.00")
        assert inv[(2, rc.KIND_LLO)].amount_usd == D("1000.00")
        assert (inv[(1, rc.KIND_LLO)].start_date, inv[(1, rc.KIND_LLO)].end_date) == (
            dt.date(2025, 3, 1),
            dt.date(2025, 3, 20),
        )
        # Each invoice says which lines it is made of, in proportion.
        assert inv[(1, rc.KIND_LLO)].sources == {"a": D("1800.0000"), "b": D("1200.0000")}
        assert result.pairs[0]["status"] == "invoiced" and result.pairs[0]["gap_usd"] == D("4000.00")

    def test_a_running_opportunity_holds_the_whole_pair(self):
        result = rc.build(
            [_line("a", 9000)], PROGRAMMES, [_opp(1, recorded=1000), _opp(2, active=True, recorded=1000)]
        )
        assert result.invoices == []
        assert result.pairs[0]["status"].startswith("held")

    def test_connect_recording_more_than_the_ledger_is_reported_not_invoiced(self):
        result = rc.build([_line("a", 1000)], PROGRAMMES, [_opp(1, recorded=1500)])
        assert result.invoices == []
        assert result.pairs[0]["status"] == "Connect recorded more than the ledger paid"

    def test_nobody_reached_splits_by_recorded_cost(self):
        result = rc.build([_line("a", 4000)], PROGRAMMES, [_opp(1, recorded=1500), _opp(2, recorded=500)])
        inv = _by(result)
        assert (inv[(1, rc.KIND_LLO)].amount_usd, inv[(2, rc.KIND_LLO)].amount_usd) == (D("1500.00"), D("500.00"))
        assert inv[(1, rc.KIND_LLO)].basis["parts"][0]["split_by"] == "recorded cost"


class TestDirectCosts:
    def test_split_over_the_programme_and_only_closed_opportunities_are_invoiced(self):
        lines = [_line("c", 1000, rc.DIRECT, org="")]
        opps = [_opp(1, reached=300), _opp(2, org="janna", program=82, reached=100, active=True)]
        result = rc.build(lines, PROGRAMMES, opps)
        assert [(i.opportunity_id, i.kind, i.amount_usd) for i in result.invoices] == [
            (1, rc.KIND_DIRECT, D("750.00"))
        ]
        assert result.programmes[0]["held_for_active_usd"] == D("250")


class TestUnattributed:
    def test_what_cannot_be_placed_is_listed_never_spread(self):
        lines = [
            _line("no-org", 10, org=""),
            _line("no-prog", 20, programme=""),
            _line("unmapped", 30, programme="Something Else"),
            _line("no-opp", 40, org="janna"),
            _line("staff", 50, "excluded"),
        ]
        result = rc.build(lines, PROGRAMMES, [_opp(1)])
        assert {u["line_key"]: u["why"] for u in result.unattributed} == {
            "no-org": rc.WHY_NO_ORG,
            "no-prog": rc.WHY_NO_PROGRAMME,
            "unmapped": rc.WHY_PROGRAMME_UNMAPPED,
            "no-opp": rc.WHY_NO_OPPORTUNITY,
        }
        assert result.invoices == []


def test_invoice_numbers_are_stable_and_never_look_like_connects():
    number = rc.invoice_number(603, rc.KIND_LLO)
    assert number == rc.invoice_number(603, rc.KIND_LLO) and number.startswith("LABS-")
    assert number != rc.invoice_number(603, rc.KIND_DIRECT)
