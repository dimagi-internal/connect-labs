from commcare_connect.campaign.models import Microplan


def _objective(target, goal_pct):
    return round((target or 0) * (goal_pct or 95) / 100)


def _ptd(budget, campaign):
    return round((budget or 0) * campaign.days_elapsed / campaign.days_total) if campaign.days_total else 0


def create_microplan(campaign, data, owner_name) -> Microplan:
    roles = [
        {
            "roleId": r.get("roleId"),
            "role": r.get("role"),
            "rate": int(r.get("rate") or 0),
            "planned": int(r.get("planned") or 0),
            "actual": 0,
        }
        for r in (data.get("roles") or [])
    ]
    target = int(data.get("target") or 0)
    goal = int(data.get("goalPct") or 95)
    budget = int(data.get("budget") or 0)
    n = campaign.microplans.count()
    return Microplan.objects.create(
        campaign=campaign,
        microplan_id=f"MP-{200 + n}",
        region_id=data.get("regionId") or "",
        region=data.get("region") or "",
        lga=data.get("lga") or "",
        settlements=int(data.get("settlements") or 0),
        wards=int(data.get("wards") or 0),
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
        doses=int(data.get("doses") or 0),
        doses_used=0,
        cold_boxes=int(data.get("coldBoxes") or 0),
        vehicles=int(data.get("vehicles") or 0),
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
            "rate": int(r.get("rate") or 0),
            "planned": int(r.get("planned") or 0),
            "actual": existing_actual.get(r.get("roleId"), 0),
        }
        for r in (data.get("roles") or [])
    ]
    target = int(data.get("target") or 0)
    goal = int(data.get("goalPct") or 95)
    mp.region_id = data.get("regionId") or mp.region_id
    mp.region = data.get("region") or mp.region
    mp.lga = data.get("lga") or mp.lga
    mp.settlements = int(data.get("settlements") or 0)
    mp.wards = int(data.get("wards") or 0)
    mp.roles = roles
    mp.planned_wf = sum(r["planned"] for r in roles)
    mp.target = target
    mp.goal_pct = goal
    mp.objective = _objective(target, goal)
    mp.doses = int(data.get("doses") or 0)
    mp.cold_boxes = int(data.get("coldBoxes") or 0)
    mp.vehicles = int(data.get("vehicles") or 0)
    mp.budget = int(data.get("budget") or 0)
    mp.planned_to_date = _ptd(mp.budget, mp.campaign)
    mp.updated = "Jun 4, 2026"
    mp.save()
    return mp


def set_target(mp, target, goal_pct) -> Microplan:
    mp.target = int(target or 0)
    mp.goal_pct = int(goal_pct or 95)
    mp.objective = _objective(mp.target, mp.goal_pct)
    mp.updated = "Jun 4, 2026"
    mp.save(update_fields=["target", "goal_pct", "objective", "updated"])
    return mp


def set_budget(mp, budget) -> Microplan:
    mp.budget = int(budget or 0)
    mp.planned_to_date = _ptd(mp.budget, mp.campaign)
    mp.updated = "Jun 4, 2026"
    mp.save(update_fields=["budget", "planned_to_date", "updated"])
    return mp
