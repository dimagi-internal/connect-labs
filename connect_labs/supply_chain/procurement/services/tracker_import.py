"""Import a procurement due-diligence tracker from Drive into a programme.

**Why the sheet and not a fixture.** The same reason `marketplace_import`
reads the LLO directory from Drive: no supplier name, contact or price is
written into this repository, where the people who own that identity cannot
review it and where it drifts silently from the sheet. The repository is
public; the tracker is not.

**The sheet is read in HER shape, not ours.** Her columns are "Price /
carton", "Freight", "Total landed" -- a per-tender layout with two tenders side
by side -- and several cells are prose rather than numbers. The loader's job
is to carry across exactly what she wrote and no more. In particular it
REFUSES to import her own derived figures:

    "$0.46/sachet (quoted). Carton price not given; ~$69/carton is derived
     @150/carton, unconfirmed."

$0.46 per sachet is a fact the supplier stated and is imported. The ~$69 is a
derivation she did by hand and flagged as unconfirmed; importing it as a
price would turn her own caveat into a stored number that looks authoritative
and would then be re-derived on top of. The system derives that figure itself,
from the pack specification, and says so when it cannot.

Every refusal is reported. That report is the point of the run as much as the
import is: it is the list of things her spreadsheet knows and this system
cannot yet act on.

**Why this is a service and not just a management command.** It was a command
only, which made it the one capability in this domain that the API and MCP
could not reach -- and "no capability without an operation" is the rule the
whole registry exists to enforce (design doc section 9). It also made the
import impossible to run against the deployed environment, where there is no
shell. The command is now a thin wrapper over `import_tracker` and the
operation calls the same function.
"""

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

import httpx
from google.auth.transport.requests import Request

from connect_labs.labs.synthetic.gdrive import _load_credentials

# "Connect-RUTF Supply Bootstrap" -- a copy of Sophie's tracker, shared with
# the labs service account. A module constant so a normal run needs no
# argument, overridable so a different programme's tracker can be loaded.
SPREADSHEET_ID = "1O985Gh2aTp8ugqgg7QrlvPCy8VFdLAMkEMC12s2ByzA"
TAB = "Sheet1"
# Row 4 carries the merged group headers ("Tender 1 Quote (500 cartons)",
# "Feb Re-quote (2,000 cartons)"); row 5 the per-column ones.
GROUP_HEADER_ROW = 4
HEADER_ROW = 5

# Her two tenders, and which columns hold each. Declared as data so the two are
# handled by one code path -- an earlier sketch had them as two blocks and the
# second silently lacked the freight handling.
TENDERS = (
    {
        # The tender's NAME comes off the sheet's own group header, at this
        # column. It used to be the literal "Tender 1 — May 2026", which the
        # sheet nowhere states: May was one supplier's quote date (DABS, 18
        # May) promoted into the tender's identity. Tender 1 actually spans
        # February to May across suppliers, so the label was wrong for EHA,
        # whose quote is dated 23 Feb. A derived value stored as though
        # stated -- in a string constant, where no derivation guard could
        # see it.
        "label_column": 8,
        "fallback_label": "Tender 1",
        "quantity": "500",
        "contacted": 6,
        "responded": 7,
        "quote_date": 8,
        "price": 9,
        "freight": 10,
        "total": 11,
    },
    {
        # "Feb Re-quote" names the February DELIVERY requirement, not a
        # February quote: it was re-contacted 9 Sep 2026. Tender names in this
        # tracker describe the requirement, never the quote date -- which is
        # the rule the Tender 1 label broke.
        "label_column": 12,
        "fallback_label": "Tender 2",
        "quantity": "2000",
        "contacted": 12,
        "responded": 13,
        "quote_date": 14,
        "price": 15,
        "freight": 16,
        "total": 17,
    },
)

NAME, TYPE, LOCATION, CONTACT, EMAIL, PHONE = 0, 1, 2, 3, 4, 5
STATUS, RATIONALE = 18, 19

