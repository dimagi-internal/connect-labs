"""The connect-messaging site's documents, and the profile labs keeps current in canopy (S2)."""

from __future__ import annotations

import jwt
import pytest
from canopy_sdk.keys import load_private_key

from connect_labs.messaging import service
from connect_labs.messaging.canopy_bridge import conf, profile_push, tasks
from connect_labs.messaging.canopy_bridge.models import ProfilePush
from connect_labs.messaging.canopy_bridge.tests.conftest import SITE_KEY
from connect_labs.messaging.models import Address
from connect_labs.messaging.tests.conftest import PROGRAM
from connect_labs.workers.models import ConnectWorker, WorkerEvent, WorkerMembership, WorkerState

pytestmark = pytest.mark.django_db


def _member(worker, stage="learning", opp=9001, program=PROGRAM):
    membership = WorkerMembership.objects.create(worker=worker, opportunity_id=opp, program_id=program)
    WorkerState.objects.create(membership=membership, stage=stage)
    return membership


def _address(worker, program=PROGRAM, channel="ch-1"):
    return Address.objects.create(
        worker=worker, channel_source=service.channel_source(program), program_id=program, channel_id=channel
    )


# --- off until configured ---------------------------------------------------------------------


def test_the_site_documents_are_404_until_the_site_is_configured(client, settings):
    settings.CANOPY_MESSAGING_SIGNING_KEY = ""
    assert client.get("/messaging/canopy/site.json").status_code == 404
    assert client.get("/messaging/canopy/jwks.json").status_code == 404


def test_the_bridge_is_off_without_messaging_or_any_setting(site_configured, settings):
    settings.MESSAGING_ENABLED = False
    assert not conf.enabled() and "MESSAGING_ENABLED is off" in conf.problems()
    settings.MESSAGING_ENABLED = True
    assert conf.enabled()
    settings.CANOPY_MESSAGING_WORKSPACE = ""
    assert not conf.enabled() and conf.problems() == ["CANOPY_MESSAGING_WORKSPACE is not set"]


def test_site_json_and_jwks_describe_the_messaging_site(client, site_configured):
    jwks = client.get("/messaging/canopy/jwks.json").json()
    public = load_private_key(SITE_KEY).public_key()
    assert [k["kid"] for k in jwks["keys"]] == [conf.host_config().kid]
    response = client.get("/messaging/canopy/site.json")
    assert response.status_code == 200 and response["Content-Type"] == "application/jose"
    doc = jwt.decode(response.content, public, algorithms=["EdDSA"], options={"verify_aud": False})
    assert doc["iss"] == "connect-messaging"
    assert doc["site"]["person_scheme"] == "labs-worker"
    assert doc["site"]["jwks_url"] == "https://labs.connect.dimagi.com/messaging/canopy/jwks.json"
    assert doc["site"]["mcp_resource"] == "https://labs.connect.dimagi.com/messaging/canopy/mcp/"
    assert doc["site"]["origins"] == ["https://labs.connect.dimagi.com"]
    assert doc["title"] == "Connect Worker messaging"


def test_the_messaging_site_never_uses_the_panels_key(site_configured, settings):
    settings.CANOPY_SIGNING_KEY = "something else"
    assert conf.signing_key() == SITE_KEY


# --- the profile -------------------------------------------------------------------------------


def test_the_profile_is_labels_never_a_phone_or_history(worker):
    worker.phone = "+2348000000000"
    worker.languages = ["ha", "en"]
    worker.save()
    _member(worker, stage="delivering")
    _address(worker)
    profile = profile_push.profile_for(worker)
    assert profile == {
        "stage": "delivering",
        "program": PROGRAM,
        "opportunity": 9001,
        "language": "ha",
        "consent": "on",
        "timezone": "Africa/Lagos",
    }
    item = profile_push.item_for(worker, 5)
    assert item["sub"] == "personalid:amina123" and item["record_uri"] == "labs-worker://amina123"
    assert "+234" not in str(item)


def test_consent_says_whether_labs_may_message_the_worker(worker):
    assert profile_push.consent_label(worker) == "none"
    address = _address(worker)
    assert profile_push.consent_label(worker) == "on"
    Address.objects.filter(pk=address.pk).update(consent=False)
    assert profile_push.consent_label(worker) == "off"


