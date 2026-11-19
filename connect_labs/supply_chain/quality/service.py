"""Record, list and withdraw quality tests. The operations call these; nothing else writes a test."""

from datetime import date

from django.db import transaction
from django.db.models import Q

from connect_labs.supply_chain import records
from connect_labs.supply_chain.models import QualityTest
from connect_labs.supply_chain.quality import judge


def _day(value):
    if value in (None, ""):
        return None
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def _points_of_partner(program_id, partner_org_id) -> list[int]:
    """The places a partner runs: its own stores and the points below them (stock_issue's rule)."""
    from connect_labs.supply_chain.stock.services.issues import partner_by_point

    return [pk for pk, org in partner_by_point(program_id).items() if org == partner_org_id]


def list_tests(
    access,
    *,
    supply_point_id=None,
    partner_org_id=None,
    kind=None,
    result=None,
    batch=None,
    since=None,
    until=None,
    include_withdrawn=False,
    limit=500,
):
    program_id = access._require_program()
    qs = QualityTest.objects.filter(program_id=program_id).select_related("supply_point", "commodity", "item")
    if supply_point_id is not None:
        qs = qs.filter(supply_point_id=supply_point_id)
    if partner_org_id is not None:
        qs = qs.filter(supply_point_id__in=_points_of_partner(program_id, partner_org_id))
    if kind:
        qs = qs.filter(kind=kind)
    if result:
        qs = qs.filter(result=result)
    if batch:
        qs = qs.filter(batch__iexact=batch.strip())
    if since:
        qs = qs.filter(tested_on__gte=_day(since))
    if until:
        qs = qs.filter(tested_on__lte=_day(until))
    if not include_withdrawn:
        qs = qs.filter(withdrawn_on__isnull=True)
    return list(qs[:limit])


@transaction.atomic
def record_test(access, data):
    """Save one test with its result. Refuses what cannot be a test rather than storing half of one."""
    from connect_labs.supply_chain.data_access import _fresh

    program_id = access._require_program()
    kind = data["kind"]
    point = access._require_supply_point(data["supply_point_id"])
    tested_on = _day(data["tested_on"])
    if tested_on > date.today():
        raise ValueError("tested_on is in the future: record a test once it has been done")
    item = access._resolve_item(data.get("item_id"))
    commodity = access._require_commodity(data["commodity_slug"]) if data.get("commodity_slug") else None
    if item is not None and commodity is None:
        commodity = item.commodity
    if item is not None and commodity is not None and item.commodity_id != commodity.pk:
        raise ValueError(f"item {item.pk} is {item.commodity.name}, not {commodity.name}")
    batch = (data.get("batch") or "").strip()
    dispenser = (data.get("dispenser") or "").strip()
    collected_on = _day(data.get("collected_on"))
    readings, trials = judge.readings_from(data.get("readings"), data.get("trials"))
    storage = {name: bool(value) for name, value in (data.get("storage") or {}).items() if value is not None}

    if kind in ("batch", "dispenser_sample"):
        if commodity is None:
            raise ValueError(
                "a batch or dispenser sample test names the product it tested (commodity_slug or item_id)"
            )
        if not readings:
            raise ValueError("no readings: give what was measured (readings, or trials to average)")
        if storage:
            raise ValueError("storage answers belong on a storage check, not a sample test")
    if kind == "batch" and not batch:
        raise ValueError("a batch test names the batch it sampled")
    if kind == "dispenser_sample":
        if not dispenser:
            raise ValueError("a dispenser sample names the dispenser (or water point) it came from")
        if collected_on is not None and collected_on > tested_on:
            raise ValueError("collected_on is after tested_on: a sample is collected before it is tested")
    if kind == "storage":
        if not storage:
            raise ValueError("a storage check answers at least one of: " + ", ".join(records.STORAGE_CHECKS))
        if readings or trials:
            raise ValueError("readings belong on a batch or dispenser sample test, not a storage check")

    result, findings = judge.judge(kind, commodity, readings, storage)
    test = QualityTest.objects.create(
        program_id=program_id,
        opportunity_id=data.get("opportunity_id"),
        kind=kind,
        supply_point=point,
        tested_on=tested_on,
        tested_by=data["tested_by"].strip(),
        commodity=commodity,
        item=item,
        batch=batch,
        sample_ref=(data.get("sample_ref") or "").strip(),
        dispenser=dispenser,
        collected_by=(data.get("collected_by") or "").strip(),
        collected_on=collected_on,
        readings=readings,
        trials=trials,
        storage=storage,
        result=result,
        findings=findings,
        source=data["source"],
        recorded_by_org_id=data.get("recorded_by_org_id"),
        note=(data.get("note") or "").strip(),
    )
    return _fresh(test)


@transaction.atomic
def withdraw_test(access, test_id, reason):
    """Take a test out of every figure without deleting it: it stays listed, marked withdrawn."""
    from connect_labs.supply_chain.data_access import _fresh
    from connect_labs.supply_chain.identity import person_name

    test = QualityTest.objects.select_for_update().filter(program_id=access._require_program(), pk=test_id).first()
    if test is None:
        raise ValueError(f"test {test_id} does not exist in this program")
    if test.is_withdrawn:
        raise ValueError(f"test {test_id} was already withdrawn on {test.withdrawn_on.isoformat()}")
    user = access.user if getattr(access.user, "is_authenticated", False) else None
    test.withdrawn_on = date.today()
    test.withdrawn_by = person_name(user)[:255]
    test.withdrawn_reason = reason.strip()
    test.save()
    return _fresh(test)


def latest_standing(program_id, *, as_of=None, opportunity_id=None) -> list[QualityTest]:
    """The newest test still standing for each thing tested: a batch, a dispenser, a store.

    What a failed test means is decided by the newest one: a batch that failed and was retested
    and passed is fine, and one that passed and then failed is not.
    """
    qs = QualityTest.objects.filter(program_id=program_id, withdrawn_on__isnull=True).select_related(
        "supply_point", "commodity"
    )
    if as_of is not None:
        qs = qs.filter(tested_on__lte=as_of)
    if opportunity_id is not None:
        # A store shared by the programme names no opportunity: its tests count for every one.
        qs = qs.filter(
            Q(opportunity_id=opportunity_id)
            | Q(supply_point__opportunity_id=opportunity_id)
            | Q(opportunity_id__isnull=True, supply_point__opportunity_id__isnull=True)
        )
    newest: dict = {}
    for test in qs.order_by("tested_on", "id"):
        newest[subject_key(test)] = test
    return list(newest.values())


def subject_key(test) -> tuple:
    """What a test is about: one batch of one product, one dispenser, or one store's storage."""
    if test.kind == "batch":
        return ("batch", test.commodity_id, test.batch.lower())
    if test.kind == "dispenser_sample":
        return ("dispenser_sample", test.dispenser.lower())
    return ("storage", test.supply_point_id)


def subject_label(test) -> str:
    if test.kind == "batch":
        product = test.commodity.name if test.commodity_id else "product"
        return f"Batch {test.batch} ({product}) at {test.supply_point.name}"
    if test.kind == "dispenser_sample":
        # People often type the word themselves ("Dispenser WP-04"): say it once.
        named = test.dispenser if test.dispenser.lower().startswith("dispenser") else f"Dispenser {test.dispenser}"
        return f"{named}, tested at {test.supply_point.name}"
    return f"Storage at {test.supply_point.name}"
