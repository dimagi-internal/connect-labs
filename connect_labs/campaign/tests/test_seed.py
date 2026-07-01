import pytest

from connect_labs.campaign.models import Campaign, Worker
from connect_labs.campaign.services import seed


@pytest.mark.django_db
def test_seed_is_idempotent_and_coherent():
    c1 = seed.seed_campaign()
    c2 = seed.seed_campaign()
    assert c1.id == c2.id  # idempotent: no duplicate campaign
    assert Campaign.objects.count() == 1
    assert c1.workers.count() == 64
    assert c1.donors.count() == 4
    assert c1.regions.count() == 5
    assert c1.worker_roles.count() == 5
    # invariant: amount == days_worked * role rate
    rates = {r.role_id: r.rate for r in c1.worker_roles.all()}
    for w in c1.workers.all():
        assert w.amount == w.days_worked * rates[w.role_id]
    # exactly the canonical donor order/value
    assert list(c1.donors.values_list("short", flat=True)) == ["Gavi", "BMGF", "UNICEF", "WHO"]


@pytest.mark.django_db
def test_seed_injects_seven_fraud_pairs():
    c = seed.seed_campaign(fresh=True)
    flagged = [w for w in c.workers.all() if w.duplicate]
    # 7 pairs => up to 14 workers carry duplicate=True and a dup_with pointer
    assert 2 <= len(flagged) <= 14
    for w in flagged:
        assert w.dup_with
        assert len(w.fraud_rules) >= 1
        assert any(link.get("shared") for link in w.linked)


@pytest.mark.django_db
def test_seed_fresh_replaces_not_duplicates():
    seed.seed_campaign()
    seed.seed_campaign(fresh=True)
    assert Campaign.objects.count() == 1
    assert Worker.objects.count() == 64


@pytest.mark.django_db
def test_seed_scales_to_requested_worker_count():
    """worker_count exercises the UX at realistic scale while preserving invariants."""
    c = seed.seed_campaign(fresh=True, worker_count=500)
    assert c.workers.count() == 500
    rates = {r.role_id: r.rate for r in c.worker_roles.all()}
    for w in c.workers.all():
        assert w.amount == w.days_worked * rates[w.role_id]  # invariant holds at scale
        assert w.region_id in set(c.regions.values_list("region_id", flat=True))  # valid FKs
    # fraud clusters scale with the roster (≈7 pairs per 64 workers)
    flagged = [w for w in c.workers.all() if w.duplicate]
    assert len(flagged) >= 14
    for w in flagged:
        assert w.dup_with and len(w.fraud_rules) >= 1


@pytest.mark.django_db
def test_seed_handles_tiny_worker_count_without_crashing():
    """A degenerate small roster must not blow up fraud injection (edge case)."""
    c = seed.seed_campaign(fresh=True, worker_count=3)
    assert c.workers.count() == 3
