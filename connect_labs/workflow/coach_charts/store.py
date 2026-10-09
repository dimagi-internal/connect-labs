"""Frozen charts, stored: the row a sent conversation's picture link names.

A coaching preview builds each worker's chart (``chart.build_chart``) and stores it
here. Its id is a hash of the chart's content AND of who previewed it, for which run
and worker, so the same preview finds the same row and nobody can name another
person's chart as their own. The commit of a preview carries the ids it was shown
(``actions.py``), each re-checked against who is committing, which run and worker,
and the request -- so what is sent is exactly the chart that was approved, frozen,
not drawn again from the run.
"""

from __future__ import annotations

import hashlib

from connect_labs.workflow.coach_charts import chart as charts


def record_id(chart: dict, *, user_id: int, run_id: int, worker_key: str) -> str:
    body = f"{charts.chart_id(chart)}:{user_id}:{run_id}:{worker_key}"
    return hashlib.sha256(body.encode()).hexdigest()[:32]


def save(chart: dict, *, user, run, worker_key: str, request: dict):
    """The stored row for ``chart`` (made if new)."""
    from connect_labs.workflow.models import CoachChart

    pk = record_id(chart, user_id=user.pk, run_id=run.id, worker_key=worker_key)
    record, _ = CoachChart.objects.get_or_create(
        pk=pk,
        defaults={
            "created_by": user,
            "run_id": run.id,
            "opportunity_id": getattr(run, "opportunity_id", None),
            "program_id": getattr(run, "program_id", None),
            "worker_key": worker_key,
            "request": request,
            "chart": chart,
        },
    )
    return record


def load_bound(pk, *, user, run, worker_key: str, request: dict):
    """The stored chart ``pk`` if it is this person's, for this run and worker, made
    for this request; else None (and the caller builds a fresh one)."""
    from connect_labs.workflow.models import CoachChart

    if not isinstance(pk, str) or len(pk) != 32:
        return None
    record = CoachChart.objects.filter(pk=pk).first()
    if record is None:
        return None
    if (record.created_by_id, record.run_id, record.worker_key) != (user.pk, run.id, worker_key):
        return None
    if record.request != request:
        return None
    if record_id(record.chart, user_id=user.pk, run_id=run.id, worker_key=worker_key) != pk:
        return None
    return record


def get(pk):
    from connect_labs.workflow.models import CoachChart

    return CoachChart.objects.filter(pk=pk).first() if isinstance(pk, str) else None
