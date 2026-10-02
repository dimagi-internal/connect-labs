"""The timeline under a tender or an order: every change to it and its children, newest first.

Each line says what changed ("ETA 5 Sep → 19 Sep"), who told us ("ACE
(agent)", "via AI · Sophie", "Sophie Bello") and, when the write carried one,
the quoted source it rested on. Design doc
docs/superpowers/specs/2026-09-26-supply-sophie-history-design.md §4.3.

A record's scope is collected from its CURRENT children plus every child the
revision log knows was ever attached -- a create revision names its parent in
`changes[<fk>][1]`, a delete's flat snapshot in `changes[<fk>]` -- so an
invitation recorded in error and then removed still shows that it was.

`tender_scope_revisions` and `contract_scope_revisions` are the public
collectors; the program overview (Task 8) reads them too.

Under as-of, the lines are cut at the date but the NAMES they print --
suppliers, organisations, shipment references -- are read from the rows as
they are now (inside the rewound transaction where a view calls this, rows the
rewind restored included). A supplier renamed since reads by its new name.
"""

import datetime
from dataclasses import dataclass

from django.contrib.contenttypes.models import ContentType
from django.db.models import Q
from django.urls import reverse

from connect_labs.supply_chain.history.as_of import end_of_day
from connect_labs.supply_chain.history.labels import (
    HIDDEN_FIELDS,
    Lookup,
    actor_label,
    correction_sentence,
    create_what,
    identity,
    is_ai,
    line_summary,
    model_label,
    quote_field_label,
    sender,
    sentence,
    subject,
)
from connect_labs.supply_chain.history.models import Revision


@dataclass
class Entry:
    when: object  # datetime
    sentence: str
    actor: str
    is_ai: bool
    excerpt: str
    source_ref: str
    correct_url: str | None = None
    void_url: str | None = None
    # Which record an update is on ("Shipment · SH-1"); blank for a create or
    # delete, whose sentence already names it.
    subject: str = ""
    # The attnames this line changed, for a page (or a walkthrough) to find a
    # line by what it is about: `[data-fields~=expected_on]`.
    fields: tuple = ()
    # What the excerpt was, as far as its reference says: an email's
    # Message-ID holds an "@"; anything else is a document. Blank with no source.
    source_kind: str = ""
    # When the write that quoted it was recorded.
    recorded_on: object = None  # datetime
    # The last time the same evidence arrived again and was answered from the
    # first write rather than written twice; None when it never did (or, on a
    # past date, had not yet).
    replayed_at: object = None  # datetime
    # The line as the page reads it, one grammar for every kind of change:
    # "<entity> · <which one> · <what happened>" -- "Shipment · SH-1 ·
    # recorded: ETA 5 Sep", "Shipment · SH-1 · ETA 5 Sep → 19 Sep".
    entity: str = ""
    identity: str = ""
    what: str = ""
    # Who the quoted source came from, when the record says: a quote's
    # supplier, a shipment's carrier. Blank otherwise -- never guessed.
    sender: str = ""
    # A change to a shipment's expected arrival, as a tag beside its line:
    # "ETA moved +14 days". Blank for every other line.
    eta_moved: str = ""
    # A line that keeps the books rather than tells the story -- an invitation
    # sent, a chase recorded -- set smaller and muted so replies and quotes
    # stand out among them.
    bookkeeping: bool = False
    # On the line that made a shipment wait on a document we owe, while it
    # still does: "Waiting on us: import permit" (fulfilment/services/holds.py).
    hold: str = ""
    # Why an AI-entered quote's line offers no Correct or Void: "voided" or
    # "corrected", so every such line says something in that place.
    fix_status: str = ""

    # A single change, not an email's worth of them (see EmailEvent).
    is_group = False

    @property
    def source_link_text(self) -> str:
        """What the source toggle says: "Source email" for an email, else "Source"."""
        return "Source email" if self.source_kind == "Email" else "Source"

    @property
    def source_hide_text(self) -> str:
        """What the same affordance says while the source is open: "Hide email"."""
        return "Hide email" if self.source_kind == "Email" else "Hide source"

    @property
    def line(self) -> str:
        if not self.what:
            return self.sentence
        return " · ".join(part for part in (self.entity, self.identity, self.what) if part)


