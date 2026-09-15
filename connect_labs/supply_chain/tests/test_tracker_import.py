"""Reading a procurement tracker written by a human.

The parsing rules are the unit worth testing: the network read is a thin
wrapper, but what counts as a price, what counts as a date, and above all
what gets REFUSED are decisions with consequences. The refusals are the
reason this importer exists rather than a copy-paste.

Every sample cell below is taken verbatim in shape from a real tracker, with
supplier names replaced. The real names are never written into this
repository -- that is the whole point of reading them from Drive at runtime.
"""

from decimal import Decimal

import pytest

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.labs.models import LabsOrg
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.procurement.services import tracker_import as t

PROGRAM = 10503


class TestPrice:
    def test_a_clean_figure_is_priced_per_pack_the_column_unit(self):
        assert t._price("$52.42") == (Decimal("52.42"), "per_pack", None)
        assert t._price("$50") == (Decimal("50"), "per_pack", None)
        assert t._price("1,240.50") == (Decimal("1240.50"), "per_pack", None)

    def test_a_per_sachet_price_is_taken_at_the_unit_the_text_names(self):
        amount, unit, refusal = t._price("$0.46/sachet")
        assert (amount, unit, refusal) == (Decimal("0.46"), "per_base_unit", None)

    def test_a_hand_derived_figure_is_refused_and_the_stated_one_kept(self):
        """The load-bearing rule. This exact cell shape is why the importer
        exists: $0.46 is what a supplier said, and ~$69 is arithmetic its own
        author flagged as unconfirmed. Importing the $69 would turn a caveat
        into a stored number that looks authoritative."""
        cell = "$0.46/sachet (quoted). Carton price not given; ~$69/carton is derived " "@150/carton, unconfirmed."
        amount, unit, refusal = t._price(cell)
        assert amount == Decimal("0.46")
        assert unit == "per_base_unit"
        assert refusal is None

    def test_prose_with_no_figure_is_refused_and_quoted_back(self):
        amount, unit, refusal = t._price("Need to confirm - also duties/taxes due to IDEC")
        assert amount is None
        assert unit is None
        assert "no stated figure" in refusal
        assert "IDEC" in refusal, "the refusal should quote the cell, so it can be acted on"

    def test_the_blanks_a_human_writes_are_not_zero(self):
        for blank in ("", "  ", "-", "—", "N/A", "n/a", "None", "Pending", "TBC"):
            assert t._price(blank) == (None, None, None), blank


class TestDate:
    def test_it_reads_the_four_ways_the_tracker_writes_dates(self):
        assert t._date("20 April 2026") == "2026-04-20"
        assert t._date("9 Sep 2026") == "2026-09-09"
        assert t._date("2026-09-26") == "2026-09-26"
        # Month-first: the row this comes from was re-contacted 9 Sep and the
        # sheet itself is dated 12 Sep, so 9 October would be in the future.
        assert t._date("9/10/2026") == "2026-09-10"

    def test_an_ambiguous_slash_date_is_flagged_not_silently_resolved(self):
        """The sheet's locale is en_GB, which implies day-first, while its
        data implies month-first. The two disagree and a cell cannot settle
        it, so it is parsed AND reported rather than chosen quietly."""
        assert t.ambiguous_numeric_date("9/10/2026") is True
        # 26 cannot be a month, so there is nothing to be ambiguous about.
        assert t.ambiguous_numeric_date("9/26/2026") is False
        # Same either way round.
        assert t.ambiguous_numeric_date("9/9/2026") is False
        assert t.ambiguous_numeric_date("9 Sep 2026") is False

    def test_an_unreadable_date_is_none_never_today(self):
        """Defaulting to today would silently date an outreach to the day of
        the import, which is exactly the sort of plausible-looking wrong
        figure this domain refuses."""
        assert t._date("sometime in the spring") is None
        assert t._date("Pending") is None
        assert t._date("") is None


class TestCountry:
    def test_it_reads_a_country_out_of_free_text(self):
        assert t._country("Nigeria (Lagos)") == "NG"
        assert t._country("Burkina Faso (Ouagadougou)") == "BF"
        assert t._country("Ethiopia") == "ET"

    def test_an_unrecognised_location_is_blank_not_guessed(self):
        """A wrong country code silently changes freight and duty reasoning,
        so an unreadable one is left empty and reported."""
        assert t._country("somewhere in the Sahel") == ""
        assert t._country("") == ""


