import pytest

from connect_labs.labs.synthetic import registry
from connect_labs.labs.synthetic.models import SyntheticOpportunity


@pytest.fixture(autouse=True)
def clear_cache():
    registry.invalidate_cache()
    yield
    registry.invalidate_cache()


@pytest.mark.django_db
def test_returns_none_for_unknown_opp():
    assert registry.get_synthetic_opp(9999) is None


@pytest.mark.django_db
def test_returns_row_for_enabled_opp():
    opp = SyntheticOpportunity.objects.create(opportunity_id=42, gdrive_folder_id="folder-abc", enabled=True)
    result = registry.get_synthetic_opp(42)
    assert result is not None
    assert result.pk == opp.pk


@pytest.mark.django_db
def test_returns_none_for_disabled_opp():
    SyntheticOpportunity.objects.create(opportunity_id=42, gdrive_folder_id="folder-abc", enabled=False)
    assert registry.get_synthetic_opp(42) is None


@pytest.mark.django_db
def test_cache_avoids_repeat_queries(django_assert_num_queries):
    SyntheticOpportunity.objects.create(opportunity_id=42, gdrive_folder_id="f", enabled=True)
    # First call: one SELECT
    with django_assert_num_queries(1):
        registry.get_synthetic_opp(42)
    # Second call within TTL: zero SELECTs
    with django_assert_num_queries(0):
        registry.get_synthetic_opp(42)


@pytest.mark.django_db
def test_invalidate_cache_forces_refresh(django_assert_num_queries):
    SyntheticOpportunity.objects.create(opportunity_id=42, gdrive_folder_id="f", enabled=True)
    registry.get_synthetic_opp(42)  # populate cache

    registry.invalidate_cache()

    with django_assert_num_queries(1):
        registry.get_synthetic_opp(42)


@pytest.mark.django_db
def test_ttl_expiry_refreshes(monkeypatch, django_assert_num_queries):
    SyntheticOpportunity.objects.create(opportunity_id=42, gdrive_folder_id="f", enabled=True)

    fake_time = [1000.0]
    monkeypatch.setattr(registry.time, "monotonic", lambda: fake_time[0])

    registry.get_synthetic_opp(42)  # loads at t=1000
    fake_time[0] = 1000.0 + registry._TTL_SECONDS + 1

    with django_assert_num_queries(1):
        registry.get_synthetic_opp(42)


@pytest.mark.django_db
def test_signal_invalidates_cache_on_save(django_assert_num_queries):
    SyntheticOpportunity.objects.create(opportunity_id=42, gdrive_folder_id="f", enabled=True)
    registry.get_synthetic_opp(42)  # populate

    # Creating another row fires post_save and should invalidate
    SyntheticOpportunity.objects.create(opportunity_id=43, gdrive_folder_id="g", enabled=True)

    with django_assert_num_queries(1):
        registry.get_synthetic_opp(42)


@pytest.mark.django_db
def test_signal_invalidates_cache_on_delete(django_assert_num_queries):
    opp = SyntheticOpportunity.objects.create(opportunity_id=42, gdrive_folder_id="f", enabled=True)
    registry.get_synthetic_opp(42)  # populate cache

    opp.delete()  # fires post_delete

    with django_assert_num_queries(1):
        registry.get_synthetic_opp(42)