# ---- scope ---------------------------------------------------------------


def _type_q(model):
    # By name rather than by ContentType id: a through model's content type is
    # only created the first time one of its rows is captured.
    return Q(content_type__app_label=model._meta.app_label, content_type__model=model._meta.model_name)


def _child_ids(model, links, program_id) -> set[int]:
    """Ids of `model` rows whose FK `attname` is (or ever was) one of the given parent ids.

    `links` is {attname: parent ids}. Two queries whatever the number of
    parents: one for the rows there now, one for rows the history names.
    """
    links = {attname: sorted(ids) for attname, ids in links.items() if ids}
    if not links:
        return set()
    current = Q()
    known = Q()
    for attname, ids in links.items():
        current |= Q(**{f"{attname}__in": ids})
        known |= Q(**{f"changes__{attname}__1__in": ids}) & ~Q(action="delete")
        known |= Q(**{f"changes__{attname}__in": ids}, action="delete")
    ids = set(model._base_manager.filter(current).values_list("pk", flat=True))
    revisions = Revision.objects.filter(_type_q(model), known, program_id=program_id)
    ids |= {int(pk) for pk in revisions.values_list("object_id", flat=True)}
    return ids


def _commitment_ids(attname, parent_ids, program_id) -> set[int]:
    """What we owe on these tenders or orders, in one query.

    Not `_child_ids`: a commitment is never deleted (resolving keeps the row)
    and never moves to another tender or order, so the rows there now are all
    there ever were and the history need not be searched for more.
    """
    from connect_labs.supply_chain.models import Commitment

    if not parent_ids:
        return set()
    return set(
        Commitment.objects.filter(program_id=program_id, **{f"{attname}__in": sorted(parent_ids)}).values_list(
            "pk", flat=True
        )
    )


def _revisions(scope, program_id, until):
    q = Q()
    for model, ids in scope:
        if ids:
            q |= _type_q(model) & Q(object_id__in=[str(pk) for pk in ids])
    revisions = Revision.objects.filter(q)
    revisions = revisions.filter(program_id=program_id)
    if until is not None:
        revisions = revisions.filter(recorded_at__lte=end_of_day(until))
    return revisions.select_related("call__actor", "content_type").order_by("-recorded_at", "-id")


def tender_scope_revisions(tender_id, *, program_id, until=None):
    """Revisions of a tender, its outreach, quotes, invitations, awards, approvals and their documents,
    and of the order(s) placed from it with everything under them (design doc §4.3).

    `program_id` is required: every query names its program as well as the
    tender, so a revision from any other program cannot appear. One query
    over the union, so a revision in both scopes is listed once.
    """
    return _revisions(_tender_scope(tender_id, program_id), program_id, until)


def contract_scope_revisions(contract_id, *, program_id, until=None):
    """Revisions of an order, its shipments and their lines and charges, receipts and
    their lines, invoices, payments, and the documents on any of them."""
    return _revisions(_contract_scope({int(contract_id)}, program_id), program_id, until)


def _tender_scope(tender_id, program_id):
    from connect_labs.supply_chain.models import (
        Award,
        AwardApproval,
        Commitment,
        Contract,
        Document,
        Outreach,
        Quote,
        Tender,
    )

    tender = {int(tender_id)}
    outreach = _child_ids(Outreach, {"tender_id": tender}, program_id)
    # What we owe on the round -- a supplier's questions, our promises -- and
    # their answers, which are as much the round's story as the quotes.
    commitments = _commitment_ids("tender_id", tender, program_id)
    quotes = _child_ids(Quote, {"tender_id": tender}, program_id)
    awards = _child_ids(Award, {"tender_id": tender}, program_id)
    invited = _child_ids(Tender.invited_orgs.through, {"tender_id": tender}, program_id)
    approvals = _child_ids(AwardApproval, {"award_id": awards}, program_id)
    documents = _child_ids(
        Document,
        {"tender_id": tender, "quote_id": quotes, "award_id": awards, "approval_id": approvals},
        program_id,
    )
    # The order the tender led to is part of its story: the award, then the
    # contract, dispatch, the ETA slip, receipt and payment.
    contracts = _child_ids(Contract, {"tender_id": tender, "award_id": awards}, program_id)
    return [
        (Tender, tender),
        (Outreach, outreach),
        (Commitment, commitments),
        (Quote, quotes),
        (Award, awards),
        (Tender.invited_orgs.through, invited),
        (AwardApproval, approvals),
        (Document, documents),
        *_contract_scope(contracts, program_id),
    ]