class TestContacts:
    def _row(self, names, emails, phones=""):
        row = [""] * 20
        row[t.CONTACT], row[t.EMAIL], row[t.PHONE] = names, emails, phones
        return row

    def test_names_and_addresses_are_paired_when_the_counts_match(self):
        contacts = t._contacts(self._row("Ada; Grace", "ada@example.test; grace@example.test"))
        assert contacts == [
            {"name": "Ada", "email": "ada@example.test", "role": "sales"},
            {"name": "Grace", "email": "grace@example.test", "role": "sales"},
        ]

    def test_mismatched_counts_are_kept_apart_rather_than_paired(self):
        """Four addresses against one name is common in this tracker, and
        zipping them would invent three people."""
        contacts = t._contacts(self._row("Ada", "a@example.test; b@example.test; c@example.test"))
        emails = [c.get("email") for c in contacts if c.get("email")]
        assert emails == ["a@example.test", "b@example.test", "c@example.test"]
        assert any(c.get("note") == "names not matched to addresses" for c in contacts)
        assert not any(c.get("name") == "Ada" and c.get("email") for c in contacts)

    def test_a_dash_is_not_a_contact(self):
        assert t._contacts(self._row("-", "-")) == []


class TestSupplierStatus:
    def _row(self, status="", responded_1="", responded_2=""):
        row = [""] * 20
        row[t.STATUS] = status
        row[7], row[13] = responded_1, responded_2
        return row

    def test_an_explicit_not_yet_contacted_wins_over_everything(self):
        assert t._supplier_status(self._row(status="Not yet contacted"), has_quote=False) == "identified"

    def test_a_quote_makes_a_supplier_quoting(self):
        assert t._supplier_status(self._row(), has_quote=True) == "quoting"

    def test_a_reply_without_a_quote_is_responsive(self):
        assert t._supplier_status(self._row(responded_1="Yes (initial)"), has_quote=False) == "responsive"

    def test_silence_is_contacted_not_responsive(self):
        assert t._supplier_status(self._row(), has_quote=False) == "contacted"


def test_the_operation_is_registered_as_a_write():
    """It was a management command only, which made it the one capability in
    this domain the API and MCP could not reach -- and left no way to run the
    import where there is no shell."""
    from connect_labs.supply_chain.operations import get_operation

    assert get_operation("tracker_import").is_write is True


def _group_row(round_one="Round 1 Quote (500 cartons)", round_two="Feb Re-quote (2,000 cartons)"):
    """Row 4: the sheet's merged group headers, which NAME the rounds."""
    row = [""] * 20
    row[t.ROUNDS[0]["label_column"]] = round_one
    row[t.ROUNDS[1]["label_column"]] = round_two
    return row


def _row(
    *, price="$52.42", freight="Not specified", name="Harmattan Foods", quote_date="2026-05-06", contacted="2026-05-01"
):
    """A tracker row in the sheet's own 20-column shape."""
    row = [""] * 20
    row[t.NAME] = name
    row[t.TYPE] = "Manufacturer"
    row[t.LOCATION] = "Nigeria"
    spec = t.ROUNDS[0]
    row[spec["contacted"]] = contacted
    row[spec["responded"]] = "Yes"
    row[spec["quote_date"]] = quote_date
    row[spec["price"]] = price
    row[spec["freight"]] = freight
    return row


class TestDryRun:
    """A dry run's job is to preview what the sheet will and will not give up.

    `refused` was a hardcoded empty list on that path, so previewing a sheet
    with five refusals reported none. An empty list is not an absence of
    information -- it ASSERTS that nothing was refused, which is exactly the
    substitution (a confident zero for an unknown) that the rest of this
    domain exists to refuse. And the advice attached to the operation is "use
    dry_run first", so the misleading half is the one a reader sees first.
    """

    @pytest.mark.django_db
    def test_a_dry_run_reports_the_same_refusals_the_real_run_makes(self, monkeypatch):
        monkeypatch.setattr(t, "_read_sheet", lambda _id: (_group_row(), [_row()]))
        da = SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)

        dry = t.import_tracker(da, ensure_commodity=True, dry_run=True)
        real = t.import_tracker(da, ensure_commodity=True)

        assert dry["refused"], "a dry run reported nothing refused for a sheet that refuses something"
        assert dry["refused"] == real["refused"]
        assert any("freight recorded as not specified" in r for r in dry["refused"])

    @pytest.mark.django_db
    def test_a_dry_run_still_writes_nothing(self, monkeypatch):
        """The refusals now come from walking the same code, so the write is
        what has to be suppressed -- not the traversal."""
        monkeypatch.setattr(t, "_read_sheet", lambda _id: (_group_row(), [_row()]))
        da = SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)

        t.import_tracker(da, ensure_commodity=True, dry_run=True)

        assert call_operation("supplier_list", da, {}) == []
        assert call_operation("round_list", da, {}) == []
        assert call_operation("commodity_list", da, {}) == []


