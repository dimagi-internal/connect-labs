"""Cross-process cancel flag: the web sets it on cancel; the creation worker
checks it before creating a session so a mid-run cancel can't orphan a session
(the Celery revoke alone races session creation).
"""
from django.test import override_settings

from connect_labs.audit.data_access import is_audit_creation_cancelled, mark_audit_creation_cancelled

_LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(CACHES=_LOCMEM)
def test_not_cancelled_by_default():
    from django.core.cache import cache

    cache.clear()
    assert is_audit_creation_cancelled("task-x") is False


@override_settings(CACHES=_LOCMEM)
def test_mark_then_detected():
    from django.core.cache import cache

    cache.clear()
    mark_audit_creation_cancelled("task-x")
    assert is_audit_creation_cancelled("task-x") is True
    assert is_audit_creation_cancelled("task-other") is False


@override_settings(CACHES=_LOCMEM)
def test_empty_task_id_is_safe():
    assert is_audit_creation_cancelled("") is False
    assert is_audit_creation_cancelled(None) is False
    mark_audit_creation_cancelled("")  # no-op, must not raise