SUPPLIER_TYPES = {"manufacturer": "manufacturer", "distributor": "distributor", "trader": "trader"}

# From the free-text "Location / origin" column. Only exact, unambiguous
# matches: a wrong country code on a supplier silently changes freight and
# duty reasoning, so anything unrecognised is left blank and reported.
COUNTRIES = {
    "nigeria": "NG",
    "burkina faso": "BF",
    "ethiopia": "ET",
    "kenya": "KE",
    "france": "FR",
    "norway": "NO",
    "south africa": "ZA",
}

_CLEAN_MONEY = re.compile(r"^\$?\s*([\d,]+(?:\.\d+)?)\s*$")
_PER_UNIT = re.compile(r"\$\s*([\d,]+(?:\.\d+)?)\s*/\s*(sachet|carton)", re.IGNORECASE)
_NOT_A_VALUE = {"", "-", "—", "n/a", "na", "none", "pending", "tbc"}


def _decimal(text):
    try:
        return Decimal(text.replace(",", ""))
    except (InvalidOperation, AttributeError):
        return None


def _cell(row, index):
    return (row[index] if index < len(row) else "").strip()


def _price(raw):
    """(amount, as_quoted_unit, refusal) from a price cell.

    Three outcomes, and the third is the interesting one:

      a clean number      -> imported, priced per carton (the column's own unit)
      "$0.46/sachet ..."  -> imported at the unit the text names
      anything else       -> refused, with the cell quoted back

    A cell that mixes a stated price with a hand-derived one resolves to the
    STATED price only; the derivation is left to the system, which knows when
    it cannot do it.
    """
    if not raw or raw.strip().lower() in _NOT_A_VALUE:
        return None, None, None

    clean = _CLEAN_MONEY.match(raw)
    if clean:
        return _decimal(clean.group(1)), "per_pack", None

    per_unit = _PER_UNIT.search(raw)
    if per_unit:
        unit = "per_base_unit" if per_unit.group(2).lower() == "sachet" else "per_pack"
        return _decimal(per_unit.group(1)), unit, None

    return None, None, f"price not imported, no stated figure in {raw!r}"


# Month-first, because that is what this tracker's data means: a row
# re-contacted on 9 Sep whose new quote is dated "9/10/2026" was quoted on 10
# September, not 9 October -- October would be in the future relative to the
# sheet itself. But the spreadsheet's locale is en_GB, which implies the
# opposite reading, so the two signals genuinely disagree and the choice
# cannot be settled from a cell. See _ambiguous_numeric_date: rather than
# resolve it silently, an ambiguous one is parsed AND reported.
_DATE_FORMATS = ("%d %B %Y", "%d %b %Y", "%m/%d/%Y", "%Y-%m-%d")

_NUMERIC_DATE = re.compile(r"^\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*$")


def ambiguous_numeric_date(raw) -> bool:
    """True for a slash date whose day and month could be either way tender.

    "9/10/2026" is 10 September read month-first and 9 October read
    day-first, and nothing in the cell says which. "9/26/2026" is
    unambiguous because 26 cannot be a month. Reported rather than resolved:
    a month's error in a date that drives "days waiting" is small, but
    silently choosing is how a system stops being trustworthy about the
    things that are not small.
    """
    match = _NUMERIC_DATE.match(raw or "")
    if not match:
        return False
    first, second = int(match.group(1)), int(match.group(2))
    return first <= 12 and second <= 12 and first != second


def _date(raw):
    """The tracker writes dates four ways. Unparseable is None, never today."""
    text = (raw or "").strip()
    if not text or text.lower() in _NOT_A_VALUE:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _country(location):
    lowered = (location or "").lower()
    for name, code in COUNTRIES.items():
        if name in lowered:
            return code
    return ""


