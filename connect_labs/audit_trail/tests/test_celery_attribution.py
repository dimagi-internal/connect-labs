"""Celery actor propagation.

A task inherits nothing from the request that queued it, so work done on a
person's behalf used to land in the audit trail — and in Sentry — with no
actor at all. ``before_task_publish`` stamps the acting identity onto the
message; ``task_prerun`` reads it back into the audit context.
"""
import pytest

from connect_labs.audit_trail import service
from connect_labs.audit_trail.context import (
    AuditContext,
    audit_context,
    get_audit_context,
    reset_audit_context,
    set_audit_context,
)
from connect_labs.audit_trail.models import Action, AuditEvent, Source
from connect_labs.audit_trail.signals import on_before_task_publish, on_task_prerun


class _FakeRequest:
    """Stand-in for celery's Context — custom message headers land as attrs."""

    def __init__(self, **headers):
        self.__dict__.update(headers)


class _FakeTask:
    name = "connect_labs.microplans.tasks.fetch_footprint"

    def __init__(self, **headers):
        self.request = _FakeRequest(**headers)


@pytest.mark.django_db
def test_publish_stamps_the_acting_user_onto_the_message(user):
    headers = {}
    with audit_context(user=user, source=Source.WEB, request_id="req-1"):
        on_before_task_publish(headers=headers)

    assert headers["labs_actor_id"] == user.pk
    assert headers["labs_actor_name"] == user.username
    assert headers["labs_origin_request_id"] == "req-1"


@pytest.mark.django_db
def test_publish_resolves_a_lazy_web_request_user(rf, user):
    """The web context carries the request, not a resolved user."""
    request = rf.get("/labs/overview/")
    request.user = user
    ctx = AuditContext(source=Source.WEB, buffer=[], request=request)
    token = set_audit_context(ctx)
    headers = {}
    try:
        on_before_task_publish(headers=headers)
    finally:
        reset_audit_context(token)

    assert headers["labs_actor_id"] == user.pk


def test_publish_is_a_noop_without_an_actor():
    headers = {}
    on_before_task_publish(headers=headers)
    assert headers == {}


@pytest.mark.django_db
def test_prerun_attributes_the_task_to_the_publisher(user):
    task = _FakeTask(labs_actor_id=user.pk, labs_actor_name=user.username)
    on_task_prerun(task_id="task-abc", task=task)
    try:
        ctx = get_audit_context()
        assert ctx.user_id == user.pk
        assert ctx.username == user.username
        assert ctx.source == Source.CELERY
        assert ctx.path == "connect_labs.microplans.tasks.fetch_footprint"
    finally:
        reset_audit_context(task.request.audit_trail_token)


@pytest.mark.django_db
def test_beat_published_task_stays_unattributed():
    """Tasks from beat or a script have no publisher — that must still work."""
    task = _FakeTask()
    on_task_prerun(task_id="task-abc", task=task)
    try:
        assert get_audit_context().user_id is None
    finally:
        reset_audit_context(task.request.audit_trail_token)


@pytest.mark.django_db
def test_events_recorded_inside_the_task_carry_the_actor(user):
    task = _FakeTask(labs_actor_id=user.pk, labs_actor_name=user.username)
    on_task_prerun(task_id="task-abc", task=task)
    try:
        service.record(Action.EXPORT, resource_type="user_visits", record_count=5)
    finally:
        reset_audit_context(task.request.audit_trail_token)

    event = AuditEvent.objects.get()
    assert event.user_id == user.pk
    assert event.source == Source.CELERY


@pytest.mark.django_db
def test_full_length_celery_request_id_is_stored():
    """The correlation id is built by this signal, so assert it here: a celery
    task id is a 36-char uuid and "celery:" + that is 43, which used to be
    clipped to 28 — cutting the tail off the very UUID it exists to correlate.
    (That the column now *accepts* 43 is covered in test_service.)"""
    task_id = "b6b1a3f2-6d1c-4a52-9d2e-3f0a7c8e1d55"
    task = _FakeTask()
    on_task_prerun(task_id=task_id, task=task)
    try:
        service.record(Action.READ, resource_type="thing")
    finally:
        reset_audit_context(task.request.audit_trail_token)

    assert AuditEvent.objects.get().request_id == f"celery:{task_id}"
