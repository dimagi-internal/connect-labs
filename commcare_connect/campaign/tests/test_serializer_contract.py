"""Golden key-set contract for serializer output.

~5,400 LOC of React read `window.CUT_DATA` by key. A renamed/removed key silently
blanks the UI (the `role`/`region` bug was exactly this). `test_serializers` checks
the top-level payload keys exactly but only a *subset* of worker keys — so a removed
worker field would slip through. This freezes the EXACT worker + planning row key
sets; any add/remove fails until the golden is updated in lockstep with the JSX.
"""
from __future__ import annotations

import pytest

from commcare_connect.campaign.services import seed, serializers

pytestmark = pytest.mark.contract

# Frozen contract — the exact camelCase keys the prototype's React reads per worker.
WORKER_KEYS = frozenset(
    {
        "id",
        "first",
        "last",
        "name",
        "gender",
        "phone",
        "regionId",
        "region",
        "lga",
        "roleId",
        "role",
        "rate",
        "daysWorked",
        "daysApproved",
        "amount",
        "kyc",
        "pay",
        "bank",
        "acct",
        "nin",
        "passport",
        "enrolled",
        "attendance",
        "priorCampaigns",
        "duplicate",
        "dupWith",
        "fraudRules",
        "linked",
        "investigation",
        "documents",
    }
)

PLANNING_KEYS = frozenset(
    {
        "id",
        "name",
        "lgas",
        "plannedWf",
        "actualWf",
        "budget",
        "spent",
        "target",
        "reached",
        "vaccineAlloc",
        "vaccineUsed",
    }
)

# Plan 4 serializers.
ACTIVITY_KEYS = frozenset(
    {"id", "name", "donor", "status", "start", "end", "requests", "workers", "region", "target", "reached", "synced"}
)

MICROPLAN_KEYS = frozenset(
    {
        "id",
        "regionId",
        "region",
        "lga",
        "settlements",
        "wards",
        "plannedWf",
        "actualWf",
        "roles",
        "budget",
        "spent",
        "plannedToDate",
        "target",
        "objective",
        "goalPct",
        "reached",
        "doses",
        "dosesUsed",
        "coldBoxes",
        "vehicles",
        "status",
        "owner",
        "updated",
    }
)


@pytest.mark.django_db
def test_worker_serializer_exact_key_set():
    c = seed.seed_campaign(fresh=True)
    payload = serializers.bootstrap_payload(c)
    for w in payload["WORKERS"]:
        assert set(w.keys()) == WORKER_KEYS, f"worker key drift: {set(w.keys()) ^ WORKER_KEYS}"


@pytest.mark.django_db
def test_planning_serializer_exact_key_set():
    c = seed.seed_campaign(fresh=True)
    payload = serializers.bootstrap_payload(c)
    for row in payload["PLANNING"]:
        assert set(row.keys()) == PLANNING_KEYS, f"planning key drift: {set(row.keys()) ^ PLANNING_KEYS}"


@pytest.mark.django_db
def test_activity_serializer_exact_key_set():
    c = seed.seed_campaign(fresh=True)
    payload = serializers.bootstrap_payload(c)
    assert payload["ACTIVITIES"], "Plan 4 seeder should populate ACTIVITIES"
    for row in payload["ACTIVITIES"]:
        assert set(row.keys()) == ACTIVITY_KEYS, f"activity key drift: {set(row.keys()) ^ ACTIVITY_KEYS}"


@pytest.mark.django_db
def test_microplan_serializer_exact_key_set():
    c = seed.seed_campaign(fresh=True)
    payload = serializers.bootstrap_payload(c)
    assert payload["MICROPLANS"], "Plan 4 seeder should populate MICROPLANS"
    for row in payload["MICROPLANS"]:
        assert set(row.keys()) == MICROPLAN_KEYS, f"microplan key drift: {set(row.keys()) ^ MICROPLAN_KEYS}"