def _contacts(row):
    """Her contact columns are semicolon-separated lists of names and emails.

    Zipped positionally where the counts match, and kept as separate lists
    where they do not -- pairing four emails onto one name would invent a
    person.
    """
    names = [n.strip() for n in _cell(row, CONTACT).split(";") if n.strip() and n.strip() != "-"]
    emails = [e.strip() for e in _cell(row, EMAIL).split(";") if e.strip() and e.strip() != "-"]
    phones = [p.strip() for p in _cell(row, PHONE).split(";") if p.strip() and p.strip() != "-"]

    if names and len(names) == len(emails):
        return [{"name": name, "email": email, "role": "sales"} for name, email in zip(names, emails, strict=True)]
    contacts = [{"email": email, "role": "sales"} for email in emails]
    if names:
        contacts.append({"name": "; ".join(names), "role": "sales", "note": "names not matched to addresses"})
    if phones:
        contacts.append({"phone": "; ".join(phones), "role": "sales"})
    return contacts


def _supplier_status(row, has_quote):
    stated = _cell(row, STATUS).lower()
    if "not yet contacted" in stated:
        return "identified"
    if has_quote:
        return "quoting"
    if _cell(row, 7).lower().startswith("yes") or _cell(row, 13).lower().startswith("yes"):
        return "responsive"
    return "contacted"


class TrackerImportError(Exception):
    """The tracker could not be read or written, with a message worth showing."""


def _tender_labels(group_row):
    """Each tender's name as the SHEET states it.

    Read rather than declared, so the label cannot drift from the sheet or
    quietly assert something the sheet never said. A blank header falls back
    to a bare ordinal -- never to a month inferred from a quote date.
    """
    labels = []
    for spec in TENDERS:
        stated = _cell(group_row, spec["label_column"])
        labels.append(stated or spec["fallback_label"])
    if len(set(labels)) != len(labels):
        # Reachable only since the label became data. `_ensure_tenders` keys on
        # it, so two identical headers map both tenders to one id and the
        # second tender's prices land against the first -- two tenders collapsed
        # into one, with every quantity and age on the wrong tender. Refused
        # rather than disambiguated: appending a suffix would invent a name,
        # which is the defect this function exists to remove.
        raise TrackerImportError(
            f"the sheet's tender headers must be distinct, and resolved to {labels!r}. "
            "Give each tender group its own header."
        )
    return labels


def _read_sheet(spreadsheet_id):
    """Return (group header row, data rows).

    One request covering row 4 onward: the group headers name the tenders and
    everything from row 6 is a supplier.
    """
    credentials = _load_credentials()
    if credentials is None:
        raise TrackerImportError(
            "LABS_SYNTHETIC_GDRIVE_SA_KEY is not set, so the tracker cannot be read. "
            "It is in 1Password under AI-Agents, 'connect-labs GCP service account key'."
        )
    credentials.refresh(Request())
    response = httpx.get(
        f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}" f"/values/{TAB}!A{GROUP_HEADER_ROW}:T200",
        headers={"Authorization": f"Bearer {credentials.token}"},
        timeout=60,
    )
    if response.status_code in (403, 404):
        # Name the address rather than saying "share it with the service
        # account": the whole difficulty of this failure is not knowing WHICH
        # account, and the credentials in hand know.
        address = getattr(credentials, "service_account_email", "the service account")
        raise TrackerImportError(
            f"the service account cannot read spreadsheet {spreadsheet_id} "
            f"(HTTP {response.status_code}). Share it as a reader with: {address}"
        )
    response.raise_for_status()
    values = response.json().get("values", [])
    # Rows 4 and 5 are headers and both carry text in the name column
    # ("Supplier Identification", "Supplier name"), so they cannot be
    # filtered out by truthiness -- they are dropped by position.
    offset = HEADER_ROW + 1 - GROUP_HEADER_ROW
    group_row = values[0] if values else []
    return group_row, [row for row in values[offset:] if _cell(row, NAME)]


