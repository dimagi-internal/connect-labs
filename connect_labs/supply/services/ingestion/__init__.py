"""Ingestion: how awarded suppliers report what actually moved.

Three tiers, matching the real capability gradient of a humanitarian supply
chain rather than assuming everyone has telemetry:

* :mod:`.epcis`  — GS1 EPCIS 2.0 documents from a supplier running a
  traceability system (our Kano plant).
* :mod:`.asn`    — a despatch advice, the JSON shape any EDI translation layer
  emits from an X12 856 / EDIFACT DESADV.
* :mod:`.manual` — check-ins and typed forms, for corridors where nothing more
  is realistic (Port Sudan to El Fasher).

All three converge on :func:`~._core.capture_event`, which owns the derived
state. Callers import from this package, not from the submodules.
"""
from ._core import (
    capture_event,
    node_by_gln,
    normalise_biz_step,
    parse_event_time,
    resolve_node,
)
from .asn import capture_despatch_advice
from .epcis import capture_epcis_document
from .manual import capture_checkin, confirm_delivery, record_manual_event

__all__ = [
    # core
    "capture_event",
    "normalise_biz_step",
    "parse_event_time",
    "resolve_node",
    "node_by_gln",
    # tiers
    "capture_epcis_document",
    "capture_despatch_advice",
    "capture_checkin",
    "record_manual_event",
    "confirm_delivery",
]