def _contract_scope(contract, program_id):
    """(model, ids) for the given orders and everything under them; nothing when there are none."""
    from connect_labs.supply_chain.models import (
        Charge,
        Commitment,
        Contract,
        Document,
        Invoice,
        Payment,
        Receipt,
        ReceiptLine,
        Shipment,
        ShipmentLine,
    )

    if not contract:
        return []
    shipments = _child_ids(Shipment, {"contract_id": contract}, program_id)
    charges = _child_ids(Charge, {"shipment_id": shipments}, program_id)
    shipment_lines = _child_ids(ShipmentLine, {"shipment_id": shipments}, program_id)
    receipts = _child_ids(Receipt, {"contract_id": contract, "shipment_id": shipments}, program_id)
    receipt_lines = _child_ids(ReceiptLine, {"receipt_id": receipts}, program_id)
    invoices = _child_ids(Invoice, {"contract_id": contract}, program_id)
    payments = _child_ids(Payment, {"contract_id": contract, "invoice_id": invoices}, program_id)
    commitments = _commitment_ids("contract_id", contract, program_id)
    documents = _child_ids(
        Document,
        {
            "contract_id": contract,
            "shipment_id": shipments,
            "receipt_id": receipts,
            "invoice_id": invoices,
            "payment_id": payments,
            "charge_id": charges,
        },
        program_id,
    )
    return [
        (Contract, contract),
        (Shipment, shipments),
        (Charge, charges),
        (ShipmentLine, shipment_lines),
        (Receipt, receipts),
        (ReceiptLine, receipt_lines),
        (Invoice, invoices),
        (Payment, payments),
        (Commitment, commitments),
        (Document, documents),
    ]


# ---- lines ---------------------------------------------------------------


def _merge(group):
    """Several revisions one call made to one record, as one (oldest first in `group`).

    A handler that creates a row and then sets a field on it is one thing to
    a reader, not two lines.
    """
    if len(group) == 1:
        return group[0]
    first, last = group[0], group[-1]
    if last.action == "delete":
        return last
    changes = {}
    for revision in group:
        if revision.action == "delete":
            continue
        for attname, (old, new) in revision.changes.items():
            changes[attname] = [changes[attname][0], new] if attname in changes else [old, new]
    action = "create" if first.action == "create" else "update"
    if action == "update":
        changes = {k: v for k, v in changes.items() if v[0] != v[1]}
    return Revision(
        call=last.call,
        program_id=last.program_id,
        content_type=last.content_type,
        object_id=last.object_id,
        action=action,
        changes=changes,
        recorded_at=last.recorded_at,
    )


def _merged(revisions):
    """Newest first in, newest first out, with everything one call did to one record merged.

    Grouped across the whole call, not only adjacent rows: a handler that saves
    a shipment, then its lines, then the shipment again is still one shipment
    line, placed where the group's newest revision falls.
    """
    groups, order = {}, []
    for revision in revisions:
        if revision.call_id is None:
            key = ("single", revision.pk if revision.pk is not None else id(revision))
        else:
            key = (revision.call_id, revision.content_type_id, revision.object_id)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(revision)
    return [_merge(list(reversed(groups[key]))) for key in order]


# A line row created in the same call as its parent is part of that record, not
# an event of its own: {line model name: (parent model name, FK attname)}.
_LINE_PARENTS = {"ShipmentLine": ("Shipment", "shipment_id"), "ReceiptLine": ("Receipt", "receipt_id")}