class TestRoundLabels:
    """A round is named by the sheet, not by this module.

    The labels were literals: "Round 1 — May 2026" and "Round 2 — February
    re-quote". The sheet's own header for the first is "Round 1 Quote (500
    cartons)" -- no month anywhere. May was ONE supplier's quote date (DABS,
    18 May 2026) promoted into the round's identity, and Round 1 actually
    runs from EHA's 23 Feb quote to DABS's 18 May one. So the label was
    wrong for EHA and misleading for the rest, and because it lived in a
    string constant no derivation guard could catch it -- the same
    stored-derived-value substitution this domain refuses everywhere it can
    see one.
    """

    def test_a_round_is_named_by_the_sheets_own_group_header(self):
        labels = t._round_labels(_group_row())
        assert labels == ["Round 1 Quote (500 cartons)", "Feb Re-quote (2,000 cartons)"]

    def test_no_label_asserts_a_month_the_sheet_does_not_state(self):
        labels = t._round_labels(_group_row())
        assert not any("May" in label for label in labels)

    def test_two_rounds_resolving_to_one_name_is_refused_not_merged(self):
        """Reading the label off the sheet made a collision reachable for the
        first time: as literals the two were distinct by construction.

        `_ensure_rounds` keys on the label, so two identical headers map both
        specs to ONE round id and `_load_round` then writes the Feb re-quote's
        prices against Round 1 -- two rounds silently collapsed, with every
        quantity and age attributed to the wrong one. Refused rather than
        disambiguated: appending a suffix would invent a name, which is the
        defect this whole change exists to remove.
        """
        with pytest.raises(t.TrackerImportError) as caught:
            t._round_labels(_group_row(round_one="Quote", round_two="Quote"))
        assert "distinct" in str(caught.value)
        assert "Quote" in str(caught.value)

    def test_a_stated_header_colliding_with_the_other_fallback_is_refused(self):
        """The fallbacks are real labels too, so a header reading "Round 2"
        on the FIRST round collides with the second's fallback."""
        with pytest.raises(t.TrackerImportError):
            t._round_labels(_group_row(round_one="Round 2", round_two=""))

    def test_a_blank_header_falls_back_to_an_ordinal_never_an_inferred_month(self):
        labels = t._round_labels(_group_row(round_one="", round_two=""))
        assert labels == ["Round 1", "Round 2"]

    def test_the_header_rows_are_dropped_by_position_not_truthiness(self, monkeypatch):
        """Rows 4 and 5 both carry text in the supplier-name column
        ("Supplier Identification", "Supplier name"), so a truthiness filter
        would import two phantom suppliers."""
        group = _group_row()
        column_headers = [""] * 20
        column_headers[t.NAME] = "Supplier name"
        payload = {"values": [group, column_headers, _row(name="Harmattan Foods")]}

        class _Response:
            status_code = 200

            def json(self):
                return payload

            def raise_for_status(self):
                pass

        monkeypatch.setattr(t, "_load_credentials", lambda: _FakeCreds())
        monkeypatch.setattr(t.httpx, "get", lambda *a, **k: _Response())

        returned_group, rows = t._read_sheet("sheet-id")
        assert returned_group == group
        assert [r[t.NAME] for r in rows] == ["Harmattan Foods"]


class _FakeCreds:
    token = "unused"
    service_account_email = "sa@example.com"

    def refresh(self, _request):
        pass


