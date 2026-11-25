"""The invoices sheet and its import (`pulse.ledger`): parsing, the import end to end with the sheet
faked, the cost figures, the tools and the page. All data invented."""

import datetime as dt
from decimal import Decimal
from unittest import mock

import pytest
from django.urls import reverse

from connect_labs.mcp.tool_registry import MCPToolError
from connect_labs.mcp.tools import costs_ledger as tools
from connect_labs.pulse import costs, ledger
from connect_labs.pulse.models import PulseReconstructedInvoice, PulseScalar
from connect_labs.pulse.tests.test_costs import _opp, _work

D = Decimal

INVOICES = [
    ledger.INVOICE_HEADERS,
    ["1", "Opp 1", "Solina Health", "llo_payment", "3,000.00", "2025-03-20", "2025-03-01", "2025-03-20", "",
     "Solina Health: bills SHL/1, SHL/2", "yes", ""],
    ["2", "Opp 2", "Solina Health", "llo_payment", "1000", "", "2025-03-01", "2025-03-20", "", "", "", ""],
    ["1", "Opp 1", "Solina Health", "direct_cost", "750", "2025-02-01", "", "", "", "EHA Pharma: P1", "yes", ""],
    ["1", "Opp 1", "Solina Health", "direct_cost", "5", "", "", "", "", "", "yes", ""],
    ["999", "Nope", "?", "llo_payment", "10", "", "", "", "", "", "yes", ""],
    ["2", "Opp 2", "Solina Health", "direct_cost", "n/a", "", "", "", "", "", "maybe", ""],
]  # fmt: skip


@pytest.fixture(autouse=True)
def _fresh():
    costs.invalidate()
    yield
    costs.invalidate()


def test_parse_reads_every_row_it_can_and_names_the_rest():
    parsed = ledger.parse(INVOICES)
    assert [(i["opportunity_id"], i["kind"], i["amount_usd"]) for i in parsed.invoices] == [
        (1, "llo_payment", D("3000.00")), (2, "llo_payment", D("1000.00")), (1, "direct_cost", D("750.00")),
        (999, "llo_payment", D("10.00")),
    ]  # fmt: skip
    assert parsed.invoices[0]["title"] == ledger.KIND_TITLES["llo_payment"]
    assert parsed.problems == [
        "Invoices row 5: opportunity 1 has a second direct_cost invoice",
        "Invoices row 7: amount_usd 'n/a' is not a number; count 'maybe' is not yes or no",
    ]


def test_a_row_that_does_not_count_needs_a_reason():
    rows = [ledger.INVOICE_HEADERS, ["1", "", "", "llo_payment", "1", "", "", "", "", "", "no", ""]]
    assert ledger.parse(rows).problems == ["Invoices row 2: an invoice that does not count needs an exclude_reason"]


def test_the_ledger_is_gone():
    # No accounting line is kept anywhere in labs.
    import connect_labs.pulse.models as models

    assert not hasattr(models, "PulseLedgerLine") and not hasattr(models, "PulseReconstructedInvoiceSource")
    assert not hasattr(ledger, "LEDGER_HEADERS")