def test_a_new_channel_registers_the_worker_with_canopy(bridge_on, worker, run_now, monkeypatch):
    monkeypatch.setattr(tasks.push_worker_profile, "delay", lambda worker_id: tasks.push_worker_profile(worker_id))
    _member(worker)
    _address(worker)
    held = bridge_on.contacts["personalid:amina123"]
    assert held["record_uri"] == "labs-worker://amina123"
    assert held["display_name"] == "Amina Bello"
    assert held["profile"]["stage"] == "learning"
    worker.refresh_from_db()
    assert worker.canopy_contact_id == str(held["contact_id"])
    assert worker.canopy_profile_version == held["version"]
    assert ProfilePush.objects.get(worker=worker).status == "created"


def test_nothing_is_pushed_while_the_bridge_is_off(fake_canopy, worker, settings):
    settings.MESSAGING_ENABLED = False
    _address(worker)
    assert profile_push.push([worker]) == {"pushed": 0, "unchanged": 0, "failed": 0}
    assert tasks.sweep() is None
    assert fake_canopy.contact_requests == []


def test_an_unchanged_profile_is_not_sent_again_and_a_change_raises_the_version(bridge_on, worker):
    membership = _member(worker)
    _address(worker)
    profile_push.push([worker])
    first = bridge_on.contacts["personalid:amina123"]["version"]
    assert profile_push.push([worker]) == {"pushed": 0, "unchanged": 1, "failed": 0}
    assert len(bridge_on.contact_requests) == 1
    WorkerState.objects.filter(membership=membership).update(stage="ready_to_claim")
    profile_push.push([worker])
    held = bridge_on.contacts["personalid:amina123"]
    assert held["profile"]["stage"] == "ready_to_claim" and held["version"] > first


def test_the_sweep_pushes_workers_whose_stage_changed(bridge_on, worker):
    membership = _member(worker)
    _address(worker)
    profile_push.push_due()  # the first scan starts the cursor at the newest event
    assert bridge_on.contacts["personalid:amina123"]["profile"]["stage"] == "learning"
    WorkerState.objects.filter(membership=membership).update(stage="delivering")
    WorkerEvent.objects.create(
        worker=worker, kind="state_changed", source="labs", dedupe_key="sc-1", program_id=PROGRAM
    )
    result = profile_push.push_due()
    assert result["pushed"] == 1
    assert bridge_on.contacts["personalid:amina123"]["profile"]["stage"] == "delivering"


def test_pushes_go_in_batches_of_at_most_100(bridge_on):
    workers = []
    for i in range(150):
        w = ConnectWorker.objects.create(username=f"w{i:03d}")
        _address(w, channel=f"ch-{i}")
        workers.append(w)
    counts = profile_push.push(workers)
    assert counts["pushed"] == 150
    assert [len(r["contacts"]) for r in bridge_on.contact_requests] == [100, 50]


def test_an_unreachable_canopy_leaves_the_profile_due(bridge_on, worker):
    _address(worker)
    bridge_on.fail_next["contacts"] = (503, "unavailable: try later")
    assert profile_push.push([worker])["failed"] == 1
    row = ProfilePush.objects.get(worker=worker)
    assert row.status == "error" and row.dirty
    assert profile_push.push_due()["pushed"] == 1
    assert ProfilePush.objects.get(worker=worker).status == "created"


def test_a_stale_answer_is_not_retried(bridge_on, worker):
    _address(worker)
    bridge_on.contacts["personalid:amina123"] = {"version": 2**62, "contact_id": 7}
    profile_push.push([worker])
    row = ProfilePush.objects.get(worker=worker)
    assert row.status == "stale" and not row.dirty


def test_ensure_pushed_registers_before_a_first_session(bridge_on, worker):
    _address(worker)
    assert profile_push.ensure_pushed(worker)
    assert "personalid:amina123" in bridge_on.contacts
    requests = len(bridge_on.contact_requests)
    assert profile_push.ensure_pushed(worker)
    assert len(bridge_on.contact_requests) == requests


def test_an_erased_worker_is_never_pushed(bridge_on, worker):
    from django.utils import timezone

    _address(worker)
    worker.erased_at = timezone.now()
    worker.save()
    assert profile_push.push([worker])["pushed"] == 0
    assert profile_push.push_due()["pushed"] == 0
