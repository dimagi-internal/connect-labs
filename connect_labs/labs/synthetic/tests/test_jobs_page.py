"""Your synthetic jobs: where each stands, and stopping one (views.SyntheticJobsView)."""

import json
import time
from unittest import mock

import pytest
from django.core.cache import cache
from django.urls import reverse

from connect_labs.labs.synthetic import tasks
from connect_labs.mcp import profile_limits


@pytest.fixture
def signed_in(client, django_user_model):
    user = django_user_model.objects.create_user(username="jo", password="x", email="jo@dimagi.com")
    client.force_login(user)
    return user


def _job(user, task_id, *, ago=60):
    signature = json.dumps({"kind": "clone_opp", "opportunity_ids": [2230], "case_timelines": True}, sort_keys=True)
    jobs = cache.get(profile_limits._inflight_key(user)) or []
    jobs.append({"task_id": task_id, "signature": signature, "at": time.time() - ago})
    cache.set(profile_limits._inflight_key(user), jobs, 3600)
    cache.set(f"synthetic_profile_owner:{task_id}", user.id, 3600)


@pytest.fixture(autouse=True)
def clean_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.mark.django_db
def test_a_job_waiting_while_a_slot_is_free_is_called_out(client, signed_in):
    _job(signed_in, "t-stuck")
    with mock.patch.object(profile_limits, "_state", return_value="RETRY"):
        body = client.get(reverse("labs:synthetic:jobs")).content.decode()
    assert "Waiting for a free slot" in body and "#2230" in body
    # Both slots are free, so a job still "waiting for a slot" was lost, and the page says what to do.
    assert 'data-testid="job-stuck"' in body
    assert 'data-testid="job-cancel"' in body


@pytest.mark.django_db
def test_a_job_waiting_behind_busy_slots_is_not_called_stuck(client, signed_in):
    _job(signed_in, "t-waiting")
    cache.set("synthetic:job-slot:0", "someone-else", 600)
    cache.set("synthetic:job-slot:1", "another", 600)
    with mock.patch.object(profile_limits, "_state", return_value="RETRY"):
        body = client.get(reverse("labs:synthetic:jobs")).content.decode()
    assert 'data-testid="job-stuck"' not in body
    assert body.count("someone else's") == 2


@pytest.mark.django_db
def test_cancelling_stops_the_job_and_frees_its_slot(client, signed_in):
    _job(signed_in, "t-1")
    cache.set("synthetic:job-slot:1", "t-1", 600)
    with (
        mock.patch.object(tasks.celery_app.control, "revoke") as revoke,
        mock.patch.object(tasks.celery_app.backend, "store_result") as store,
    ):
        response = client.post(reverse("labs:synthetic:cancel_job", args=["t-1"]))
    assert response.status_code == 302
    revoke.assert_called_once_with("t-1", terminate=True)
    store.assert_called_once_with("t-1", None, "REVOKED")
    assert cache.get("synthetic:job-slot:1") is None


@pytest.mark.django_db
def test_someone_elses_job_cannot_be_cancelled(client, signed_in, django_user_model):
    other = django_user_model.objects.create_user(username="sam", password="x", email="sam@dimagi.com")
    _job(other, "t-theirs")
    with mock.patch.object(tasks.celery_app.control, "revoke") as revoke:
        response = client.post(reverse("labs:synthetic:cancel_job", args=["t-theirs"]))
    assert response.status_code == 404
    revoke.assert_not_called()
