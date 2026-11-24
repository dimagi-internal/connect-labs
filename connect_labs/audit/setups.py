"""Saved audit setups: keep what the creation wizard sends, and turn it into one audit.

    clean(body)              -> the fields an AuditSetup stores, or SetupRefused saying why
    save(body, user)         -> a new AuditSetup
    summary(setup)           -> one plain line ("25 most recent visits per worker · ...")
    run_kwargs(setup, ...)   -> the keyword arguments of ``tasks.run_audit_creation`` for
                                ONE worker on their opportunities

A setup holds HOW to audit, never WHO: the wizard's worker choice, visit ids and preview
results are not kept. A window of dates becomes a number of recent days, so a setup made
in November still means "the last 28 days" in March.
"""

from __future__ import annotations

from datetime import date, timedelta

from connect_labs.audit.setup_models import AuditSetup

VISIT_STATUSES = {"pending", "approved", "rejected", "over_limit"}
MAX_IMAGE_AUDITS = 20
MAX_CONTEXT_FIELDS = 50
MAX_TEXT = 300


class SetupRefused(ValueError):
    """What is wrong with a setup, in words for the person saving it."""


def _text(v, what, limit=MAX_TEXT, required=False) -> str:
    if v is None or v == "":
        if required:
            raise SetupRefused(f"{what} is required.")
        return ""
    if not isinstance(v, str) or len(v) > limit:
        raise SetupRefused(f"{what} must be text of up to {limit} characters.")
    return v.strip()


def _int(v, what, lo, hi) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise SetupRefused(f"{what} must be a whole number.")
    if isinstance(v, bool) or not (lo <= n <= hi):
        raise SetupRefused(f"{what} must be between {lo} and {hi}.")
    return n


def _strings(v, what, limit=20) -> list[str]:
    if v in (None, ""):
        return []
    if not isinstance(v, list) or len(v) > limit or any(not isinstance(x, str) or len(x) > MAX_TEXT for x in v):
        raise SetupRefused(f"{what} must be a list of up to {limit} names.")
    return [x for x in dict.fromkeys(v) if x]


def _criteria(c) -> dict:
    """The wizard's criteria (camelCase or snake_case), as a setup keeps them."""
    if not isinstance(c, dict):
        raise SetupRefused("criteria must be an object.")

    def get(*keys):
        for k in keys:
            if c.get(k) not in (None, ""):
                return c[k]
        return None

    audit_type = get("audit_type", "type") or "last_n_per_flw"
    out: dict = {}
    if audit_type == "last_n_per_flw":
        out["audit_type"] = "last_n_per_flw"
        out["count_per_flw"] = _int(get("count_per_flw", "countPerFlw"), "Visits per worker", 1, 500)
    elif audit_type in ("date_range", "recent_days"):
        days = get("days")
        if days is None:
            start, end = get("start_date", "startDate"), get("end_date", "endDate")
            try:
                days = (date.fromisoformat(str(end)[:10]) - date.fromisoformat(str(start)[:10])).days + 1
            except (TypeError, ValueError):
                raise SetupRefused("A date-range setup needs a start and an end date.")
        out["audit_type"] = "recent_days"
        out["days"] = _int(days, "The number of recent days", 1, 366)
    else:
        raise SetupRefused(
            "A setup audits each worker's most recent visits, or their visits in a window of recent days. "
            "Choose 'Last N visits per FLW' or a date range."
        )
    out["sample_percentage"] = _int(get("sample_percentage", "samplePercentage") or 100, "Sample percentage", 1, 100)
    out["deliver_unit_types"] = _strings(get("deliver_unit_types", "deliverUnitTypes"), "Forms")
    statuses = _strings(get("visit_statuses", "visitStatuses"), "Visit statuses")
    if any(s not in VISIT_STATUSES for s in statuses):
        raise SetupRefused(f"Visit statuses must be among: {', '.join(sorted(VISIT_STATUSES))}.")
    out["visit_statuses"] = statuses
    out["exclude_prior_audited"] = bool(get("exclude_prior_audited", "excludePriorAudited"))
    threshold = get("pass_threshold", "passThreshold")
    out["pass_threshold"] = None if threshold is None else _int(threshold, "The pass mark", 0, 100)
    out["tag"] = _text(get("tag"), "The tag", 100)
    return out


def _image_audits(v) -> list[dict]:
    if v in (None, ""):
        return []
    if not isinstance(v, list) or len(v) > MAX_IMAGE_AUDITS:
        raise SetupRefused(f"Photos to audit must be a list of up to {MAX_IMAGE_AUDITS}.")
    out = []
    for a in v:
        if not isinstance(a, dict):
            raise SetupRefused("Each photo to audit must be an object.")
        path = _text(a.get("image_path"), "A photo's form path", required=True)
        reviewers = []
        for r in a.get("reviewers") or []:
            if not isinstance(r, dict):
                raise SetupRefused("Each AI reviewer must be an object.")
            agent = _text(r.get("agent_id"), "An AI reviewer", 100, required=True)
            config = r.get("config") or {}
            if not isinstance(config, dict):
                raise SetupRefused("An AI reviewer's settings must be an object.")
            reviewers.append(
                {
                    "agent_id": agent,
                    "config": config,
                    "auto_apply_actions": _strings(r.get("auto_apply_actions"), "Automatic actions"),
                }
            )
        out.append({"image_path": path, "reviewers": reviewers})
    return out


