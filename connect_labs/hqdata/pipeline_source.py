"""A CommCare project's stored rows, as the pipeline layer reads them (``data_source.type == "hq_store"``).

The rows come out in exactly the shape the ``cchq_forms`` and ``cchq_cases`` sources produce, so
a schema written against either runs unchanged against a project's store: form paths start
``form.``, case paths ``case.``.
"""

from __future__ import annotations

from datetime import datetime

from django.conf import settings

from connect_labs.audit_trail.models import Action
from connect_labs.hqdata.access import ProjectAccessDenied, may_use_project
from connect_labs.hqdata.audit import project_denied, project_event
from connect_labs.hqdata.models import HQCase, HQProject
from connect_labs.hqdata.pipelines import PipelineProblem
from connect_labs.hqdata.scope import hq_scope_id
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


class ProjectTooLarge(PipelineProblem):
    """The project holds more rows than one pipeline run reads into memory."""


def max_rows() -> int:
    return int(getattr(settings, "HQ_STORE_PIPELINE_MAX_ROWS", 500_000))


def _within_ceiling(qs, project_id) -> None:
    """Refuse, before loading anything, a read that would not fit in the worker.

    A pipeline reads its source whole (the cache stage takes a list), so a very large project would
    be killed by the worker's memory limit with nothing to say why. Counting first costs one query.
    """
    count, ceiling = qs.count(), max_rows()
    if count > ceiling:
        raise ProjectTooLarge(
            f"This project keeps {count:,} rows here and a pipeline reads at most {ceiling:,} at once. "
            "Narrow the pipeline to one app, form or case type."
        )


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


def fetch_store_as_visit_dicts(data_source) -> list[dict]:
    """The project's stored rows, deleted ones and archived forms left out, as visit dicts."""
    project = HQProject.objects.get(pk=data_source.project_id)
    with store_for(project).session() as store:
        return _read(store, data_source)


def _queryset(store, data_source):
    """The stored rows a source reads, deleted ones and archived forms left out, in a stable order."""
    project_id = data_source.project_id
    if data_source.store_kind == "forms":
        qs = store.forms(project_id).filter(deleted_at__isnull=True, archived=False)
        if data_source.app_id:
            qs = qs.filter(app_id=data_source.app_id)
        if data_source.xmlns:
            qs = qs.filter(xmlns=data_source.xmlns)
        return qs.order_by("received_on", "form_id")
    qs = store.cases(project_id).filter(deleted_at__isnull=True)
    if data_source.case_type:
        qs = qs.filter(case_type=data_source.case_type)
    return qs.order_by("date_opened", "case_id")


def ensure_fits(data_source) -> None:
    """Raise ``ProjectTooLarge`` if a read of this source would not fit; one count query, nothing loaded."""
    project = HQProject.objects.get(pk=data_source.project_id)
    with store_for(project).session() as store:
        _within_ceiling(_queryset(store, data_source), data_source.project_id)


def _read(store, data_source) -> list[dict]:
    from connect_labs.labs.analysis.backends.sql.cchq_cases_fetcher import normalize_cchq_case_to_visit_dict
    from connect_labs.labs.analysis.backends.sql.cchq_fetcher import normalize_cchq_form_to_visit_dict

    qs = _queryset(store, data_source)
    _within_ceiling(qs, data_source.project_id)  # also here: a caller may reach the pipeline without run_pipeline
    if data_source.store_kind == "forms":
        return [
            normalize_cchq_form_to_visit_dict(row.form_json, i) for i, row in enumerate(qs.iterator(chunk_size=2000))
        ]
    scope = hq_scope_id(data_source.project_id)
    return [
        normalize_cchq_case_to_visit_dict(case_as_hq_dict(row), scope, i)
        for i, row in enumerate(qs.iterator(chunk_size=2000))
    ]


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
