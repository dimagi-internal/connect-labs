"""Tier 1 — GS1 EPCIS 2.0 capture.

The ISO/IEC 19987 visibility-event format, as JSON-LD. This is what a supplier
with a real traceability system already emits, so we accept their documents
rather than asking them to reshape anything.
"""
from ... import gs1  # noqa: F401  (kept for identifier helpers used by callers)
from ...models import SupplyEvent
from ..org_actions import ActionError
from ._core import capture_event, normalise_biz_step, parse_event_time, resolve_node

def capture_epcis_document(org, document):
    """Capture an EPCIS 2.0 ``EPCISDocument`` (or a bare event list).

    Returns ``{"captured": n, "duplicates": n, "event_ids": [...]}``.
    """
    if not isinstance(document, dict):
        raise ActionError("body must be an EPCIS document object")

    body = document.get("epcisBody") or {}
    events = body.get("eventList")
    if events is None:
        events = document.get("eventList") or ([document] if document.get("type") else None)
    if not events:
        raise ActionError("no events found in document")

    captured, duplicates, ids = 0, 0, []
    for raw_event in events:
        event, created = _capture_epcis_event(org, raw_event)
        ids.append(event.id)
        if created:
            captured += 1
        else:
            duplicates += 1
    return {"captured": captured, "duplicates": duplicates, "event_ids": ids}


EPCIS_TYPE_MAP = {
    "ObjectEvent": SupplyEvent.EventType.OBJECT,
    "AggregationEvent": SupplyEvent.EventType.AGGREGATION,
    "TransformationEvent": SupplyEvent.EventType.TRANSFORMATION,
}


def _capture_epcis_event(org, raw_event):
    if not isinstance(raw_event, dict):
        raise ActionError("each event must be an object")

    event_type = EPCIS_TYPE_MAP.get(raw_event.get("type"), SupplyEvent.EventType.OBJECT)
    biz_step = normalise_biz_step(raw_event.get("bizStep"))
    event_time = parse_event_time(raw_event.get("eventTime"))
    read_point = resolve_node(raw_event.get("readPoint") or raw_event.get("bizLocation"))

    transactions = {}
    for entry in raw_event.get("bizTransactionList") or []:
        if isinstance(entry, dict) and entry.get("type"):
            transactions[str(entry["type"]).rsplit(":", 1)[-1]] = entry.get("bizTransaction")

    epc_list = raw_event.get("epcList") or raw_event.get("outputEPCList") or []
    quantity_list = raw_event.get("quantityList") or raw_event.get("outputQuantityList") or []

    return capture_event(
        org,
        event_type=event_type,
        biz_step=biz_step,
        event_time=event_time,
        read_point=read_point,
        epc_list=epc_list,
        quantity_list=quantity_list,
        biz_transactions=transactions,
        disposition=str(raw_event.get("disposition") or "").rsplit(":", 1)[-1],
        source_tier=SupplyEvent.SourceTier.EPCIS,
        external_id=raw_event.get("eventID") or "",
        raw=raw_event,
    )
