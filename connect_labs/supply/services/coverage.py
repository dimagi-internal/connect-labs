"""Coverage: delivery measured against need, not counted.

Volume delivered cannot answer the question a health ministry has. A large
delivery into a large caseload and a small delivery into a small one are
identical in tonnes; with the caseload as the denominator they are ninety-one
percent and thirty-four percent of need, which is a different conversation and
the one that justifies the next appropriation.

The same rows feed the government view (per district, scoped to one country)
and the funder view (rolled up per country), so the two cannot report different
coverage for the same geography.
"""
from datetime import date

from django.db.models import Sum

from .. import gs1
from ..models import CaseloadEstimate, ChildOutcome, DistributionRecord, Shipment


def _delivered_cartons_by_district(country=None):
    """Cartons confirmed delivered into each district."""
    qs = Shipment.objects.filter(
        status__in=[Shipment.Status.DELIVERED, Shipment.Status.CONFIRMED],
        unit="cartons",
    ).exclude(destination__adm1_code="")
    if country:
        qs = qs.filter(destination__country=country)
    rows = qs.values("destination__adm1_code").annotate(cartons=Sum("quantity"))
    return {r["destination__adm1_code"]: int(r["cartons"] or 0) for r in rows}


def coverage_by_district(country=None, month=None):
    """Coverage percent and uncovered children, per admin-1 district."""
    month = month or date.today().replace(day=1)
    delivered = _delivered_cartons_by_district(country=country)

    estimates = CaseloadEstimate.objects.filter(month__lte=month)
    if country:
        estimates = estimates.filter(country=country)

    # One row per district: the most recent estimate at or before the month.
    latest = {}
    for estimate in estimates.order_by("adm1_code", "month"):
        latest[estimate.adm1_code] = estimate

    rows = []
    for adm1_code, estimate in latest.items():
        cartons = delivered.get(adm1_code, 0)
        courses = gs1.cartons_to_children(cartons)
        caseload = estimate.children_sam or 0
        percent = round((courses / caseload) * 100, 1) if caseload else None
        rows.append(
            {
                "adm1_code": adm1_code,
                "adm1_name": estimate.adm1_name,
                "country": estimate.country,
                "ipc_phase": estimate.ipc_phase,
                "caseload": caseload,
                "delivered_cartons": cartons,
                "courses_delivered": courses,
                "coverage_percent": percent,
                "uncovered_children": max(caseload - courses, 0),
                "source_note": estimate.source_note,
            }
        )
    return sorted(rows, key=lambda r: (r["coverage_percent"] is None, r["coverage_percent"] or 0))


def coverage_by_country(month=None):
    """The same figures rolled up per country, for the funder view."""
    per_country = {}
    for row in coverage_by_district(month=month):
        bucket = per_country.setdefault(
            row["country"],
            {"country": row["country"], "caseload": 0, "courses_delivered": 0, "districts": 0},
        )
        bucket["caseload"] += row["caseload"]
        bucket["courses_delivered"] += row["courses_delivered"]
        bucket["districts"] += 1
    out = []
    for bucket in per_country.values():
        caseload = bucket["caseload"]
        bucket["coverage_percent"] = round((bucket["courses_delivered"] / caseload) * 100, 1) if caseload else None
        bucket["uncovered_children"] = max(caseload - bucket["courses_delivered"], 0)
        out.append(bucket)
    return sorted(out, key=lambda r: r["coverage_percent"] or 0)


def courses_versus_recoveries(country=None):
    """The two figures that do not agree, and the gap between them.

    "Children treated" in almost every report is cartons divided by a treatment
    factor — arithmetic presented as an outcome. Beside it here sits the number
    of children with a *recorded* recovery, built from measurements taken at the
    point of treatment.

    They disagree, and the gap is the most useful thing on the screen: not every
    child admitted on a batch completes treatment. Reporting one number without
    the other is the difference between what was shipped and what is known to
    have worked.
    """
    delivered = _delivered_cartons_by_district(country=country)
    courses = gs1.cartons_to_children(sum(delivered.values()))

    outcomes = ChildOutcome.objects.all()
    records = DistributionRecord.objects.all()
    if country:
        outcomes = outcomes.filter(site__country=country)
        records = records.filter(site__country=country)

    total_observed = outcomes.count()
    recovered = outcomes.filter(discharge_status=ChildOutcome.Discharge.RECOVERED).count()
    breakdown = {
        status: outcomes.filter(discharge_status=status).count() for status, _label in ChildOutcome.Discharge.choices
    }

    return {
        "courses_delivered": courses,
        "courses_method": (
            "Cartons confirmed delivered, at one carton per child's full course. "
            "This is arithmetic on the supply record — it says nothing about treatment."
        ),
        "children_observed": total_observed,
        "children_recovered": recovered,
        "recovery_method": (
            "Children with a recorded discharge as recovered, from measurement "
            "series captured at the point of treatment. Synthetic in this environment."
        ),
        "observed_recovery_rate": round((recovered / total_observed) * 100, 1) if total_observed else None,
        "discharge_breakdown": breakdown,
        "distributions_recorded": records.count(),
        # The honest framing: outcomes are observed on a sample of the children
        # a batch fed, so the gap is reported as a rate applied to the courses
        # delivered, never as a raw subtraction of two differently-sized things.
        "gap_note": (
            "Outcomes are observed on a sample of the children each batch fed, not on "
            "every course delivered. The two figures are reported side by side with "
            "their methods rather than reconciled into one."
        ),
    }
