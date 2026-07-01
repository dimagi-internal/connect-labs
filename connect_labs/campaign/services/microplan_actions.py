from connect_labs.campaign.models import Microplan


def _int(value, default=0):
    """Coerce a possibly-missing/blank/garbage form value to int, preserving the
    ``int(value or default)`` semantics but falling back to ``default`` instead of
    raising on a non-numeric string — a malformed field shouldn't 500 the write."""
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _objective(target, goal_pct):
    return round((target or 0) * (goal_pct or 95) / 100)


def _ptd(budget, campaign):
    return round((budget or 0) * campaign.days_elapsed / campaign.days_total) if campaign.days_total else 0


def create_microplan(campaign, data, owner_name) -> Microplan:
    roles = [
        {
            "roleId": r.get("roleId"),
            "role": r.get("role"),
            "rate": _int(r.get("rate")),
            "planned": _int(r.get("planned")),
            "actual": 0,
        }
        for r in (data.get("roles") or [])
    ]
    target = _int(data.get("target"))
    goal = _int(data.get("goalPct"), 95)
    budget = _int(data.get("budget"))
    n = campaign.microplans.count()
    return Microplan.objects.create(
        campaign=campaign,
        microplan_id=f"MP-{200 + n}",
        region_id=data.get("regionId") or "",
        region=data.get("region") or "",
        lga=data.get("lga") or "",
        settlements=_int(data.get("settlements")),
        wards=_int(data.get("wards")),
        planned_wf=sum(r["planned"] for r in roles),
        actual_wf=0,
        roles=roles,
        budget=budget,
        spent=0,
        planned_to_date=_ptd(budget, campaign),
        target=target,
        objective=_objective(target, goal),
        goal_pct=goal,
        reached=0,
        doses=_int(data.get("doses")),
        doses_used=0,
        cold_boxes=_int(data.get("coldBoxes")),
        vehicles=_int(data.get("vehicles")),
        status="Planned",
        owner=owner_name,
        updated="Jun 4, 2026",
    )


def update_microplan(mp, data) -> Microplan:
    existing_actual = {r.get("roleId"): r.get("actual", 0) for r in (mp.roles or [])}
    roles = [
        {
            "roleId": r.get("roleId"),
            "role": r.get("role"),
            "rate": _int(r.get("rate")),
            "planned": _int(r.get("planned")),
            "actual": existing_actual.get(r.get("roleId"), 0),
        }
        for r in (data.get("roles") or [])
    ]
    target = _int(data.get("target"))
    goal = _int(data.get("goalPct"), 95)
    mp.region_id = data.get("regionId") or mp.region_id
    mp.region = data.get("region") or mp.region
    mp.lga = data.get("lga") or mp.lga
    mp.settlements = _int(data.get("settlements"))
    mp.wards = _int(data.get("wards"))
    mp.roles = roles
    mp.planned_wf = sum(r["planned"] for r in roles)
    mp.target = target
    mp.goal_pct = goal
    mp.objective = _objective(target, goal)
    mp.doses = _int(data.get("doses"))
    mp.cold_boxes = _int(data.get("coldBoxes"))
    mp.vehicles = _int(data.get("vehicles"))
    mp.budget = _int(data.get("budget"))
    mp.planned_to_date = _ptd(mp.budget, mp.campaign)
    mp.updated = "Jun 4, 2026"
    mp.save()
    return mp


def set_target(mp, target, goal_pct) -> Microplan:
    mp.target = _int(target)
    mp.goal_pct = _int(goal_pct, 95)
    mp.objective = _objective(mp.target, mp.goal_pct)
    mp.updated = "Jun 4, 2026"
    mp.save(update_fields=["target", "goal_pct", "objective", "updated"])
    return mp


def set_budget(mp, budget) -> Microplan:
    mp.budget = _int(budget)
    mp.planned_to_date = _ptd(mp.budget, mp.campaign)
    mp.updated = "Jun 4, 2026"
    mp.save(update_fields=["budget", "planned_to_date", "updated"])
    return mp