def _context_fields(v) -> list[dict]:
    if v in (None, ""):
        return []
    if not isinstance(v, list) or len(v) > MAX_CONTEXT_FIELDS:
        raise SetupRefused(f"Fields shown beside photos must be a list of up to {MAX_CONTEXT_FIELDS}.")
    out = []
    for f in v:
        if not isinstance(f, dict):
            raise SetupRefused("Each field shown beside a photo must be an object.")
        out.append(
            {
                "image_path": _text(f.get("image_path") or f.get("imagePath"), "A field's photo path", required=True),
                "field_path": _text(f.get("field_path") or f.get("fieldPath"), "A field's form path", required=True),
                "label": _text(f.get("label"), "A field's label", 100),
            }
        )
    return out


def clean(body) -> dict:
    """What an AuditSetup stores from the wizard's body: {name, opportunities, criteria,
    image_audits, context_fields}. SetupRefused names the first problem."""
    if not isinstance(body, dict):
        raise SetupRefused("The setup must be an object.")
    opps = []
    for o in body.get("opportunities") or []:
        oid = o.get("id") if isinstance(o, dict) else o
        opps.append(_int(oid, "An opportunity id", 1, 10**12))
    if not opps:
        raise SetupRefused("Choose the opportunities the setup is built on (step 1).")
    image_audits = _image_audits(body.get("image_audits"))
    if not image_audits:
        raise SetupRefused("Choose at least one photo to audit (step 5).")
    return {
        "name": _text(body.get("name"), "The setup's name", 200, required=True),
        "opportunity_ids": list(dict.fromkeys(opps)),
        "criteria": _criteria(body.get("criteria")),
        "image_audits": image_audits,
        "context_fields": _context_fields(body.get("context_fields")),
    }


def save(body, user) -> AuditSetup:
    fields = clean(body)
    name = (getattr(user, "name", "") or getattr(user, "username", "") or "").strip()[:200]
    return AuditSetup.objects.create(
        **fields, created_by=user if getattr(user, "pk", None) else None, created_by_name=name
    )


def get(setup_id) -> AuditSetup | None:
    try:
        return AuditSetup.objects.filter(pk=int(setup_id), archived=False).first()
    except (TypeError, ValueError):
        return None


def _photo(path: str) -> str:
    leaf = (path or "").rstrip("/").split("/")[-1]
    return leaf.replace("_", " ") or path


def summary(setup: AuditSetup) -> str:
    """One plain line: which visits, which photos and checkers, the pass mark."""
    c = setup.criteria or {}
    if c.get("audit_type") == "recent_days":
        visits = f"visits in the last {c.get('days')} days"
    else:
        visits = f"{c.get('count_per_flw')} most recent visits per worker"
    if (c.get("sample_percentage") or 100) < 100:
        visits += f" ({c['sample_percentage']}% sample)"
    photos = []
    for a in setup.image_audits or []:
        agents = [r["agent_id"].replace("_", " ") for r in a.get("reviewers") or []]
        photos.append(_photo(a["image_path"]) + (f" ({', '.join(agents)})" if agents else ""))
    parts = [visits, "photos: " + "; ".join(photos)]
    if c.get("deliver_unit_types"):
        parts.append("forms: " + ", ".join(c["deliver_unit_types"]))
    if c.get("pass_threshold") is not None:
        parts.append(f"pass mark {c['pass_threshold']}%")
    return " · ".join(parts)


def as_dict(setup: AuditSetup) -> dict:
    return {
        "id": setup.pk,
        "name": setup.name,
        "summary": summary(setup),
        "opportunity_ids": setup.opportunity_ids,
        "created_by": setup.created_by_name,
        "updated_at": setup.updated_at.isoformat() if setup.updated_at else None,
    }


def run_kwargs(
    setup: AuditSetup,
    *,
    access_token: str,
    creator_username: str,
    worker_username: str,
    opportunity_ids: list[int],
    title: str,
    workflow_run_id: int | None = None,
    today: date | None = None,
) -> dict:
    """``run_audit_creation``'s keyword arguments for ONE worker, one session per
    opportunity (``granularity: per_flw``, the worker named in ``selected_flw_user_ids``)."""
    c = setup.criteria or {}
    criteria = {
        "granularity": "per_flw",
        "selected_flw_user_ids": [worker_username],
        "sample_percentage": c.get("sample_percentage") or 100,
        "deliver_unit_types": c.get("deliver_unit_types") or [],
        "visit_statuses": c.get("visit_statuses") or [],
        "exclude_prior_audited": bool(c.get("exclude_prior_audited")),
        "title": title[:200],
        "tag": c.get("tag") or "",
    }
    if c.get("pass_threshold") is not None:
        criteria["pass_threshold"] = c["pass_threshold"]
    if c.get("audit_type") == "recent_days":
        end = today or date.today()
        criteria.update(
            {
                "audit_type": "date_range",
                "start_date": (end - timedelta(days=int(c["days"]) - 1)).isoformat(),
                "end_date": end.isoformat(),
            }
        )
    else:
        criteria.update({"audit_type": "last_n_per_flw", "count_per_flw": int(c.get("count_per_flw") or 10)})
    return {
        "access_token": access_token,
        "username": creator_username,
        "opportunities": [{"id": int(o), "name": ""} for o in opportunity_ids],
        "criteria": criteria,
        "workflow_run_id": workflow_run_id,
        "image_audits": setup.image_audits or None,
        "context_fields": setup.context_fields or None,
    }
