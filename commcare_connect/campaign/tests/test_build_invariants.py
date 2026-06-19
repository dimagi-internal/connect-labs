"""Build-invariant guards for the Campaign Utility Tool.

1. No missing migrations — models and migrations must stay in lockstep. This catches
   the common Plan-N mistake of editing a model without `makemigrations` (Plans 4–6
   add Activity/Microplan/Reporting models).
2. Demo reproducibility — the seeded demo rests on a fixed PRNG (`seed.SEED`). Two
   fresh seeds must produce byte-identical worker data, or screenshots, walkthroughs,
   and "the numbers moved" demos drift run-to-run.
"""
from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command

from commcare_connect.campaign.services import seed


@pytest.mark.django_db
def test_no_missing_campaign_migrations():
    out = StringIO()
    try:
        call_command("makemigrations", "campaign", "--check", "--dry-run", stdout=out, stderr=out)
    except SystemExit as exc:  # --check exits non-zero when model changes lack a migration
        raise AssertionError("campaign has un-migrated model changes:\n" + out.getvalue()) from exc


@pytest.mark.django_db
def test_seed_is_deterministic_across_fresh_runs():
    def snapshot(campaign):
        return sorted((w.worker_id, w.name, w.amount, w.kyc, w.pay, w.dup_with) for w in campaign.workers.all())

    first = snapshot(seed.seed_campaign(fresh=True))
    second = snapshot(seed.seed_campaign(fresh=True))
    assert first == second, "seeded worker data is not reproducible — the fixed PRNG contract is broken"