def ensure_rutf(access):
    """RUTF as the published specification defines it.

    Sachet weight, pack size and minimum shelf life come from the 2007
    WHO/WFP/UNICEF/UN-SCN joint statement and UNICEF Supply Division's
    published technical specification -- facts, not choices.

    `course_definition` is left EMPTY on purpose. How many sachets a day for
    how many days is a programme's treatment protocol, not a property of the
    commodity, and inventing one here would silently unlock every
    cost-per-course figure in the system on a number nobody agreed. It comes
    back Unconfirmed until the programme states it, which is the correct
    answer.
    """
    from connect_labs.supply_chain.operations import call_operation

    call_operation(
        "commodity_upsert",
        access,
        {
            "data": {
                "slug": "rutf",
                "name": "Ready-to-use therapeutic food",
                "category": "therapeutic_food",
                "base_unit": "sachet",
                "pack_unit": "carton",
                "base_per_pack": 150,
                "base_unit_grams": 92,
                "shelf_life_months_minimum": 18,
                "spec_reference": "WHO/WFP/UNICEF/UN-SCN joint statement, 2007",
            }
        },
    )


def _ensure_tenders(op, commodity_slug, labels):
    """The tracker's tenders, by label, idempotently.

    Matched on label rather than created blindly: this is meant to be
    re-runnable as the sheet is edited, and a second run should update a tender
    rather than produce a twin.
    """
    existing = {r["label"]: r["id"] for r in op("tender_list")}
    ids = []
    for spec, label in zip(TENDERS, labels, strict=True):
        if label in existing:
            ids.append(existing[label])
            continue
        created = op(
            "tender_create",
            data={
                "label": label,
                "lines": [
                    {
                        "commodity_slug": commodity_slug,
                        "quantity": spec["quantity"],
                        "quantity_unit": "carton",
                    }
                ],
                "delivery_point": {
                    "name": "Central store",
                    "city": "Kano",
                    "country": "NG",
                    "country_name": "Nigeria",
                    "incoterm_requested": "DDP",
                },
                "shelf_life_months_minimum": 18,
            },
        )
        op("tender_open", tender_id=created["id"])
        ids.append(created["id"])
    return ids


def _ensure_supplier(op, row, name, refusals):
    existing = next((s for s in op("supplier_list", search=name) if s["name"] == name), None)
    has_quote = any(_price(_cell(row, spec["price"]))[0] is not None for spec in TENDERS)
    location = _cell(row, LOCATION)
    country = _country(location)
    if not country and location:
        refusals.append(f"{name}: country not recognised in {location!r}, left blank")

    data = {
        "name": name,
        "type": SUPPLIER_TYPES.get(_cell(row, TYPE).lower(), ""),
        "country": country,
        "city": location,
        "status": _supplier_status(row, has_quote),
        "contacts": _contacts(row),
        "notes": _cell(row, RATIONALE),
    }
    if existing:
        return op("supplier_update", supplier_id=existing["id"], data=data)
    return op("supplier_create", data=data)


def _decimals_equal(left, right) -> bool:
    """Compare two money/quantity values without tripping over formatting.

    "52.42" and "52.4200" are the same stated price, and a re-import must not
    report a supplier changing their quote because a serialiser padded it.
    """
    if left in (None, "") and right in (None, ""):
        return True
    if left in (None, "") or right in (None, ""):
        return False
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except InvalidOperation:
        return str(left) == str(right)


# The facts a SUPPLIER stated. A re-import compares these and nothing else:
# `notes` is our own prose and may be edited freely, and comparing it would
# report a changed quote every time somebody tidied a sentence.
_QUOTE_FACTS = (
    "as_quoted_unit",
    # The currency the supplier priced in, and the rate used to bring it to
    # USD. The importer always writes USD at 1, so leaving these out meant a
    # stored quote in another currency with an equal NUMBER read as unchanged
    # -- agreeing silently with a figure that means something different.
    "as_quoted_currency",
    "fx_rate_to_usd",
    "quantity_basis_unit",
    "freight_basis",
    "duties_basis",
    "received_on",
)
_QUOTE_FIGURES = ("as_quoted_amount", "quantity_basis", "freight_amount", "duties_amount")