class TestRefusalAttribution:
    """Every refusal names the round it belongs to.

    A refusal on the 500-carton round and one on the 2,000-carton re-quote
    are different follow-ups, so a message that cannot be attributed to a
    round is close to useless. Threading the round label through
    `_load_round` put it in scope of a loop that already bound `label` to a
    date FIELD name ("outreach date", "quote date"). Python leaks the loop
    variable, so from that loop onward every refusal reported the field
    name where the round belonged -- silently, because the assertions only
    ever matched the tail of the message.
    """

    @pytest.mark.django_db
    def test_every_refusal_names_its_round(self, monkeypatch):
        monkeypatch.setattr(t, "_read_sheet", lambda _id: (_group_row(), [_row()]))
        da = SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)

        refused = t.import_tracker(da, ensure_commodity=True, dry_run=True)["refused"]

        assert refused
        for message in refused:
            assert "Round 1 Quote (500 cartons)" in message, message

    @pytest.mark.django_db
    def test_an_ambiguous_date_names_the_round_and_the_field(self, monkeypatch):
        """The two are different things and the message needs both: which
        round, and which of its dates could not be read."""
        monkeypatch.setattr(t, "_read_sheet", lambda _id: (_group_row(), [_row(quote_date="9/10/2026")]))
        da = SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)

        refused = t.import_tracker(da, ensure_commodity=True, dry_run=True)["refused"]

        ambiguous = [m for m in refused if "ambiguous" in m]
        assert len(ambiguous) == 1, refused
        assert "Round 1 Quote (500 cartons)" in ambiguous[0]
        assert "quote date" in ambiguous[0]


class TestRerun:
    """A second run of the same sheet must not double the programme.

    The operation is described as idempotent and is re-run whenever the sheet
    is edited, but only ROUNDS and SUPPLIERS were matched before writing --
    outreach and quotes were created unconditionally. Re-importing the real
    tracker took labs programme 10063 from 3 quotes and 16 invitations to 6
    and 32, with every supplier listed twice on the comparison screen.

    It went unnoticed because the report counts what a RUN wrote, not what the
    programme now holds, and those read identically on a first import.
    """

    @pytest.mark.django_db
    def test_a_second_run_of_an_unchanged_sheet_adds_nothing(self, monkeypatch):
        monkeypatch.setattr(t, "_read_sheet", lambda _id: (_group_row(), [_row()]))
        da = SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)

        t.import_tracker(da, ensure_commodity=True)
        first_quotes = call_operation("quote_list", da, {})
        first_outreach = call_operation("outreach_list", da, {})

        t.import_tracker(da, ensure_commodity=True)

        assert len(call_operation("quote_list", da, {})) == len(first_quotes)
        assert len(call_operation("outreach_list", da, {})) == len(first_outreach)

    @pytest.mark.django_db
    def test_the_report_distinguishes_what_it_wrote_from_what_was_already_there(self, monkeypatch):
        """The count that hid this. A run that writes nothing must not report
        the same numbers as a run that created everything."""
        monkeypatch.setattr(t, "_read_sheet", lambda _id: (_group_row(), [_row()]))
        da = SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)

        first = t.import_tracker(da, ensure_commodity=True)
        second = t.import_tracker(da, ensure_commodity=True)

        assert first["imported"]["quotes"] == 1
        assert second["imported"]["quotes"] == 0
        assert second["unchanged"]["quotes"] == 1

    @pytest.mark.django_db
    def test_a_changed_price_is_refused_rather_than_silently_replacing_the_quote(self, monkeypatch):
        """A quote is a supplier's stated fact and carries its own revision
        chain (version, superseded_by_quote_id, quote_correct). Overwriting it
        because a spreadsheet cell moved would destroy that trail, so the
        difference is reported and left for quote_correct."""
        monkeypatch.setattr(t, "_read_sheet", lambda _id: (_group_row(), [_row(price="$52.42")]))
        da = SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)
        t.import_tracker(da, ensure_commodity=True)

        monkeypatch.setattr(t, "_read_sheet", lambda _id: (_group_row(), [_row(price="$60.00")]))
        result = t.import_tracker(da, ensure_commodity=True)

        assert result["imported"]["quotes"] == 0
        assert any("quote_correct" in r for r in result["refused"]), result["refused"]
        assert any("52.42" in r and "60.00" in r for r in result["refused"]), result["refused"]
        assert len(call_operation("quote_list", da, {})) == 1

    @pytest.mark.django_db
    def test_a_later_invitation_is_a_second_event_not_a_duplicate(self, monkeypatch):
        """Outreach is deliberately NOT unique per (round, supplier) -- the
        model says so, because re-inviting is a real event worth keeping. So
        the match is on the date: the same invitation read twice is one event,
        an invitation on a new date is two."""
        monkeypatch.setattr(t, "_read_sheet", lambda _id: (_group_row(), [_row()]))
        da = SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)
        t.import_tracker(da, ensure_commodity=True)

        monkeypatch.setattr(t, "_read_sheet", lambda _id: (_group_row(), [_row(contacted="2026-06-01")]))
        t.import_tracker(da, ensure_commodity=True)

        assert len(call_operation("outreach_list", da, {})) == 2


