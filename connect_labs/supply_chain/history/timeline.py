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
"""

from dataclasses import dataclass

from django.contrib.contenttypes.models import ContentType
from django.db.models import Q
from django.urls import reverse

from connect_labs.supply_chain.history.as_of import end_of_day
from connect_labs.supply_chain.history.labels import Lookup, actor_label, is_ai, sentence, subject
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


# ---- scope ---------------------------------------------------------------


def _type_q(model):
    # By name rather than by ContentType id: a through model's content type is
    # only created the first time one of its rows is captured.
    return Q(content_type__app_label=model._meta.app_label, content_type__model=model._meta.model_name)


def _child_ids(model, links, program_id=None) -> set[int]:
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
    revisions = Revision.objects.filter(_type_q(model), known)
    if program_id is not None:
        revisions = revisions.filter(program_id=program_id)
    ids |= {int(pk) for pk in revisions.values_list("object_id", flat=True)}
    return ids


def _revisions(scope, program_id, until):
    q = Q()
    for model, ids in scope:
        if ids:
            q |= _type_q(model) & Q(object_id__in=[str(pk) for pk in ids])
    revisions = Revision.objects.filter(q)
    if program_id is not None:
        revisions = revisions.filter(program_id=program_id)
    if until is not None:
        revisions = revisions.filter(recorded_at__lte=end_of_day(until))
    return revisions.select_related("call__actor", "content_type").order_by("-recorded_at", "-id")


def tender_scope_revisions(tender_id, *, program_id=None, until=None):
    """Revisions of a tender, its outreach, quotes, invitations, awards, approvals and their documents.

    Pass `program_id` from a view: the query then names its program as well
    as the tender, and a revision from any other program cannot appear.
    """
    from connect_labs.supply_chain.models import Award, AwardApproval, Document, Outreach, Quote, Tender

    tender = {int(tender_id)}
    outreach = _child_ids(Outreach, {"tender_id": tender}, program_id)
    quotes = _child_ids(Quote, {"tender_id": tender}, program_id)
    awards = _child_ids(Award, {"tender_id": tender}, program_id)
    invited = _child_ids(Tender.invited_orgs.through, {"tender_id": tender}, program_id)
    approvals = _child_ids(AwardApproval, {"award_id": awards}, program_id)
    documents = _child_ids(
        Document,
        {"tender_id": tender, "quote_id": quotes, "award_id": awards, "approval_id": approvals},
        program_id,
    )
    scope = [
        (Tender, tender),
        (Outreach, outreach),
        (Quote, quotes),
        (Award, awards),
        (Tender.invited_orgs.through, invited),
        (AwardApproval, approvals),
        (Document, documents),
    ]
    return _revisions(scope, program_id, until)


def contract_scope_revisions(contract_id, *, program_id=None, until=None):
    """Revisions of an order, its shipments and their lines and charges, receipts and
    their lines, invoices, payments, and the documents on any of them."""
    from connect_labs.supply_chain.models import (
        Charge,
        Contract,
        Document,
        Invoice,
        Payment,
        Receipt,
        ReceiptLine,
        Shipment,
        ShipmentLine,
    )

    contract = {int(contract_id)}
    shipments = _child_ids(Shipment, {"contract_id": contract}, program_id)
    charges = _child_ids(Charge, {"shipment_id": shipments}, program_id)
    shipment_lines = _child_ids(ShipmentLine, {"shipment_id": shipments}, program_id)
    receipts = _child_ids(Receipt, {"contract_id": contract, "shipment_id": shipments}, program_id)
    receipt_lines = _child_ids(ReceiptLine, {"receipt_id": receipts}, program_id)
    invoices = _child_ids(Invoice, {"contract_id": contract}, program_id)
    payments = _child_ids(Payment, {"invoice_id": invoices}, program_id)
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
    scope = [
        (Contract, contract),
        (Shipment, shipments),
        (Charge, charges),
        (ShipmentLine, shipment_lines),
        (Receipt, receipts),
        (ReceiptLine, receipt_lines),
        (Invoice, invoices),
        (Payment, payments),
        (Document, documents),
    ]
    return _revisions(scope, program_id, until)


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
    """Newest first in, newest first out, with each run of one call on one record merged."""
    merged, run = [], []
    for revision in revisions:
        key = (revision.call_id, revision.content_type_id, revision.object_id)
        if run and revision.call_id is not None and key == run[0][0]:
            run.append((key, revision))
            continue
        if run:
            merged.append(_merge([r for _, r in reversed(run)]))
        run = [(key, revision)]
    if run:
        merged.append(_merge([r for _, r in reversed(run)]))
    return merged


def entry_for(revision, *, lookup=None, offer_fixes=True, live_quote_ids=None) -> Entry:
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
        actor=actor_label(call),
        is_ai=ai,
        excerpt=getattr(call, "source_excerpt", "") or "",
        source_ref=getattr(call, "source_ref", "") or "",
    )
    if model is None:
        return entry
    if revision.action == "update":
        row = lookup.row(model, revision.object_id)
        entry.subject = subject(model, _values(row) if row is not None else None, lookup)
    if model is Quote and ai and offer_fixes and revision.action != "delete":
        quote_id = int(revision.object_id)
        if live_quote_ids is None:
            live_quote_ids = set(_live_quotes([quote_id]))
        if quote_id in live_quote_ids:
            entry.correct_url = reverse("supply_chain:procurement_quote_correct", args=[quote_id])
            entry.void_url = reverse("supply_chain:procurement_quote_void", args=[quote_id])
    return entry


def _values(row):
    return {f.attname: getattr(row, f.attname) for f in row._meta.concrete_fields}


def _live_quotes(ids):
    from connect_labs.supply_chain.models import Quote

    return Quote._base_manager.filter(pk__in=ids, voided=False, superseded_by__isnull=True).values_list(
        "pk", flat=True
    )


def _timeline(revisions, until) -> list[Entry]:
    from connect_labs.supply_chain.models import Quote

    revisions = _merged(list(revisions))
    lookup = Lookup()
    by_model = {}
    for revision in revisions:
        if revision.action == "update":
            by_model.setdefault(revision.content_type.model_class(), set()).add(revision.object_id)
    for model, pks in by_model.items():
        if model is not None:
            lookup.prime(model, pks)
    quote_type = ContentType.objects.get_for_model(Quote)
    quote_ids = {int(r.object_id) for r in revisions if r.content_type_id == quote_type.pk}
    live = set(_live_quotes(quote_ids)) if quote_ids and until is None else set()
    entries = [entry_for(r, lookup=lookup, offer_fixes=until is None, live_quote_ids=live) for r in revisions]
    return [e for e in entries if e.sentence]


def timeline_for_tender(tender_id, *, program_id=None, until=None) -> list[Entry]:
    return _timeline(tender_scope_revisions(tender_id, program_id=program_id, until=until), until)


def timeline_for_contract(contract_id, *, program_id=None, until=None) -> list[Entry]:
    return _timeline(contract_scope_revisions(contract_id, program_id=program_id, until=until), until)