def _quote_differences(existing: dict, data: dict) -> list[str]:
    """What the sheet now says that the recorded quote does not."""
    out = []
    for field in _QUOTE_FACTS:
        was, now = existing.get(field) or "", data.get(field) or ""
        if str(was) != str(now):
            out.append(f"{field} {was or 'unset'!r} -> {now or 'unset'!r}")
    for field in _QUOTE_FIGURES:
        if not _decimals_equal(existing.get(field), data.get(field)):
            out.append(f"{field} {existing.get(field) or 'unset'} -> {data.get(field) or 'unset'}")
    return out


def _outreach_differs(existing: dict, data: dict) -> bool:
    """Whether the sheet now says something different about this invitation."""
    for field in ("responded", "response_kind", "notes", "channel"):
        if field in data and existing.get(field) != data[field]:
            return True
    return False


def _live_quote(op, tender_id, supplier_id, commodity_slug):
    """The quote currently standing for this supplier on this tender.

    Voided and superseded versions are skipped: they are history, and a
    re-import should neither match them nor resurrect them.
    """
    for quote in op("quote_list", tender_id=tender_id):
        if (
            quote["supplier_id"] == supplier_id
            and quote["commodity_slug"] == commodity_slug
            and not quote["voided"]
            and quote["superseded_by_quote_id"] is None
        ):
            return quote
    return None