def _fold_lines(revisions, lookup):
    """Drop line creates whose parent was created by the same call; return {parent revision id(): [summaries]}.

    "Shipment recorded: SH-1, ETA 5 Sep — 300 cartons, batch B1" rather than a
    second line saying the same shipment has a line.
    """
    parents = {}
    for revision in revisions:
        model = revision.content_type.model_class()
        if revision.action == "create" and model is not None and revision.call_id is not None:
            parents[(revision.call_id, model.__name__, str(revision.object_id))] = revision
    kept, suffixes = [], {}
    for revision in revisions:
        model = revision.content_type.model_class()
        link = _LINE_PARENTS.get(model.__name__) if model is not None else None
        if link and revision.action == "create" and revision.call_id is not None:
            parent_name, attname = link
            parent_id = (revision.changes.get(attname) or [None, None])[1]
            parent = parents.get((revision.call_id, parent_name, str(parent_id)))
            if parent is not None:
                summary = line_summary(model, revision.changes, lookup)
                if summary:
                    suffixes.setdefault(id(parent), []).append(summary)
                continue
        kept.append(revision)
    return kept, suffixes


def source_kind(ref) -> str:
    """ "Email" for a Message-ID, "Document" for any other reference, "" for none."""
    if not ref:
        return ""
    return "Email" if "@" in ref else "Document"


def _as_date(value):
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def eta_moved(changes) -> str:
    """ "ETA moved +14 days" (or "-3 days") for a change to `expected_on` between two days; "" otherwise."""
    before, after = (changes or {}).get("expected_on") or (None, None)
    before, after = _as_date(before), _as_date(after)
    if before is None or after is None or before == after:
        return ""
    days = (after - before).days
    return f"ETA moved {'+' if days > 0 else '-'}{abs(days)} day{'' if abs(days) == 1 else 's'}"


def _replayed_at(call, until):
    if call is None or not getattr(call, "replay_count", 0) or call.last_replayed_at is None:
        return None
    if until is not None and call.last_replayed_at > end_of_day(until):
        return None
    return call.last_replayed_at


def entry_for(revision, *, lookup=None, offer_fixes=True, live_quote_ids=None, until=None) -> Entry:
    """One revision as one timeline line.

    `offer_fixes` is off in as-of mode: a past date is read-only. Correct and
    Void are offered only on a quote an AI entered that still stands (not
    voided, not already corrected); `live_quote_ids` answers that for a whole
    timeline in one query, and is looked up here when not given.
    """
    from connect_labs.supply_chain.models import Quote

    lookup = lookup or Lookup()
    model = revision.content_type.model_class()
    call = revision.call
    ai = is_ai(call)
    entry = Entry(
        when=revision.recorded_at,
        sentence=sentence(model, revision.action, revision.changes, lookup) if model is not None else "",
        actor=actor_label(call, lookup),
        is_ai=ai,
        excerpt=getattr(call, "source_excerpt", "") or "",
        source_ref=getattr(call, "source_ref", "") or "",
        fields=tuple(k for k in revision.changes if k not in HIDDEN_FIELDS) if revision.action != "delete" else (),
        source_kind=source_kind(getattr(call, "source_ref", "")),
        recorded_on=getattr(call, "recorded_at", None) or revision.recorded_at,
        replayed_at=_replayed_at(call, until),
        eta_moved=eta_moved(revision.changes) if revision.action == "update" else "",
    )
    if model is None:
        return entry
    values = None
    if revision.action == "update":
        row = lookup.row(model, revision.object_id)
        values = _values(row) if row is not None else None
        entry.subject = subject(model, values, lookup)
    elif revision.action == "create":
        values = {k: v[1] for k, v in revision.changes.items()}
    else:
        values = dict(revision.changes)
    _set_line(entry, model, revision.action, values, lookup, revision.object_id)
    if model.__name__ == "Payment" and revision.action == "update":
        _as_linked_payment(entry, revision.changes, values, lookup)
    # Who sent the evidence, as the caller read it off the email, wins: it is
    # this write's own provenance. An update without one names nobody rather
    # than borrowing the record's first teller (ruling 9).
    stated = (getattr(call, "source_sender", "") or "").strip()
    if stated:
        entry.sender = stated
    elif revision.action == "update":
        entry.sender = ""
    entry.bookkeeping = _is_bookkeeping(model, revision)
    if model is Quote and ai and offer_fixes and revision.action != "delete":
        quote_id = int(revision.object_id)
        if live_quote_ids is None:
            live_quote_ids = set(_live_quotes([quote_id]))
        if quote_id in live_quote_ids:
            entry.correct_url = reverse("supply_chain:procurement_quote_correct", args=[quote_id])
            entry.void_url = reverse("supply_chain:procurement_quote_void", args=[quote_id])
        else:
            row = lookup.row(Quote, quote_id)
            entry.fix_status = "voided" if row is not None and row.voided else "corrected"
    return entry


