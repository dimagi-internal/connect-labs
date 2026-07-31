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

from django.db.models import F, Sum

from .. import gs1
from ..models import CaseloadEstimate, ChildOutcome, DistributionRecord, Shipment


def _delivered_cartons_by_district(country=None):
    """Cartons confirmed delivered INTO each district, from outside it.

    A consignment moves in hops, and each hop is its own Shipment. Summing
    every arrival in a district therefore counts the same cartons twice: once
    when they reach the district hub from the plant, and again when the hub
    despatches them onward to the feeding sites it serves. Borno read 24,675
    against 15,000 that ever crossed its boundary, because 9,675 of onward
    distribution *within* Borno was added to the cartons that arrived.

    Redistribution inside a district is not new supply reaching it. So a leg
    counts only when it crosses a district boundary, which is what "delivered
    into this district" means and is the only reading that lets coverage be
    compared between districts at all.
    """
    qs = (
        Shipment.objects.filter(
            status__in=[Shipment.Status.DELIVERED, Shipment.Status.CONFIRMED],
            unit="cartons",
        )
        .exclude(destination__adm1_code="")
        .exclude(origin__adm1_code=F("destination__adm1_code"))
    )
    if country:
        qs = qs.filter(destination__country=country)
    rows = qs.values("destination__adm1_code").annotate(cartons=Sum("quantity"))
    return {r["destination__adm1_code"]: int(r["cartons"] or 0) for r in rows}


def _dispensed_cartons_by_district(country=None):
    """Cartons a site actually HANDED OUT, per district.

    The companion the coverage figure never had. ``_delivered_cartons_by_district``
    counts what crossed into a district and was confirmed there — which includes
    everything sitting in a district hub, undistributed. On the seeded world that
    is 54,255 of the 58,251 courses coverage divides by: **93%**. Four of five
    districts showed substantial "coverage of need" with literally zero cartons
    dispensed, Gombe and Kassala both reading 91%.

    That is a real and useful number — supply positioned against need is exactly
    what a control tower wants to know — but it is not the number the words
    "coverage of need" promise, and a funder reading the two together has no way
    to tell them apart unless both are on the screen.
    """
    from ..models import DistributionRecord

    qs = DistributionRecord.objects.select_related("site")
    if country:
        qs = qs.filter(site__country=country)
    out = {}
    for record in qs.exclude(site__adm1_code=""):
        code = record.site.adm1_code
        out[code] = out.get(code, 0) + float(record.cartons_dispensed or 0)
    return {k: int(v) for k, v in out.items()}


def _requirement_by_district(country=None, month=None):
    """Children needing treatment per district, summed over the response window.

    **Deliveries are cumulative, so the requirement has to be too.** A contract
    delivers a season's supply in one consignment; a CaseloadEstimate is one
    month. Dividing the first by the second is a unit error that reports a
    district at 424% of need and makes the whole coverage figure worthless —
    which is exactly what it did before this function existed.

    The window is every month we hold an estimate for, up to ``month``. It is
    returned alongside the figure so the surface can state what period the
    coverage covers, because a coverage percent with no window is as
    unanswerable as one with no denominator.
    """
    month = month or date.today().replace(day=1)
    estimates = CaseloadEstimate.objects.filter(month__lte=month)
    if country:
        estimates = estimates.filter(country=country)

    per_district = {}
    for estimate in estimates.order_by("adm1_code", "month"):
        row = per_district.setdefault(
            estimate.adm1_code,
            {"latest": estimate, "requirement": 0, "months": 0, "from_month": estimate.month},
        )
        row["latest"] = estimate  # ordered by month, so the last wins
        row["requirement"] += estimate.children_sam or 0
        row["months"] += 1
        row["from_month"] = min(row["from_month"], estimate.month)
    return per_district