def _load_tender(op, row, spec, label, tender_id, supplier, commodity_slug, refusals):
    counts = {"invitations": 0, "quotes": 0, "unchanged_invitations": 0, "unchanged_quotes": 0}
    name = _cell(row, NAME)
    sent_on_raw = _cell(row, spec["contacted"])
    sent_on = _date(sent_on_raw)
    responded_raw = _cell(row, spec["responded"]).lower()
    amount, unit, refusal = _price(_cell(row, spec["price"]))

    if not sent_on and not responded_raw:
        return counts

    responded = responded_raw.startswith("yes") or amount is not None
    outreach_data = {
        "tender_id": tender_id,
        "supplier_id": supplier["id"],
        "channel": "manual",
        **({"sent_on": sent_on} if sent_on else {}),
        "responded": responded,
        "response_kind": ("quote" if amount is not None else "needs_info" if responded else "no_reply"),
        "notes": _cell(row, RATIONALE),
    }
    # Matched on the DATE as well as the supplier. Outreach is deliberately not
    # unique per (tender, supplier) -- the model says so, because re-inviting is
    # a real event worth keeping -- so the same invitation read twice is one
    # event and an invitation on a new date is two. Creating unconditionally
    # took programme 10063 from 16 invitations to 32 on a single re-run.
    existing = next(
        (
            o
            for o in op("outreach_list", tender_id=tender_id)
            if o["supplier_id"] == supplier["id"] and (o["sent_on"] or None) == (sent_on or None)
        ),
        None,
    )
    if existing:
        # Only write when something actually differs, and report it as a write
        # when it does. Counting an update as `unchanged` would reintroduce
        # exactly the confusion this guards against: a report that does not
        # say what the run did.
        if _outreach_differs(existing, outreach_data):
            op("outreach_update", outreach_id=existing["id"], data=outreach_data)
            counts["invitations"] = 1
        else:
            counts["unchanged_invitations"] = 1
    else:
        op("outreach_log", data=outreach_data)
        counts["invitations"] = 1

    # `field`, not `label`: `label` is the tender, and Python leaks a loop
    # variable, so binding it here renamed the tender to "quote date" in every
    # refusal from this point on.
    for field, raw in (("outreach date", sent_on_raw), ("quote date", _cell(row, spec["quote_date"]))):
        if ambiguous_numeric_date(raw):
            refusals.append(
                f"{name}, {label}: {field} {raw!r} is ambiguous -- read month-first as "
                f"{_date(raw)}, but the sheet's locale implies day-first. Confirm it."
            )
    if refusal:
        refusals.append(f"{name}, {label}: {refusal}")
    if amount is None:
        return counts

    freight_raw = _cell(row, spec["freight"])
    freight_amount = _price(freight_raw)[0]
    if freight_amount is not None:
        freight = {"freight_basis": "excluded", "freight_amount": str(freight_amount)}
    elif freight_raw and freight_raw.lower() not in _NOT_A_VALUE:
        # Something that is not a number -- "Not specified", "Need to
        # confirm". That is the honest state, not a zero.
        freight = {"freight_basis": "not_specified"}
        refusals.append(f"{name}, {label}: freight recorded as not specified, the note was {freight_raw!r}")
    else:
        freight = {"freight_basis": "not_specified"}

    # Quantity basis: what the SUPPLIER priced, which is not always the
    # tender's quantity. One quote here covers 100,000 sachets against a
    # 500-carton tender, and recording the tender's figure instead would make an
    # incomparable quote look comparable.
    quantity_basis = spec["quantity"]
    quantity_unit = "carton"
    if unit == "per_base_unit":
        total = _price(_cell(row, spec["total"]))[0]
        if total is not None and amount:
            quantity_basis = str((total / amount).quantize(Decimal("1")))
            quantity_unit = "sachet"
            refusals.append(
                f"{name}, {label}: priced per sachet for {quantity_basis} sachets, "
                f"not the tender's {spec['quantity']} cartons"
            )

    # Duty and tax information is not confined to one column: a "Transport"
    # cell reading "Need to confirm - also duties/taxes due to IDEC" carries
    # both. Scanning only the rationale marked such a quote "duties not
    # specified" when the truth is "duties excluded, amount unknown", which
    # reads as a smaller gap than it is.
    duties = {"duties_basis": "not_specified"}
    note = f"{_cell(row, RATIONALE)} {freight_raw}".lower()
    if any(word in note for word in ("duties", "duty", "idec", "taxes")):
        duties = {"duties_basis": "excluded"}
        refusals.append(
            f"{name}, {label}: duties excluded with no amount -- the note mentions "
            "taxes or duties but states no figure"
        )

    received_on = _date(_cell(row, spec["quote_date"]))
    quote_data = {
        "tender_id": tender_id,
        "supplier_id": supplier["id"],
        "commodity_slug": commodity_slug,
        "as_quoted_amount": str(amount),
        "as_quoted_unit": unit,
        "as_quoted_currency": "USD",
        "fx_rate_to_usd": "1",
        "quantity_basis": quantity_basis,
        "quantity_basis_unit": quantity_unit,
        # No quote in this tracker recorded sachets per carton, which is
        # the single fact that blocks every per-sachet comparison.
        "pack_spec_source": "not_stated",
        **freight,
        **duties,
        **({"received_on": received_on} if received_on else {}),
        "notes": _cell(row, RATIONALE),
    }

    # A quote already standing for this supplier on this tender is not
    # something to write over. It carries its own revision chain (version,
    # superseded_by_quote_id, quote_correct), so replacing it because a
    # spreadsheet cell moved would destroy the trail of what the supplier
    # actually said and when. Unchanged means do nothing; changed means say
    # so and leave the correction to the operation built for it.
    standing = _live_quote(op, tender_id, supplier["id"], commodity_slug)
    if standing is not None:
        differences = _quote_differences(standing, quote_data)
        if differences:
            refusals.append(
                f"{name}, {label}: the sheet no longer matches quote {standing['id']} "
                f"({'; '.join(differences)}). A quote is what the supplier stated, so this "
                "is a correction rather than a re-import -- use quote_correct."
            )
        else:
            counts["unchanged_quotes"] = 1
        return counts

    op("quote_record", data=quote_data)
    counts["quotes"] = 1
    return counts


def _no_write_op(access):
    """Every operation a dry run reaches, with only the WRITES taken out.

    Reads go through for real. Stubbing them was the bug: with every `_list`
    answering "nothing exists yet", a preview could not see the quotes and
    invitations already recorded, so it reported three quotes to import that
    were already there and `unchanged` was permanently zero. That is the same
    mistake as the hardcoded empty `refused` this function used to return --
    suppressing a write by suppressing the read that informs it.

    A stubbed write answers with the id its caller will read. `supplier_update`
    echoes the supplier it was given, because the quote lookup keys on it and
    a zero there would make every existing quote look new.
    """

    def op(name, **payload):
        from connect_labs.supply_chain.operations import call_operation, get_operation

        if not get_operation(name).is_write:
            return call_operation(name, access, payload)
        if name == "supplier_update":
            return {"id": payload["supplier_id"]}
        return {"id": 0}

    return op