# What an outreach change touches when it only keeps the books.
_CHASE_FIELDS = {"last_reminder_on", "updated_at"}


def _is_bookkeeping(model, revision) -> bool:
    """An invitation sent, or a chase recorded: lines the round needs kept but nobody reads for."""
    if model is None or model.__name__ != "Outreach":
        return False
    if revision.action == "create":
        return True
    return revision.action == "update" and set(revision.changes) <= _CHASE_FIELDS


def _set_line(entry, model, action, values, lookup, object_id=None):
    """Fill the entry's "<entity> · <which one> · <what happened>" parts."""
    # Who the source came from, off the record as it stands: a carrier
    # recorded after the email arrived still says whose email it was.
    # Only the two records a sender is read from, so no other line costs a query.
    row = None
    if (
        model.__name__ in ("Quote", "Shipment", "Invoice", "Receipt")
        and action != "delete"
        and object_id
        not in (
            None,
            "",
        )
    ):
        row = lookup.row(model, object_id)
    entry.sender = sender(model, _values(row) if row is not None else values, lookup)
    if model._meta.auto_created:
        entry.entity = model_label(model)
        entry.identity = identity(model, values, lookup)
        entry.what = "withdrawn" if action == "delete" else "sent"
        return
    entry.entity = model_label(model)
    entry.identity = identity(model, values, lookup) if values is not None else ""
    if action == "create":
        entry.what = create_what(model, values, lookup)
    elif action == "delete":
        entry.what = "removed"
    else:
        entry.what = entry.sentence


def _as_linked_payment(entry, changes, values, lookup):
    """A payment an invoice later acknowledged: "Payment of 28 Jul linked to INV-HT-26-0912".

    The update that links a payment made in advance to the invoice that
    acknowledged it read "Payment · 53,400.00 USD · Invoice: INV-...", which
    sat beside the payment's own line as if it were a second payment.
    """
    from connect_labs.supply_chain.models import Invoice

    old, new = changes.get("invoice_id") or [None, None]
    if old is not None or new is None:
        return
    paid_on = (values or {}).get("paid_on")
    text = "Payment" + (f" of {_day_text(paid_on)}" if paid_on else "") + f" linked to {lookup.name(Invoice, new)}"
    confirmed = (changes.get("confirmed_by_payee_on") or [None, None])[1]
    if confirmed:
        text += f", confirmed by the payee {_day_text(confirmed)}"
    entry.sentence = entry.what = text
    entry.entity = entry.identity = ""


def _day_text(value) -> str:
    if isinstance(value, str):
        try:
            value = datetime.date.fromisoformat(value[:10])
        except ValueError:
            return value
    return f"{value.day} {value.strftime('%b')}"


def _values(row):
    return {f.attname: getattr(row, f.attname) for f in row._meta.concrete_fields}


def _live_quotes(ids):
    from connect_labs.supply_chain.models import Quote

    return Quote._base_manager.filter(pk__in=ids, voided=False, superseded_by__isnull=True).values_list(
        "pk", flat=True
    )


def _fold_corrections(revisions):
    """Fold `quote_correct`'s two writes into one line; return (kept, {new version revision id(): superseded id}).

    A correction creates the new version and marks the old one "replaced by a
    corrected version" in the same call. To a reader that is one event -- the
    quote was corrected -- so the update on the superseded quote is dropped and
    the new version's create line says what the correction changed.
    """
    from connect_labs.supply_chain.models import Quote

    creates = {}
    for revision in revisions:
        if (
            revision.action == "create"
            and revision.call_id is not None
            and revision.content_type.model_class() is Quote
        ):
            creates[(revision.call_id, str(revision.object_id))] = revision
    kept, folded = [], {}
    for revision in revisions:
        if (
            revision.action == "update"
            and revision.call_id is not None
            and revision.content_type.model_class() is Quote
        ):
            replacement = (revision.changes.get("superseded_by_id") or [None, None])[1]
            new = creates.get((revision.call_id, str(replacement))) if replacement is not None else None
            if new is not None:
                folded[id(new)] = revision.object_id
                continue
        kept.append(revision)
    return kept, folded


