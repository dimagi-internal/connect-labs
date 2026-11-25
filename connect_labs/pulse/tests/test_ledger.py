"""The costs ledger sheet and its import (`pulse.ledger`): parsing, the import end to end with the sheet
faked, the cost figures, the tools and the page. All data invented."""

import datetime as dt
from decimal import Decimal
from unittest import mock

import pytest
from django.urls import reverse

from connect_labs.mcp.tool_registry import MCPToolError
from connect_labs.mcp.tools import costs_ledger as tools
from connect_labs.pulse import costs, ledger
from connect_labs.pulse.models import PulseLedgerLine, PulseOpportunity, PulseReconstructedInvoice, PulseScalar
from connect_labs.pulse.tests.test_costs import _opp, _work

D = Decimal

LEDGER = [
    ledger.LEDGER_HEADERS,
    ["b1", "intacct", "2025-03-01", "INC", "Solina Health", "SHL/1", "setup", "GSO - CC Connect", "INC-3579",
     "501003", "6,000.00", "llo_fixed", "org-1", "GiveWell CHC", "name"],
    ["b2", "intacct", "2025-03-20", "INC", "Solina Health", "SHL/2", "milestone", "GSO - CC Connect", "INC-3579",
     "501003", "4000", "llo_fixed", "org-1", "GiveWell CHC", "name"],
    ["c1", "intacct", "2025-02-01", "INC", "EHA Pharma", "P1", "ORS co-packs", "GSO - CC Connect", "INC-3579",
     "501004", "1000", "direct_cost", "", "GiveWell CHC", "supplier"],
    ["s1", "intacct", "2025-02-01", "INC", "Cigna", "X", "insurance", "GSO - CC Connect", "", "601", "500",
     "excluded", "", "", "staff"],
    ["bad", "intacct", "2025-02-01", "INC", "?", "", "", "", "", "", "n/a", "llo_fixed", "", "", ""],
]  # fmt: skip
PROGRAMMES = [ledger.PROGRAMMES_HEADERS, ["GiveWell CHC", "24, 82", "batches 1-4"]]
EXCLUSIONS = [ledger.EXCLUSIONS_HEADERS]


def _tabs(exclusions=EXCLUSIONS):
    tabs = {ledger.LEDGER_TAB: LEDGER, ledger.PROGRAMMES_TAB: PROGRAMMES, ledger.EXCLUSIONS_TAB: exclusions}
    return lambda sid, tab: tabs[tab]


@pytest.fixture(autouse=True)
def _fresh():
    costs.invalidate()
    yield
    costs.invalidate()


def test_parse_reads_every_row_it_can_and_names_the_rest():
    parsed = ledger.parse(LEDGER, PROGRAMMES, [ledger.EXCLUSIONS_HEADERS, ["603", "llo_payment", "pilot"]])
    assert [r["line_key"] for r in parsed.lines] == ["b1", "b2", "c1", "s1"]
    assert parsed.lines[0]["usd"] == D("6000.00") and parsed.lines[0]["date"] == dt.date(2025, 3, 1)
    assert parsed.programmes == {"GiveWell CHC": [24, 82]}
    assert parsed.exclusions == {(603, "llo_payment"): "pilot"}
    assert parsed.problems == ["Ledger row 6: usd 'n/a' is not a number"]