def describe(rows, labels) -> list[str]:
    """What a dry run reports: what would be imported, and what would not."""
    out = []
    for row in rows:
        name = _cell(row, NAME)
        bits = [f"{name} ({_cell(row, TYPE) or 'type not stated'}, {_country(_cell(row, LOCATION)) or '??'})"]
        for spec, label in zip(TENDERS, labels, strict=True):
            amount, unit, refusal = _price(_cell(row, spec["price"]))
            if amount is not None:
                bits.append(f"{label}: {amount} {unit}")
            elif refusal:
                bits.append(f"{label}: {refusal}")
        out.append(" | ".join(bits))
    return out


def import_tracker(
    access,
    *,
    spreadsheet_id=SPREADSHEET_ID,
    commodity_slug="rutf",
    ensure_commodity=False,
    dry_run=False,
) -> dict:
    """Read the tracker and record what it states, refusing what it derives.

    Returns a report rather than raising on awkward rows. `refused` is as much
    the point of a run as `imported`: it is the list of things the sheet knows
    and this system will not guess at.
    """
    from connect_labs.supply_chain.operations import call_operation

    if access.get_commodity(commodity_slug) is None:
        if not (ensure_commodity and commodity_slug == "rutf"):
            raise TrackerImportError(
                f"commodity {commodity_slug!r} is not in this programme's catalogue. "
                "A quote needs something to be a quote FOR. Pass ensure_commodity to "
                "create RUTF from its published specification."
            )
        if not dry_run:
            ensure_rutf(access)

    group_row, rows = _read_sheet(spreadsheet_id)
    labels = _tender_labels(group_row)

    # A dry run walks the SAME traversal with the write stubbed out, rather
    # than taking a separate preview path. `refused` is the half of the report
    # the caller is told to read, and it used to be hardcoded empty here -- so
    # previewing a sheet that refuses five things reported none. An empty list
    # is not an absence of information; it asserts that nothing was refused,
    # which is the substitution of a confident zero for an unknown that the
    # rest of this domain exists to refuse.
    op = (
        _no_write_op(access) if dry_run else lambda name, **payload: call_operation(name, access, payload)
    )  # noqa: E731
    refusals: list[str] = []
    imported = {"suppliers": 0, "tenders": 0, "invitations": 0, "quotes": 0}
    # Reported separately from `imported`, because reading a RUN's write count
    # as the programme's total is exactly how the duplication this guards
    # against went unnoticed: on a first import the two read identically.
    unchanged = {"invitations": 0, "quotes": 0}

    tender_ids = _ensure_tenders(op, commodity_slug, labels)
    imported["tenders"] = len(tender_ids)

    for row in rows:
        name = _cell(row, NAME)
        supplier = _ensure_supplier(op, row, name, refusals)
        imported["suppliers"] += 1
        for spec, label, tender_id in zip(TENDERS, labels, tender_ids, strict=True):
            counts = _load_tender(op, row, spec, label, tender_id, supplier, commodity_slug, refusals)
            imported["invitations"] += counts["invitations"]
            imported["quotes"] += counts["quotes"]
            unchanged["invitations"] += counts["unchanged_invitations"]
            unchanged["quotes"] += counts["unchanged_quotes"]

    if dry_run:
        return {
            "dry_run": True,
            "rows": len(rows),
            "would_import": describe(rows, labels),
            "imported": {},
            "unchanged": unchanged,
            "refused": sorted(set(refusals)),
        }
    return {
        "dry_run": False,
        "rows": len(rows),
        "imported": imported,
        "unchanged": unchanged,
        "refused": sorted(set(refusals)),
    }
