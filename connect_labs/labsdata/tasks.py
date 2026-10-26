"""Celery tasks for the Data page: building a de-identified export, and sweeping old ones."""

from __future__ import annotations

from config import celery_app


@celery_app.task(acks_late=True)
def build_space_export(export_id: int) -> None:
    from .exports import build

    build(export_id)


@celery_app.task
def sweep_space_exports() -> int:
    from .exports import sweep

    return sweep()
