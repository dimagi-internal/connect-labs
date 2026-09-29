"""Which program a supply record belongs to, for every model in the app.

The as-of rewind (design doc §3.5) undoes revisions for one program only, so
every concrete model needs a way to name its program -- directly, through a
reference tier's `scope_key`, or by walking a foreign key to a record that
already knows. `PATHS` is a completeness list: every model registered under
the `supply_chain` app label is either here or in `SKIPPED_MODELS` with a
reason, and `test_every_supply_model_has_a_program_path` enforces it so a new
model can't go un-scoped by omission.
"""


def _scope(obj):
    key = getattr(obj, "scope_key", "") or ""
    return int(key.split(":", 1)[1]) if key.startswith("prog:") else None


def _via(*attrs):
    def resolve(obj):
        for attr in attrs:
            target = obj
            for part in attr.split("."):
                target = getattr(target, part, None) if target is not None else None
            if target is not None:
                return program_of(target) if not isinstance(target, int) else target
        return None

    return resolve


def _direct(obj):
    return obj.program_id


PATHS = {
    # Program-scoped tiers: the field is right there.
    "Tender": _direct,
    "Contract": _direct,
    "Document": _direct,
    "SupplyPoint": _direct,
    "Movement": _direct,
    "DispensingRule": _direct,
    "WorkerVisit": _direct,
    "StockCount": _direct,
    "Distribution": _direct,
    "Consignment": _direct,
    "AlertSubscription": _direct,
    "AlertNotice": _direct,
    "UpdateLink": _direct,
    # Reference tier: scoped by `scope_key` ("prog:<id>"), not a program_id column.
    "Commodity": _scope,
    "Item": _scope,
    "Supplier": _scope,
    # Everything else walks a foreign key to something that already knows.
    "Outreach": _via("tender"),
    "Quote": _via("tender"),
    "Award": _via("tender"),
    "AwardApproval": _via("award"),
    "Shipment": _via("contract"),
    "Invoice": _via("contract"),
    "Charge": _via("shipment"),
    "ShipmentLine": _via("shipment"),
    "Receipt": _via("contract", "shipment", "supply_point"),
    "ReceiptLine": _via("receipt"),
    "Payment": _via("invoice"),
    "DistributionLine": _via("distribution"),
    "AlertCheckState": _via("subscription"),
    # `contract` is nullable on a submission; `link` never is.
    "UpdateLinkSubmission": _via("link"),
    # Many-to-many link rows: Django's auto-created through models, named
    # `<Model>_<field>`. Each walks the FK to the side that owns the field.
    "Tender_invited_orgs": _via("tender"),
    "UpdateLink_contracts": _via("updatelink"),
    "UpdateLink_supply_points": _via("updatelink"),
    "UpdateLink_approvals": _via("updatelink"),
}

# Skipped deliberately, not by omission -- each reason says why rewinding by
# program would be wrong for that model.
SKIPPED_MODELS = {
    # Company facts, once per organisation, shared by every program it sells
    # to. See SupplierProfile's own docstring in models.py.
    "SupplierProfile": "organisation-level: one profile per company, shared across every program",
    # A supplier's own claim about what it sells, attached to the profile
    # above -- same organisation-level scope, not a program's record.
    "SupplierOffering": "organisation-level: attached to SupplierProfile, shared across every program",
    # Explicitly cross-program by design -- see Portfolio's own docstring.
    "Portfolio": "spans programs by design; a named set of program ids, not a program's own record",
}


def program_of(instance):
    resolve = PATHS.get(type(instance).__name__)
    return resolve(instance) if resolve else None