@pytest.mark.django_db
class TestImport:
    def _world(self):
        _opp(1, program_id=24)
        _opp(2, program_id=82)
        PulseOpportunity.objects.filter(opportunity_id=2).update(org_slug="org-1")  # both opportunities are org-1's
        _work(1, 2000, org=1000, units=3)  # Connect recorded 3,000 + 3,000 = 6,000 of the 10,000 paid
        _work(2, 2000, org=1000, units=3)
        PulseScalar.objects.create(key=ledger.SHEET_KEY, value={"id": "sheet-1"})

    def _run(self, **kw):
        reached = {1: {"reached": 300}, 2: {"reached": 100}}
        with mock.patch.object(ledger.directory, "read_tab", _tabs(kw.pop("exclusions", EXCLUSIONS))), \
             mock.patch.object(ledger.directory, "write_tab") as write:  # fmt: skip
            return ledger.run_import(people_reached=lambda ids: reached, **kw), write

    def test_builds_the_invoices_counts_them_and_writes_labs_tabs_back(self):
        self._world()
        got, write = self._run()
        assert got["ledger_lines"] == 4 and got["problems"] == ["Ledger row 6: usd 'n/a' is not a number"]
        rows = {(r.opportunity_id, r.kind): r for r in PulseReconstructedInvoice.objects.all()}
        assert rows[(1, "llo_payment")].amount_usd == D("3000.00")
        assert rows[(2, "llo_payment")].amount_usd == D("1000.00")
        assert rows[(1, "direct_cost")].amount_usd == D("750.00")
        assert rows[(1, "llo_payment")].sources.count() == 2
        # They count in every cost figure, and recorded_usd is still what Connect had.
        c = costs.opportunity_costs([1])[1]
        assert c.reconstructed_usd == D("3750.00") and c.recorded_usd == D("3000.00")
        assert {call.args[1] for call in write.call_args_list} == set(ledger.LABS_TABS)
        # The comparison: Connect recorded 6,000 across both; 4,000 missed + 1,000 bought directly.
        assert (got["recorded_usd"], got["implied_usd"]) == (6000.0, 11000.0)
        opps_tab = next(c.args[2] for c in write.call_args_list if c.args[1] == "Labs Opportunities")
        row1 = next(r for r in opps_tab[1:] if r[0] == 1)
        assert row1[4:9] == ["3000.00", "3000.00", "750.00", "6750.00", "3750.00"]

    def test_reimport_is_stable_and_an_exclusion_stops_one_counting(self):
        self._world()
        self._run()
        first = set(PulseReconstructedInvoice.objects.values_list("invoice_number", "amount_usd"))
        self._run(exclusions=[ledger.EXCLUSIONS_HEADERS, ["1", "direct_cost", "supplies were for another round"]])
        assert set(PulseReconstructedInvoice.objects.values_list("invoice_number", "amount_usd")) == first
        assert PulseReconstructedInvoice.objects.get(opportunity_id=1, kind="direct_cost").counted is False
        assert costs.opportunity_costs([1])[1].reconstructed_usd == D("3000.00")

    def test_a_dry_run_writes_nothing(self):
        self._world()
        got, write = self._run(dry_run=True)
        assert got["invoices"] == 4 and not PulseLedgerLine.objects.exists() and not write.called

    def test_no_sheet_is_refused(self):
        with pytest.raises(ledger.ImportFailed) as e:
            ledger.run_import(people_reached=lambda ids: {})
        assert e.value.code == "NO_SHEET"

    def test_the_read_tool_returns_invoices_with_their_ledger_lines(self):
        self._world()
        self._run()
        got = tools.costs_reconstructed_invoices(None, opportunity_ids=[1], sources=True)
        assert got["counted_usd"] == 3750.0 and got["sheet"].endswith("/d/sheet-1/edit")
        llo = next(i for i in got["invoices"] if i["kind"] == "llo_payment")
        assert llo["service_delivery"] is False and {s["line_key"] for s in llo["sources"]} == {"b1", "b2"}

    def test_the_page_lists_them(self, client, django_user_model):
        self._world()
        self._run()
        client.force_login(django_user_model.objects.create(username="u", email="u@dimagi.com"))
        page = client.get(reverse("pulse:costs_reconstructed"))
        assert page.status_code == 200 and b"Paid to the organisation" in page.content
        assert b"$3,000 \xe2\x86\x92" not in page.content  # per person is $10.00 -> $22.50 for opp 1
        assert b"$10.00 \xe2\x86\x92 $22.50" in page.content
        csv = client.get(reverse("pulse:costs_reconstructed") + "?format=csv")
        assert csv.content.decode().splitlines()[0].startswith("invoice_number,opportunity_id")


@pytest.mark.django_db
def test_only_dimagi_may_import():
    with mock.patch.object(tools, "_may_import", return_value=False), pytest.raises(MCPToolError):
        tools.costs_ledger_import(None)


class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, str(body)

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(self.status_code)


def _answer(status, body):
    def call(*args, **kwargs):
        return _Resp(status, body)

    return call


class _Creds:
    valid, token, service_account_email = True, "t", "labs@sa.example"


@pytest.mark.django_db
class TestCreateSheet:
    def _run(self, get, post, tabs, **kw):
        written = []
        with mock.patch("connect_labs.labs.synthetic.gdrive._load_credentials", return_value=_Creds()), \
             mock.patch("httpx.get", side_effect=get), mock.patch("httpx.post", side_effect=post), \
             mock.patch.object(ledger.directory, "read_tab", side_effect=lambda sid, tab: tabs[tab]), \
             mock.patch.object(ledger.directory, "write_tab", side_effect=lambda sid, tab, v: written.append(tab)):  # fmt: skip
            return ledger.create_sheet(["p@dimagi.com"], **kw), written

    def test_a_refused_create_says_why_and_how_to_adopt(self):
        refusal = {"error": {"message": "no quota", "errors": [{"reason": "storageQuotaExceeded"}]}}
        with pytest.raises(ledger.ImportFailed) as e:
            self._run(_answer(200, {"parents": ["folder"]}), _answer(403, refusal), {})
        assert e.value.code == "CANNOT_CREATE" and "storageQuotaExceeded" in str(e.value)
        assert "labs@sa.example" in str(e.value) and not PulseScalar.objects.filter(key=ledger.SHEET_KEY).exists()

    def test_adopting_a_sheet_adds_only_the_empty_tabs(self):
        meta = {"parents": ["folder"], "mimeType": "application/vnd.google-apps.spreadsheet",
                "capabilities": {"canEdit": True}}  # fmt: skip
        tabs = {ledger.LEDGER_TAB: LEDGER, ledger.PROGRAMMES_TAB: [], ledger.EXCLUSIONS_TAB: [[""]]}
        got, written = self._run(_answer(200, meta), _answer(200, {}), tabs, spreadsheet_id="s" * 30)
        assert got["adopted"] and got["spreadsheet_id"] == "s" * 30
        assert written == [ledger.PROGRAMMES_TAB, ledger.EXCLUSIONS_TAB]  # the Ledger tab's rows are kept
        assert ledger.sheet_id() == "s" * 30

    def test_adopting_a_sheet_it_cannot_edit_is_refused(self):
        meta = {"mimeType": "application/vnd.google-apps.spreadsheet", "capabilities": {"canEdit": False}}
        with pytest.raises(ledger.ImportFailed) as e:
            self._run(_answer(200, meta), None, {}, spreadsheet_id="s" * 30)
        assert e.value.code == "NO_ACCESS" and "labs@sa.example" in str(e.value)
