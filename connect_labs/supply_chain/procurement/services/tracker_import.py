"""Import a procurement due-diligence tracker from Drive into a programme.

**Why the sheet and not a fixture.** The same reason `pulse_partner_import`
reads the LLO directory from Drive: no supplier name, contact or price is
written into this repository, where the people who own that identity cannot
review it and where it drifts silently from the sheet. The repository is
public; the tracker is not.

**The sheet is read in HER shape, not ours.** Her columns are "Price /
carton", "Freight", "Total landed" -- a per-round layout with two rounds side
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
HEADER_ROW = 5

# Her two rounds, and which columns hold each. Declared as data so the two are
# handled by one code path -- an earlier sketch had them as two blocks and the
# second silently lacked the freight handling.
ROUNDS = (
    {
        "label": "Round 1 — May 2026",
        "quantity": "500",
        "contacted": 6,
        "responded": 7,
        "quote_date": 8,
        "price": 9,
        "freight": 10,
        "total": 11,
    },
    {
        "label": "Round 2 — February re-quote",
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
    """True for a slash date whose day and month could be either way round.

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


def _read_rows(spreadsheet_id):
    credentials = _load_credentials()
    if credentials is None:
        raise TrackerImportError(
            "LABS_SYNTHETIC_GDRIVE_SA_KEY is not set, so the tracker cannot be read. "
            "It is in 1Password under AI-Agents, 'connect-labs GCP service account key'."
        )
    credentials.refresh(Request())
    response = httpx.get(
        f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}" f"/values/{TAB}!A{HEADER_ROW + 1}:T200",
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
    return [row for row in response.json().get("values", []) if _cell(row, NAME)]


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


def _ensure_rounds(op, commodity_slug):
    """The tracker's rounds, by label, idempotently.

    Matched on label rather than created blindly: this is meant to be
    re-runnable as the sheet is edited, and a second run should update a round
    rather than produce a twin.
    """
    existing = {r["label"]: r["id"] for r in op("round_list")}
    ids = []
    for spec in ROUNDS:
        if spec["label"] in existing:
            ids.append(existing[spec["label"]])
            continue
        created = op(
            "round_create",
            data={
                "label": spec["label"],
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
        op("round_open", round_id=created["id"])
        ids.append(created["id"])
    return ids


def _ensure_supplier(op, row, name, refusals):
    existing = next((s for s in op("supplier_list", search=name) if s["name"] == name), None)
    has_quote = any(_price(_cell(row, spec["price"]))[0] is not None for spec in ROUNDS)
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


def _load_round(op, row, spec, round_id, supplier, commodity_slug, refusals):
    counts = {"invitations": 0, "quotes": 0}
    name = _cell(row, NAME)
    sent_on_raw = _cell(row, spec["contacted"])
    sent_on = _date(sent_on_raw)
    responded_raw = _cell(row, spec["responded"]).lower()
    amount, unit, refusal = _price(_cell(row, spec["price"]))

    if not sent_on and not responded_raw:
        return counts

    responded = responded_raw.startswith("yes") or amount is not None
    op(
        "outreach_log",
        data={
            "round_id": round_id,
            "supplier_id": supplier["id"],
            "channel": "manual",
            **({"sent_on": sent_on} if sent_on else {}),
            "responded": responded,
            "response_kind": ("quote" if amount is not None else "needs_info" if responded else "no_reply"),
            "notes": _cell(row, RATIONALE),
        },
    )
    counts["invitations"] = 1

    for label, raw in (("outreach date", sent_on_raw), ("quote date", _cell(row, spec["quote_date"]))):
        if ambiguous_numeric_date(raw):
            refusals.append(
                f"{name}, {spec['label']}: {label} {raw!r} is ambiguous -- read month-first as "
                f"{_date(raw)}, but the sheet's locale implies day-first. Confirm it."
            )
    if refusal:
        refusals.append(f"{name}, {spec['label']}: {refusal}")
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
        refusals.append(f"{name}, {spec['label']}: freight recorded as not specified, the note was {freight_raw!r}")
    else:
        freight = {"freight_basis": "not_specified"}

    # Quantity basis: what the SUPPLIER priced, which is not always the
    # round's quantity. One quote here covers 100,000 sachets against a
    # 500-carton round, and recording the round's figure instead would make an
    # incomparable quote look comparable.
    quantity_basis = spec["quantity"]
    quantity_unit = "carton"
    if unit == "per_base_unit":
        total = _price(_cell(row, spec["total"]))[0]
        if total is not None and amount:
            quantity_basis = str((total / amount).quantize(Decimal("1")))
            quantity_unit = "sachet"
            refusals.append(
                f"{name}, {spec['label']}: priced per sachet for {quantity_basis} sachets, "
                f"not the round's {spec['quantity']} cartons"
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
            f"{name}, {spec['label']}: duties excluded with no amount -- the note mentions "
            "taxes or duties but states no figure"
        )

    received_on = _date(_cell(row, spec["quote_date"]))
    op(
        "quote_record",
        data={
            "round_id": round_id,
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
        },
    )
    counts["quotes"] = 1
    return counts


def describe(rows) -> list[str]:
    """What a dry run reports: what would be imported, and what would not."""
    out = []
    for row in rows:
        name = _cell(row, NAME)
        bits = [f"{name} ({_cell(row, TYPE) or 'type not stated'}, {_country(_cell(row, LOCATION)) or '??'})"]
        for spec in ROUNDS:
            amount, unit, refusal = _price(_cell(row, spec["price"]))
            if amount is not None:
                bits.append(f"{spec['label']}: {amount} {unit}")
            elif refusal:
                bits.append(f"{spec['label']}: {refusal}")
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

    rows = _read_rows(spreadsheet_id)
    if dry_run:
        return {
            "dry_run": True,
            "rows": len(rows),
            "would_import": describe(rows),
            "imported": {},
            "refused": [],
        }

    op = lambda name, **payload: call_operation(name, access, payload)  # noqa: E731
    refusals: list[str] = []
    imported = {"suppliers": 0, "rounds": 0, "invitations": 0, "quotes": 0}

    round_ids = _ensure_rounds(op, commodity_slug)
    imported["rounds"] = len(round_ids)

    for row in rows:
        name = _cell(row, NAME)
        supplier = _ensure_supplier(op, row, name, refusals)
        imported["suppliers"] += 1
        for spec, round_id in zip(ROUNDS, round_ids, strict=True):
            counts = _load_round(op, row, spec, round_id, supplier, commodity_slug, refusals)
            imported["invitations"] += counts["invitations"]
            imported["quotes"] += counts["quotes"]

    return {
        "dry_run": False,
        "rows": len(rows),
        "imported": imported,
        "refused": sorted(set(refusals)),
    }
