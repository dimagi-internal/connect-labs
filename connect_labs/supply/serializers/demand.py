"""Wire shapes for the demand domain.

Every figure that carries a method carries it here too. A caseload or a cover
projection whose derivation is not on the wire arrives at the browser as a bare
number, and a bare number is the thing that cannot be defended when someone
asks where it came from.
"""


def caseload_dict(estimate):
    return {
        "id": estimate.id,
        "country": estimate.country,
        "adm1_code": estimate.adm1_code,
        "adm1_name": estimate.adm1_name,
        "month": estimate.month.isoformat(),
        "ipc_phase": estimate.ipc_phase,
        "under5_population": estimate.under5_population,
        "children_sam": estimate.children_sam,
        "source_note": estimate.source_note,
    }


def distribution_plan_dict(plan, inbound_cartons=0, on_hand=0):
    """A planned distribution day, with its cover state resolved.

    ``covered`` / ``at_risk`` / ``uncovered`` is computed here rather than in
    the browser so the calendar and the cover projection cannot disagree about
    the same Tuesday.
    """
    required = float(plan.cartons_required or 0)
    # Cover is decided by what the site will actually be HOLDING on the day.
    # ``on_hand`` is the running balance at this date — opening stock, plus
    # every consignment that lands on or before it, minus what earlier
    # distributions already spent. A truck arriving on Friday does not cover
    # Tuesday, so ``inbound_cartons`` (what is still on the road AFTER this
    # date) is reported for context and deliberately does NOT count toward
    # covering this one.
    available = float(on_hand)
    if required <= 0 or available >= required:
        state = "covered"
    elif available > 0:
        state = "at_risk"
    else:
        state = "uncovered"
    return {
        "id": plan.id,
        "site_id": plan.site_id,
        "site_name": plan.site.name,
        "scheduled_for": plan.scheduled_for.isoformat(),
        "expected_children": plan.expected_children,
        "cartons_required": required,
        "cartons_inbound": float(inbound_cartons),  # still on the road AFTER this date
        "cartons_on_hand": float(on_hand),
        "state": state,
        "note": plan.note,
    }


def shortfall_signal_dict(signal):
    return {
        "id": signal.id,
        "site_id": signal.site_id,
        "site_name": signal.site.name,
        "org_name": signal.org.legal_name,
        "raised_on": signal.raised_on.isoformat(),
        "needed_by": signal.needed_by.isoformat(),
        "children_affected": signal.children_affected,
        "cartons_short": float(signal.cartons_short),
        "note": signal.note,
        "status": signal.status,
        "resolved_by_action_id": signal.resolved_by_action_id,
    }


def supply_action_dict(action):
    return {
        "id": action.id,
        "kind": action.kind,
        "kind_label": action.get_kind_display(),
        "actor": action.actor,
        "rationale": action.rationale,
        "effect": action.effect,
        "source_node_id": action.source_node_id,
        "source_node_name": action.source_node.name if action.source_node else None,
        "target_node_id": action.target_node_id,
        "target_node_name": action.target_node.name if action.target_node else None,
        "quantity": float(action.quantity) if action.quantity is not None else None,
        "shipment_id": action.shipment_id,
        "shipment_reference": action.shipment.reference if action.shipment else None,
        "created_at": action.created_at.isoformat(),
    }


def distribution_record_dict(record, include_outcomes=False):
    data = {
        "id": record.id,
        "site_id": record.site_id,
        "site_name": record.site.name,
        "org_name": record.org.legal_name,
        "distributed_on": record.distributed_on.isoformat(),
        "cartons_dispensed": float(record.cartons_dispensed),
        "children_served": record.children_served,
        "batch_lot": record.batch_lot,
        "shipment_line_id": record.shipment_line_id,
        "shipment_reference": (record.shipment_line.shipment.reference if record.shipment_line_id else None),
        # Every surface rendering the last mile says so, in the payload rather
        # than only in a template, so no consumer can drop the label.
        "synthetic": True,
    }
    if include_outcomes:
        data["outcomes"] = [child_outcome_dict(c) for c in record.child_outcomes.all()]
    return data


def child_outcome_dict(child):
    return {
        "id": child.id,
        "anon_id": child.anon_id,
        "site_id": child.site_id,
        "site_name": child.site.name,
        "batch_lot": child.batch_lot,
        "admitted_on": child.admitted_on.isoformat(),
        "admission_muac_mm": child.admission_muac_mm,
        "latest_muac_mm": child.latest_muac_mm,
        "measurements": child.measurements,
        "discharge_status": child.discharge_status,
        "discharge_label": child.get_discharge_status_display(),
        "discharged_on": child.discharged_on.isoformat() if child.discharged_on else None,
        "synthetic": True,
    }