def _as_correction(entry, revision, superseded_id, lookup):
    """Rewrite a new quote version's create line as the correction it was."""
    from connect_labs.supply_chain.models import Quote

    old = lookup.row(Quote, superseded_id)
    new_values = {k: v[1] for k, v in revision.changes.items()}
    entry.sentence, entry.fields = correction_sentence(
        Quote, _values(old) if old is not None else {}, new_values, lookup
    )
    # Whose quote; "Quote" is already the sentence's first word.
    entry.subject = subject(Quote, new_values, lookup).removeprefix(model_label(Quote) + " · ")
    entry.entity = model_label(Quote)
    entry.identity = identity(Quote, new_values, lookup)
    entry.what = "corrected" + entry.sentence.removeprefix(f"{model_label(Quote)} corrected")
    entry.sender = sender(Quote, new_values, lookup)


def _timeline(revisions, until) -> list[Entry]:
    from connect_labs.supply_chain.models import Quote

    lookup = Lookup()
    revisions, suffixes = _fold_lines(_merged(list(revisions)), lookup)
    revisions, corrections = _fold_corrections(revisions)
    by_model = {}
    for revision in revisions:
        model = revision.content_type.model_class()
        # Updates name their record from the row; a quote's or a shipment's
        # create reads its source's sender from it too (`_set_line`).
        if revision.action == "update" or (
            revision.action == "create" and model is not None and model.__name__ in ("Quote", "Shipment")
        ):
            by_model.setdefault(model, set()).add(revision.object_id)
    for model, pks in by_model.items():
        if model is not None:
            lookup.prime(model, pks)
    quote_type = ContentType.objects.get_for_model(Quote)
    quote_ids = {int(r.object_id) for r in revisions if r.content_type_id == quote_type.pk}
    live = set(_live_quotes(quote_ids)) if quote_ids and until is None else set()
    entries = []
    built = []
    for revision in revisions:
        entry = entry_for(revision, lookup=lookup, offer_fixes=until is None, live_quote_ids=live, until=until)
        if id(revision) in corrections:
            _as_correction(entry, revision, corrections[id(revision)], lookup)
        # Lines come oldest first inside the call, as they were entered.
        lines = list(reversed(suffixes.get(id(revision), [])))
        if lines and entry.sentence:
            entry.sentence += " — " + "; ".join(lines)
            if entry.what:
                entry.what += " — " + "; ".join(lines)
        entries.append(entry)
        built.append((entry, revision))
    if until is None:
        _mark_holds(built)
    return [e for e in entries if e.sentence]


def _mark_holds(built):
    """Put "Waiting on us: import permit" on the line that asked for a document we still owe.

    The hold is read from holds.py, so the history and the order's banner name
    the same thing. Only the line that added the document to the shipment's
    requirements carries it, and only while the hold stands: today's fact,
    which is why a rewound history leaves it off.
    """
    from connect_labs.supply_chain.fulfilment.services.holds import holds_for
    from connect_labs.supply_chain.models import Contract, Shipment

    asked = [
        (entry, revision)
        for entry, revision in built
        if revision.action in ("create", "update")
        and "required_documents" in revision.changes
        and (revision.changes["required_documents"] or [None, None])[1]
        and revision.content_type.model_class() is Shipment
    ]
    if not asked:
        return
    contract_of = dict(
        Shipment._base_manager.filter(pk__in={int(r.object_id) for _, r in asked}).values_list("pk", "contract_id")
    )
    holds = holds_for(list(Contract._base_manager.filter(pk__in=set(contract_of.values()))))
    for entry, revision in asked:
        shipment_id = int(revision.object_id)
        waiting = {h.what for h in holds.get(contract_of.get(shipment_id), []) if h.shipment_id == shipment_id}
        if not waiting:
            continue
        old, new = revision.changes["required_documents"]
        before = {d.get("kind") for d in (old or []) if isinstance(d, dict)}
        for document in new or []:
            if not isinstance(document, dict) or document.get("kind") in before:
                continue
            what = (document.get("kind") or "").replace("_", " ")
            if what in waiting:
                # The record's own name for it, when it carries one ("Form M").
                local = str(document.get("name") or "").strip()
                entry.hold = f"Waiting on us: {what}" + (f" ({local})" if local else "")
                break