class TestReviewFindings:
    """Three gaps CodeRabbit found in the idempotency fix (#1778)."""

    def test_a_currency_change_is_a_difference_even_at_the_same_number(self):
        """The importer always writes USD, so comparing only the figure means
        a stored quote in another currency with an equal number reads as
        unchanged -- and a currency mismatch is precisely the kind of thing
        that must not be silently agreed with."""
        existing = {
            "id": 1,
            "as_quoted_amount": "52.42",
            "as_quoted_unit": "per_pack",
            "as_quoted_currency": "EUR",
            "fx_rate_to_usd": "1",
            "quantity_basis": "500",
            "quantity_basis_unit": "carton",
            "freight_basis": "excluded",
            "freight_amount": None,
            "duties_basis": "not_specified",
            "duties_amount": None,
            "received_on": "2026-05-06",
        }
        data = dict(existing, as_quoted_currency="USD")
        differences = t._quote_differences(existing, data)
        assert any("currency" in d for d in differences), differences

    @pytest.mark.django_db
    def test_a_changed_outreach_row_is_reported_as_written_not_unchanged(self, monkeypatch):
        """It calls outreach_update -- a write -- so counting it as
        `unchanged` reintroduces exactly the confusion this fix was for: a
        report that does not say what the run did."""
        monkeypatch.setattr(t, "_read_sheet", lambda _id: (_group_row(), [_row()]))
        da = SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)
        t.import_tracker(da, ensure_commodity=True)

        # Same date, different reply state: the invitation is the same event,
        # but the row has to change.
        row = _row()
        row[t.ROUNDS[0]["responded"]] = "No"
        row[t.ROUNDS[0]["price"]] = ""
        monkeypatch.setattr(t, "_read_sheet", lambda _id: (_group_row(), [row]))
        result = t.import_tracker(da, ensure_commodity=True)

        assert result["imported"]["invitations"] == 1
        assert result["unchanged"]["invitations"] == 0
        assert len(call_operation("outreach_list", da, {})) == 1

    @pytest.mark.django_db
    def test_an_unchanged_outreach_row_is_not_rewritten(self, monkeypatch):
        monkeypatch.setattr(t, "_read_sheet", lambda _id: (_group_row(), [_row()]))
        da = SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)
        t.import_tracker(da, ensure_commodity=True)
        result = t.import_tracker(da, ensure_commodity=True)
        assert result["imported"]["invitations"] == 0
        assert result["unchanged"]["invitations"] == 1

    @pytest.mark.django_db
    def test_a_dry_run_sees_what_is_already_there(self, monkeypatch):
        """Suppressing writes by suppressing READS made the preview claim it
        would import three quotes that already existed -- the same mistake as
        the hardcoded empty `refused`, in a new place."""
        monkeypatch.setattr(t, "_read_sheet", lambda _id: (_group_row(), [_row()]))
        da = SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)
        t.import_tracker(da, ensure_commodity=True)

        preview = t.import_tracker(da, ensure_commodity=True, dry_run=True)

        assert preview["unchanged"]["quotes"] == 1
        assert preview["unchanged"]["invitations"] == 1
        # And still wrote nothing.
        assert len(call_operation("quote_list", da, {})) == 1


class TestNoInventedOrganisation:
    """The import must not invent an organisation.

    An earlier version created a `programme_org` party called "Programme
    team" in each programme, so a spreadsheet importer minted a new
    organisation record for Dimagi -- a body that plainly exists in Connect.
    That is the second-registry behaviour this work removes. Dimagi resolves
    to the one Dimagi row instead, whether or not an import ever ran.
    """

    @pytest.mark.django_db
    def test_an_import_creates_no_organisation(self, monkeypatch):
        monkeypatch.setattr(t, "_read_sheet", lambda _id: (_group_row(), [_row()]))
        da = SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)

        t.import_tracker(da, ensure_commodity=True)

        assert not LabsOrg.objects.filter(name="Programme team").exists()

    @pytest.mark.django_db
    def test_a_dimagi_user_is_attributed_without_one(self, monkeypatch):
        """The end of the chain, and the reason the invention was never
        needed: who Dimagi is does not depend on a programme's setup."""
        from connect_labs.supply_chain.identity import resolve_org

        monkeypatch.setattr(t, "_read_sheet", lambda _id: (_group_row(), [_row()]))
        da = SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)
        t.import_tracker(da, ensure_commodity=True)

        class _User:
            email = "sophie@dimagi.com"
            is_authenticated = True

        assert resolve_org(SupplyDataAccess(program_id=PROGRAM, user=_User(), caller=SYSTEM)).slug == "dimagi"
