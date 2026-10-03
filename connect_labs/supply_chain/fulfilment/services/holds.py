"""What an order is held on that WE owe, so a delay of ours never reads as the supplier's.

The rehearsal's trucks sat at the border for a Form M the consignee -- us --
had not provided, and three screens blamed the supplier for it: the overview
said "waiting on arrival", the order said "30 days past the promised lead
time" and addressed the check to the supplier. Who has the next move is a
fact the records hold, so it is read here once and every screen uses it
(docs/superpowers/specs/2026-10-02-supply-tracking-reality.md, ruling 5):

  - a document a not-yet-received shipment requires, not on file, and owed by
    our side of the order -- the buyer's organisation when we are the buyer
    of record or an agency buys for us (a partner buyer is the partner's to
    answer, and stays theirs);
  - an open promise of ours on the order (`Commitment`, kind "promise").

A hold names what is owed, to whom it is owed when known, and since when.
"""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Hold:
    what: str  # "import permit", or the promise as made
    since: date | None
    owed_to: str = ""  # who is waiting, when the record says
    shipment_id: int | None = None
    commitment_id: int | None = None
    # The document's own name where the record gives one ("Form M" for a
    # Nigerian import permit): carried on the shipment's requirement, never guessed.
    name: str = ""
    # The document kind a held shipment requires ("import_permit"), so the
    # screen can offer to attach exactly that document and clear the hold.
    kind: str = ""
    # For a held document: who asked us for it -- the sender of the email that
    # set the requirement (a forwarder clearing the goods), else the carrier.
    # Kept apart from `owed_to`: the document goes to customs, through them.
    asked_by: str = ""

    def as_dict(self) -> dict:
        return {
            "what": self.what,
            "name": self.name,
            "since": self.since.isoformat() if self.since else None,
            "owed_to": self.owed_to,
            "shipment_id": self.shipment_id,
            "commitment_id": self.commitment_id,
            "kind": self.kind,
            "asked_by": self.asked_by,
        }

    @property
    def words(self) -> str:
        """ "import permit", or "Form M (owed to Crescent Freight)" -- the thing, and who waits for it."""
        return f"{self.what} (owed to {self.owed_to})" if self.owed_to else self.what


def our_org_ids(contract) -> set:
    """The organisations whose debts are ours on this order.

    The buyer's organisation, unless a partner bought it (then a document it
    owes is the partner's to answer, as checks._audience_for_org has it).
    """
    if contract.buyer_of_record == "partner_org" or contract.buyer_org_id is None:
        return set()
    return {contract.buyer_org_id}


def holds_on_us(contract) -> list[Hold]:
    """Everything this order is waiting on us for, oldest first."""
    return holds_for([contract]).get(contract.pk, [])


def holds_for(contracts) -> dict[int, list[Hold]]:
    """`holds_on_us` for many orders in three queries, for the overview's rows."""
    from connect_labs.supply_chain.models import Commitment, Receipt, Shipment

    by_id = {c.pk: c for c in contracts}
    if not by_id:
        return {}
    received = set(
        Receipt.objects.filter(shipment__contract_id__in=by_id, shipment__isnull=False).values_list(
            "shipment_id", flat=True
        )
    )
    holds: dict[int, list[Hold]] = {}
    shipments = (
        Shipment.objects.filter(contract_id__in=by_id)
        .exclude(status__in=("delivered", "lost"))
        .exclude(required_documents=[])
        .prefetch_related("documents")
    )
    asked_by = _documents_asked_by(shipments)
    for shipment in shipments:
        ours = our_org_ids(by_id[shipment.contract_id])
        if not ours or shipment.pk in received:
            continue
        on_file = {d.kind for d in shipment.documents.all()}
        for entry in shipment.required_documents or []:
            if entry.get("owed_by_org_id") in ours and entry.get("kind") not in on_file:
                holds.setdefault(shipment.contract_id, []).append(
                    Hold(
                        what=(entry.get("kind") or "a document").replace("_", " "),
                        # No record says when the hold began, and the day it was
                        # recorded is not that day: left unknown, not guessed.
                        since=None,
                        # Who asked for it: the sender of the email that set
                        # the requirement (a forwarder), else the carrier.
                        asked_by=asked_by.get(shipment.pk) or (shipment.carrier or "").strip(),
                        shipment_id=shipment.pk,
                        name=str(entry.get("name") or "").strip(),
                        kind=str(entry.get("kind") or ""),
                    )
                )
    for promise in Commitment.objects.filter(contract_id__in=by_id, resolved_on__isnull=True).select_related(
        "owed_to_org"
    ):
        holds.setdefault(promise.contract_id, []).append(
            Hold(
                what=promise.text if promise.kind == "promise" else f"an answer: {promise.text}",
                since=promise.raised_on,
                owed_to=promise.owed_to_org.name,
                commitment_id=promise.pk,
            )
        )
    return {cid: sorted(found, key=lambda h: (h.since or date.max, h.what)) for cid, found in holds.items()}


def _documents_asked_by(shipments) -> dict[int, str]:
    """Shipment id -> who asked for its required documents, from the history.

    The sender named on the newest recorded change to a shipment's required
    documents (`OperationCall.source_sender`, e.g. the forwarder whose email
    said the trucks are held). Absent when no change carried a sender.
    """
    from django.contrib.contenttypes.models import ContentType

    from connect_labs.supply_chain.history.models import Revision
    from connect_labs.supply_chain.models import Shipment

    ids = [str(s.pk) for s in shipments]
    if not ids:
        return {}
    found: dict[int, str] = {}
    revisions = (
        Revision.objects.filter(
            content_type=ContentType.objects.get_for_model(Shipment),
            object_id__in=ids,
            changes__has_key="required_documents",
        )
        .select_related("call")
        .order_by("recorded_at")
    )
    for revision in revisions:
        sender = (getattr(revision.call, "source_sender", "") or "").strip()
        if sender:
            found[int(revision.object_id)] = sender
    return found