@pytest.mark.django_db
class TestImport:
    def _world(self):
        _opp(1, program_id=24)
        _opp(2, program_id=82)
        _work(1, 2000, org=1000, units=3)  # Connect recorded 3,000 for opp 1
        _work(2, 2000, org=1000, units=3)
        PulseScalar.objects.create(key=ledger.SHEET_KEY, value={"id": "sheet-1"})

    def _run(self, rows=INVOICES, **kw):
        reached = {1: {"reached": 300}, 2: {"reached": 100}}
        with mock.patch.object(ledger.directory, "read_tab", lambda sid, tab: rows), \
             mock.patch.object(ledger.directory, "write_tab") as write:  # fmt: skip
            return ledger.run_import(people_reached=lambda ids: reached, **kw), write

    def test_imports_the_invoices_counts_them_and_writes_nothing_back(self):
        self._world()
        got, write = self._run()
        assert not write.called
        assert got["invoices"] == 3 and got["llo_payment_usd"] == 4000.0 and got["direct_cost_usd"] == 750.0
        assert "Invoices: opportunity 999 is not one labs knows" in got["problems"]
        rows = {(r.opportunity_id, r.kind): r for r in PulseReconstructedInvoice.objects.all()}
        assert rows[(1, "llo_payment")].invoice_number == ledger.invoice_number(1, "llo_payment")
        assert rows[(1, "llo_payment")].description == "Solina Health: bills SHL/1, SHL/2"
        assert rows[(2, "llo_payment")].date == dt.date(2025, 3, 20)  # no date: its end date
        assert rows[(1, "llo_payment")].basis["reached"] == 300
        c = costs.opportunity_costs([1])[1]
        assert c.reconstructed_usd == D("3750.00") and c.recorded_usd == D("3000.00")

    def test_reimport_keeps_numbers_and_a_row_marked_no_stops_counting(self):
        self._world()
        self._run()
        PulseReconstructedInvoice.objects.filter(opportunity_id=1, kind="llo_payment").update(
            pushed_invoice_number="INV-9"
        )
        rows = [list(r) for r in INVOICES]
        rows[3][10], rows[3][11] = "no", "supplies were for another round"
        self._run(rows)
        direct = PulseReconstructedInvoice.objects.get(opportunity_id=1, kind="direct_cost")
        assert direct.counted is False and direct.excluded_reason == "supplies were for another round"
        assert (
            PulseReconstructedInvoice.objects.get(opportunity_id=1, kind="llo_payment").pushed_invoice_number
            == "INV-9"
        )
        assert costs.opportunity_costs([1])[1].reconstructed_usd == D("3000.00")

    def test_a_dry_run_writes_nothing(self):
        self._world()
        got, _ = self._run(dry_run=True)
        assert got["invoices"] == 3 and not PulseReconstructedInvoice.objects.exists()

    def test_no_sheet_is_refused(self):
        with pytest.raises(ledger.ImportFailed) as e:
            ledger.run_import(people_reached=lambda ids: {})
        assert e.value.code == "NO_SHEET"

    def test_a_tab_without_its_columns_is_refused(self):
        self._world()
        with pytest.raises(ledger.ImportFailed) as e:
            self._run([["line_key", "usd"], ["a", "1"]])
        assert e.value.code == "SHEET_MALFORMED"

    def test_the_read_tool_returns_the_invoices(self):
        self._world()
        self._run()
        got = tools.costs_reconstructed_invoices(None, opportunity_ids=[1])
        assert got["counted_usd"] == 3750.0 and got["sheet"].endswith("/d/sheet-1/edit")
        llo = next(i for i in got["invoices"] if i["kind"] == "llo_payment")
        assert llo["service_delivery"] is False and "sources" not in llo

    def test_the_page_lists_them(self, client, django_user_model):
        self._world()
        self._run()
        client.force_login(django_user_model.objects.create(username="u", email="u@dimagi.com"))
        page = client.get(reverse("pulse:costs_reconstructed"))
        assert page.status_code == 200 and b"Paid to the organisation" in page.content
        assert b"$10.00 \xe2\x86\x92 $22.50" in page.content  # opp 1: 3,000 -> 6,750 over 300 people
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

        def write(sid, tab, values):
            written.append(tab)

        with mock.patch("connect_labs.labs.synthetic.gdrive._load_credentials", return_value=_Creds()), \
             mock.patch("httpx.get", side_effect=get), mock.patch("httpx.post", side_effect=post), \
             mock.patch.object(ledger.directory, "read_tab", side_effect=lambda sid, tab: tabs[tab]), \
             mock.patch.object(ledger.directory, "write_tab", side_effect=write):  # fmt: skip
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
        tabs = {ledger.INVOICES_TAB: INVOICES, ledger.SUMMARY_TAB: [[""]]}
        got, written = self._run(_answer(200, meta), _answer(200, {}), tabs, spreadsheet_id="s" * 30)
        assert got["adopted"] and got["spreadsheet_id"] == "s" * 30
        assert written == [ledger.SUMMARY_TAB]  # the Invoices tab's rows are kept
        assert ledger.sheet_id() == "s" * 30

    def test_adopting_replaces_the_sheet_labs_used_before_and_making_a_second_is_refused(self):
        PulseScalar.objects.create(key=ledger.SHEET_KEY, value={"id": "old-sheet"})
        with pytest.raises(ledger.ImportFailed) as e:
            self._run(None, None, {})
        assert e.value.code == "SHEET_EXISTS"
        meta = {
            "parents": [],
            "mimeType": "application/vnd.google-apps.spreadsheet",
            "capabilities": {"canEdit": True},
        }
        tabs = {ledger.INVOICES_TAB: [], ledger.SUMMARY_TAB: []}
        self._run(_answer(200, meta), _answer(200, {}), tabs, spreadsheet_id="n" * 30)
        assert ledger.sheet_id() == "n" * 30

    def test_adopting_a_sheet_it_cannot_edit_is_refused(self):
        meta = {"mimeType": "application/vnd.google-apps.spreadsheet", "capabilities": {"canEdit": False}}
        with pytest.raises(ledger.ImportFailed) as e:
            self._run(_answer(200, meta), None, {}, spreadsheet_id="s" * 30)
        assert e.value.code == "NO_ACCESS" and "labs@sa.example" in str(e.value)