@dataclass
class EmailEvent:
    """Everything recorded from one inbound email, as one event in the history.

    A reply and the quote in it, or a reply and the questions it asked us, are
    one thing that happened -- an email arrived -- and read as two or three
    unrelated lines when listed apart, each opening the same excerpt. `head`
    is the line whose source the event shows; `members` are the records it
    produced, the reply first and then in the order they were entered.
    """

    when: object
    head: Entry
    members: list
    sender: str = ""
    is_group = True


def _is_reply(entry) -> bool:
    return "response_kind" in entry.fields or "responded" in entry.fields


def email_events(entries) -> list:
    """`entries` with every set of two or more recorded from the same email folded into an EmailEvent.

    The same email is the same source reference with an excerpt -- an email's
    Message-ID -- on whatever call recorded each line. The event stands where
    its newest line stood; a line with a source of its own stays a line.
    """
    by_ref = {}
    for entry in entries:
        if entry.source_ref and entry.excerpt:
            by_ref.setdefault(entry.source_ref, []).append(entry)
    out, placed = [], set()
    for entry in entries:
        group = by_ref.get(entry.source_ref) if entry.source_ref and entry.excerpt else None
        if not group or len(group) < 2:
            out.append(entry)
            continue
        if entry.source_ref in placed:
            continue
        placed.add(entry.source_ref)
        members = sorted(group, key=lambda e: (not _is_reply(e), e.when))
        sender = next((e.sender for e in members if e.sender), "")
        out.append(EmailEvent(when=group[0].when, head=members[0], members=members, sender=sender))
    return out


def timeline_for_tender(tender_id, *, program_id, until=None) -> list[Entry]:
    return _timeline(tender_scope_revisions(tender_id, program_id=program_id, until=until), until)


def timeline_for_contract(contract_id, *, program_id, until=None) -> list[Entry]:
    return _timeline(contract_scope_revisions(contract_id, program_id=program_id, until=until), until)


def ai_entered_quotes(quote_ids, *, program_id) -> dict:
    """{quote id: who told us} for the quotes whose version was entered through an AI.

    Read from each quote's create revision, the same rule as the timeline's
    and the overview's AI pill (`labels.is_ai`). A corrected quote is a new
    version with its own create: a correction made over MCP marks the version
    it wrote. One query.
    """
    from connect_labs.supply_chain.models import Quote

    ids = sorted({int(pk) for pk in quote_ids if pk is not None})
    if not ids:
        return {}
    creates = Revision.objects.filter(
        _type_q(Quote), action="create", object_id__in=[str(pk) for pk in ids], program_id=program_id
    ).select_related("call__actor")
    lookup = Lookup()
    return {int(r.object_id): actor_label(r.call, lookup) for r in creates if is_ai(r.call)}


