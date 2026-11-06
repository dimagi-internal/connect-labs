"""'Preview as partner': what an organisation WOULD see in a programme once read enforcement is on.

For the programme's runner, before it turns `acl_enforce_reads` on (access_enforce): per modelled table, how many
rows the organisation's relationships reach against the programme's total. Read-only, changes nothing, and does
not depend on the switch (it answers "what if", whether the switch is on or off).

    preview(program_id, org_id) -> {
        "program_id": 20996, "org_id": 7, "org_name": "...", "enforcement_on": False,
        "models": [{"model": "Quote", "visible": 1, "total": 2}, ...],
        "visible": 14, "total": 31,
    }

An organisation that runs the programme sees everything by definition (visible == total). The counts use the same
`scope_for_orgs` the enforcement uses, with the same relations, so the preview and the enforcement cannot disagree.
Not covered by the preview, and not enforced yet: see the spec's list of what is still unfiltered.
"""

from __future__ import annotations

from connect_labs.labs.models import LabsOrg


def preview(program_id, org_id) -> dict:
    from connect_labs.supply_chain import access_enforce, access_relations

    org = LabsOrg.objects.filter(pk=org_id).first()
    if org is None:
        raise ValueError(f"organisation {org_id} does not exist")
    rows = []
    for model in sorted(access_relations.MODELS, key=lambda m: m.__name__):
        total = access_relations.programme_rows(model, program_id).count()
        visible = access_relations.scope_for_orgs({org.pk}, program_id, model).count()
        rows.append({"model": model.__name__, "visible": visible, "total": total})
    return {
        "program_id": int(program_id),
        "org_id": org.pk,
        "org_name": org.name,
        "enforcement_on": access_enforce.switch_on(program_id),
        "models": rows,
        "visible": sum(r["visible"] for r in rows),
        "total": sum(r["total"] for r in rows),
    }