def coverage_by_district(country=None, month=None):
    """Coverage percent and uncovered children, per admin-1 district.

    Coverage is allowed to exceed 100%. A district CAN be over-supplied, and
    saying so is the point — that is where stock sits long enough to reach its
    expiry date, which the command centre raises as its own exception. Clamping
    the figure at 100 would hide the surplus and make two different situations
    render identically.
    """
    delivered = _delivered_cartons_by_district(country=country)
    dispensed = _dispensed_cartons_by_district(country=country)
    per_district = _requirement_by_district(country=country, month=month)

    rows = []
    for adm1_code, agg in per_district.items():
        estimate = agg["latest"]
        cartons = delivered.get(adm1_code, 0)
        courses = gs1.cartons_to_children(cartons)
        requirement = agg["requirement"]
        percent = round((courses / requirement) * 100, 1) if requirement else None
        rows.append(
            {
                "adm1_code": adm1_code,
                "adm1_name": estimate.adm1_name,
                "country": estimate.country,
                "ipc_phase": estimate.ipc_phase,
                "caseload": requirement,
                "monthly_caseload": estimate.children_sam or 0,
                "window_months": agg["months"],
                "window_from": agg["from_month"].isoformat(),
                "delivered_cartons": cartons,
                "courses_delivered": courses,
                "coverage_percent": percent,
                # What actually reached a child, so the positioned figure above
                # can never be read as this one by accident.
                "courses_dispensed": gs1.cartons_to_children(dispensed.get(adm1_code, 0)),
                "dispensed_percent": (
                    round((gs1.cartons_to_children(dispensed.get(adm1_code, 0)) / requirement) * 100, 1)
                    if requirement
                    else None
                ),
                "uncovered_children": max(requirement - courses, 0),
                "surplus_children": max(courses - requirement, 0),
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
            {
                "country": row["country"],
                "caseload": 0,
                "courses_delivered": 0,
                "courses_dispensed": 0,
                "districts": 0,
            },
        )
        bucket["caseload"] += row["caseload"]
        bucket["courses_delivered"] += row["courses_delivered"]
        bucket["courses_dispensed"] += row["courses_dispensed"]
        bucket["districts"] += 1
        bucket["window_months"] = max(bucket.get("window_months", 0), row["window_months"])
    out = []
    for bucket in per_country.values():
        caseload = bucket["caseload"]
        bucket["coverage_percent"] = round((bucket["courses_delivered"] / caseload) * 100, 1) if caseload else None
        bucket["uncovered_children"] = max(caseload - bucket["courses_delivered"], 0)
        bucket["surplus_children"] = max(bucket["courses_delivered"] - caseload, 0)
        bucket["dispensed_percent"] = round((bucket["courses_dispensed"] / caseload) * 100, 1) if caseload else None
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

    # A recovery rate is computed over DISCHARGED children.
    #
    # Once children still mid-course were correctly recorded as in treatment
    # rather than given a fabricated completed course, they landed in this
    # denominator and the observed recovery rate fell from the high seventies
    # to 47.8% — reporting a normally-performing programme as a failing one
    # for the crime of having recently admitted anybody. Sphere defines
    # recovery, default and death rates on exits from the programme.
    discharged = outcomes.exclude(discharge_status=ChildOutcome.Discharge.IN_TREATMENT)
    total_observed = discharged.count()
    still_in_treatment = outcomes.filter(discharge_status=ChildOutcome.Discharge.IN_TREATMENT).count()
    recovered = discharged.filter(discharge_status=ChildOutcome.Discharge.RECOVERED).count()
    breakdown = {
        status: outcomes.filter(discharge_status=status).count() for status, _label in ChildOutcome.Discharge.choices
    }

    return {
        "courses_delivered": courses,
        "courses_method": (
            "Every carton that crossed into a district, from any source — including "
            "stock imported outside an OES supply contract — at one carton per child's "
            "full course. Wider than the contract ladder above, and deliberately not "
            "reconciled with it. This is arithmetic on the supply record — it says "
            "nothing about treatment."
        ),
        "children_observed": total_observed,
        "children_recovered": recovered,
        "children_in_treatment": still_in_treatment,
        "recovery_method": (
            "Children with a recorded discharge as recovered, out of those who have "
            "been discharged at all, from measurement series captured at the point of "
            "treatment. Children still mid-course are not counted on either side of "
            "the ratio — a rate over exits is what the Sphere thresholds are defined "
            "on. Synthetic in this environment."
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