def corrections_for_quotes(quote_ids, *, program_id, until=None) -> dict:
    """{quote id: {"when", "changes", "actor", "filled_gap", ...}} for the quotes a correction made.

    What the timeline's correction line says ("sachets per carton 150 (was
    not stated)"), so a comparison row can show why an offer that
    was blocked has joined the ranking. Read from each version's create
    revision and the `quote_correct` call that wrote it. Three queries.
    """
    from connect_labs.supply_chain.models import Quote

    ids = sorted({int(pk) for pk in quote_ids if pk is not None})
    if not ids:
        return {}
    creates = Revision.objects.filter(
        _type_q(Quote),
        action="create",
        object_id__in=[str(pk) for pk in ids],
        program_id=program_id,
        call__operation="quote_correct",
    ).select_related("call__actor")
    if until is not None:
        creates = creates.filter(recorded_at__lte=end_of_day(until))
    creates = list(creates)
    if not creates:
        return {}
    old = {
        row.superseded_by_id: _values(row)
        for row in Quote._base_manager.filter(superseded_by_id__in=[int(r.object_id) for r in creates])
    }
    lookup = Lookup()
    out = {}
    for revision in creates:
        quote_id = int(revision.object_id)
        new_values = {k: v[1] for k, v in revision.changes.items()}
        text, fields = correction_sentence(Quote, old.get(quote_id, {}), new_values, lookup)
        _, _, changes = text.partition(": ")
        call = revision.call
        merged = {**old.get(quote_id, {}), **new_values}
        out[quote_id] = {
            "when": revision.recorded_at,
            "changes": changes,
            "actor": actor_label(call, lookup),
            "is_ai": is_ai(call),
            # The reply the correction rested on, so the row that shows the
            # correction can open on its evidence.
            "excerpt": getattr(call, "source_excerpt", "") or "",
            "source_kind": source_kind(getattr(call, "source_ref", "")),
            "recorded_on": getattr(call, "recorded_at", None) or revision.recorded_at,
            # Who the reply came from: the quote's supplier.
            "sender": sender(Quote, merged, lookup),
            # The figures the correction supplied, as the specification names
            # them ("sachets per carton"), so the comparison can say where each
            # came from rather than "stated on the quote".
            "labels": [
                quote_field_label(attname, merged, lookup).lower()
                for attname in fields
                if attname != "pack_spec_source"
            ],
            # The correction answered a fact the quote had left blank ("sachets
            # per carton 150 (was not stated)") -- the answer that moved a blocked
            # quote into the ranking -- rather than revising a figure it already
            # had. Only then does the ranked row say the quote joined the ranking.
            "filled_gap": any(
                old.get(quote_id, {}).get(attname) in (None, "") for attname in fields if attname != "pack_spec_source"
            ),
        }
    return out


def reminders_for_outreach(outreach_ids, *, program_id, until=None) -> dict:
    """{outreach id: how many reminders went} for the outreach table's "2nd reminder".

    There is no counter on the record: a reminder is a day `last_reminder_on`
    was set to, so the count is the distinct days the history shows it set to
    (a repeated write of the same day is one reminder). One query for every
    row. A reminder that predates the history is not here -- the caller floors
    the count at 1 when the row carries a `last_reminder_on`.
    """
    from connect_labs.supply_chain.models import Outreach

    ids = sorted({int(pk) for pk in outreach_ids if pk is not None})
    if not ids:
        return {}
    revisions = Revision.objects.filter(
        _type_q(Outreach),
        action__in=("create", "update"),
        object_id__in=[str(pk) for pk in ids],
        program_id=program_id,
        changes__has_key="last_reminder_on",
    )
    if until is not None:
        revisions = revisions.filter(recorded_at__lte=end_of_day(until))
    days = {}
    for object_id, changes in revisions.values_list("object_id", "changes"):
        change = changes.get("last_reminder_on")
        new = change[1] if isinstance(change, (list, tuple)) and len(change) == 2 else None
        if new:
            days.setdefault(int(object_id), set()).add(str(new)[:10])
    return {pk: len(found) for pk, found in days.items()}


def answered_by(commitment_ids, *, program_id) -> dict:
    """{commitment id: who answered it}, as the timeline names them ("Sophie Bello", "via AI · Sophie").

    Read from the revision that wrote its resolution; the latest one, should
    it have been answered twice. One query.
    """
    from connect_labs.supply_chain.models import Commitment

    ids = sorted({int(pk) for pk in commitment_ids if pk is not None})
    if not ids:
        return {}
    revisions = (
        Revision.objects.filter(
            _type_q(Commitment),
            action="update",
            object_id__in=[str(pk) for pk in ids],
            program_id=program_id,
            changes__has_key="resolution",
        )
        .select_related("call__actor")
        .order_by("recorded_at", "id")
    )
    lookup = Lookup()
    return {int(r.object_id): actor_label(r.call, lookup) for r in revisions}
