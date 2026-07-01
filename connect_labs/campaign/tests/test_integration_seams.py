"""The simulated-integration frontier.

Per the design spec, KYC provider, payment disbursement, and live CommCare reads are
**stubbed** for the demo — "approve payment" persists a local DB state and nothing
leaves the box. These tests (1) pin that contract — a payment approval makes NO
outbound HTTP, so the demo can't accidentally move money — and (2) leave a greppable,
skipped placeholder per seam that should turn into a real integration test when the
external system is wired. Grep `pytest.mark.stub` to find the whole frontier.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from connect_labs.campaign.services import worker_actions
from connect_labs.campaign.tests.factories import CampaignFactory, WorkerFactory

pytestmark = pytest.mark.stub


@pytest.mark.django_db
def test_payment_approval_makes_no_outbound_http():
    """Approving/queuing a payment must NOT call any payment gateway — it's a local
    state change only. If a real disbursement integration is added, this guard should
    be replaced by an assertion about the (mocked) gateway call."""
    worker = WorkerFactory(campaign=CampaignFactory(), kyc="pending", pay="pending", fraud_rules=[])
    with patch("httpx.Client") as hc, patch("httpx.get") as hg, patch("httpx.post") as hp:
        worker_actions.queue_pay(worker, approved_count=2)
        worker_actions.set_pay([worker], "approved")
    hc.assert_not_called()
    hg.assert_not_called()
    hp.assert_not_called()
    worker.refresh_from_db()
    assert worker.pay == "approved"


@pytest.mark.django_db
def test_kyc_decision_makes_no_outbound_http():
    """A KYC decision persists locally; no real KYC provider is contacted."""
    worker = WorkerFactory(campaign=CampaignFactory(), kyc="pending", fraud_rules=[])
    with patch("httpx.Client") as hc, patch("httpx.get") as hg, patch("httpx.post") as hp:
        worker_actions.set_kyc(worker, "review")
    hc.assert_not_called()
    hg.assert_not_called()
    hp.assert_not_called()
    worker.refresh_from_db()
    assert worker.kyc == "review"


# --- Greppable placeholders for the real integrations (not built yet) -------------


@pytest.mark.skip(reason="STUB: real payment-gateway disbursement is not built (spec §2/§11)")
def test_real_payment_disbursement_integration():
    """When a payment gateway is wired, assert: approve → gateway called with the
    correct amount/account; gateway failure rolls back to `hold`; idempotency."""


@pytest.mark.skip(reason="STUB: real KYC provider submission is not built (spec §2)")
def test_real_kyc_provider_submission_integration():
    """When a KYC provider is wired, assert: submit → provider called; result maps to
    approved/pending/rejected; fraud signals surface from the provider response."""


@pytest.mark.skip(reason="STUB: live CommCare worker/case reads are not built — data is seeded (spec §11)")
def test_live_commcare_worker_reads_integration():
    """When live CommCare reads replace the seeder, assert: bootstrap sources workers
    from the CommCare Case/Form API using the user's campaign_oauth token."""
