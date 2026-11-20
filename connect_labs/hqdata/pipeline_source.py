"""A CommCare project's stored rows, as the pipeline layer reads them (``data_source.type == "hq_store"``).

The rows reach a pipeline in exactly the shape the ``cchq_forms`` and ``cchq_cases`` sources produce, so
a schema written against either runs unchanged against a project's store: form paths start
``form.``, case paths ``case.``. They are written into the pipeline's raw slot by SQL, as they
change (``pipeline_feed``); this module holds the gate every read passes and where the cache lives.
"""

from __future__ import annotations

from datetime import datetime

from connect_labs.audit_trail.models import Action
from connect_labs.hqdata.access import ProjectAccessDenied, may_use_project
from connect_labs.hqdata.audit import project_denied, project_event
from connect_labs.hqdata.models import HQCase, HQProject
from connect_labs.hqdata.stores import store_for


def check_project_access(data_source, user) -> HQProject:
    """The gate every read of an ``hq_store`` pipeline passes, a cached read included.

    A cached result is not the caller's to have just because someone built it: it sits under
    the project's scope, so the question is always "is this person a verified member of THAT
    project", asked fresh. A project that does not exist and one the person cannot use refuse
    the same way, so the error never says which.
    """
    project = HQProject.objects.filter(pk=data_source.project_id).first()
    if project is None or not may_use_project(user, project):
        project_denied(data_source.project_id, user, kind="pipeline")
        raise ProjectAccessDenied("You are not a member of this CommCare project.")
    project_event(Action.READ, project.pk, user=user, kind="pipeline")
    return project


def _iso(moment: datetime | None) -> str:
    return moment.isoformat().replace("+00:00", "Z") if moment else ""


def case_as_hq_dict(row: HQCase) -> dict:
    """A stored case in the shape CommCare's Case API v2 returns it, which the case normaliser reads."""
    return {
        "case_id": row.case_id,
        "case_type": row.case_type,
        "case_name": row.case_name,
        "external_id": row.external_id,
        "owner_id": row.owner_id,
        "date_opened": _iso(row.date_opened),
        "last_modified": _iso(row.last_modified),
        "date_modified": _iso(row.last_modified),
        "server_last_modified": _iso(row.server_last_modified),
        "indexed_on": _iso(row.indexed_on),
        "closed": row.closed,
        "properties": row.properties,
        "indices": row.indices,
    }


def cache_backend_for(data_source, base_backend):
    """The backend a ``hq_store`` pipeline's cache operations go through.

    Labs' own for a project kept in labs. For one kept in a customer's database, the same backend
    run against that database, so nothing staged or computed from the project is written in labs.
    """
    from connect_labs.hqdata.redirect import RedirectedBackend

    project = HQProject.objects.get(pk=data_source.project_id)
    store = store_for(project)
    if store.alias == "default":
        return base_backend
    store.ensure_schema()  # a pipeline can run before the first sync has made the cache tables
    return RedirectedBackend(base_backend, store.alias)
