"""#1034: re-registering a synthetic opp must forget everything derived from its
fixtures, not just the registry entry.

The reported cost was six escalating attempts to make a regenerated dataset
reach a dashboard — replace the bytes, re-register, bump the schema version,
mint a new pipeline, register a new folder id — with every intermediate state
rendering cleanly while serving superseded numbers.
"""

from unittest.mock import patch

import pytest

from connect_labs.labs.synthetic.invalidation import invalidate_synthetic_caches


@pytest.fixture
def layers():
    """Patch all three invalidation layers and hand back the mocks."""
    with (
        patch("connect_labs.labs.synthetic.registry.invalidate_cache") as registry,
        patch("connect_labs.labs.integrations.connect.factory._get_fixture_store") as store,
        patch("connect_labs.labs.analysis.backends.sql.cache.SQLCacheManager.delete_all_cache") as sql,
    ):
        sql.return_value = {"raw": 1, "computed_visit": 2}
        yield registry, store, sql


class TestInvalidateSyntheticCaches:
    def test_all_three_layers_are_cleared(self, layers):
        registry, store, sql = layers
        invalidate_synthetic_caches(10038)

        registry.assert_called_once()
        store.return_value.reload.assert_called_once_with(10038)
        sql.assert_called_once_with(10038)

    def test_the_sql_layer_can_be_skipped_for_metadata_only_changes(self, layers):
        registry, store, sql = layers
        invalidate_synthetic_caches(10038, drop_sql_cache=False)

        registry.assert_called_once()
        store.return_value.reload.assert_called_once_with(10038)
        sql.assert_not_called()

    def test_a_failing_layer_does_not_stop_the_others(self, layers):
        """A half-invalidated cache is still strictly better than the stale one it
        replaced, and this runs after a registration that already succeeded —
        raising here would fail a write that actually happened."""
        registry, store, sql = layers
        store.side_effect = RuntimeError("drive is down")

        outcome = invalidate_synthetic_caches(10038)

        assert outcome["fixture_store"] is False
        registry.assert_called_once()
        sql.assert_called_once_with(10038)

    def test_it_reports_what_it_managed_to_clear(self, layers):
        outcome = invalidate_synthetic_caches(10038)
        assert outcome["registry"] is True
        assert outcome["fixture_store"] is True
        assert outcome["sql_cache"] == {"raw": 1, "computed_visit": 2}


def test_every_fixture_write_path_invalidates():
    """`synthetic_register` cleared only the registry, which is the whole bug.
    A new path that forgets should fail here rather than in front of a funder."""
    import inspect

    from connect_labs.labs.synthetic import provisioning, views
    from connect_labs.labs.synthetic.generator.io import uploader
    from connect_labs.mcp.tool_registry import get_tool

    paths = {
        "synthetic_register": get_tool("synthetic_register").handler,
        "synthetic_repoint_by_source": get_tool("synthetic_repoint_by_source").handler,
        "synthetic_reload_fixtures": get_tool("synthetic_reload_fixtures").handler,
        "upload_and_register": uploader.upload_and_register,
        "register_labs_only_opp": provisioning.register_labs_only_opp,
        "reload_fixtures_view": views.reload_fixtures_view,
    }
    for label, fn in paths.items():
        assert "invalidate_synthetic_caches" in inspect.getsource(fn), (
            f"{label} points an opp at fixtures without invalidating what is derived from them; "
            "the dashboard will render cleanly while serving the previous dataset (#1034)"
        )


def test_the_reload_tool_exists_and_is_a_write():
    """The escape hatch existed in code (`FixtureStore.reload`) with no caller
    reachable from MCP, which is how #1034 lost an afternoon."""
    from connect_labs.mcp.tool_registry import get_tool

    tool = get_tool("synthetic_reload_fixtures")
    assert tool.is_write is True
    assert "in place" in tool.description.lower()


@pytest.mark.django_db
def test_reload_tool_returns_what_it_cleared(layers):
    from connect_labs.labs.synthetic.models import SyntheticOpportunity
    from connect_labs.mcp.tool_registry import get_tool
    from connect_labs.users.models import User

    SyntheticOpportunity.objects.create(opportunity_id=10038, gdrive_folder_id="f1", visit_count=5)
    user = User.objects.create(username="reload-tester")

    with (
        patch("connect_labs.mcp.tools.synthetic._require_opportunity_access", lambda u, o: None),
        patch("connect_labs.labs.synthetic.visit_count._count_visits", return_value=7),
    ):
        out = get_tool("synthetic_reload_fixtures").handler(user=user, opportunity_id=10038)

    assert out["opportunity_id"] == 10038
    assert out["invalidated"]["registry"] is True
    assert out["visit_count"] == 7


@pytest.mark.django_db
def test_reload_tool_404s_on_an_unregistered_opp():
    from connect_labs.mcp.tool_registry import MCPToolError, get_tool
    from connect_labs.users.models import User

    user = User.objects.create(username="reload-tester-2")
    with patch("connect_labs.mcp.tools.synthetic._require_opportunity_access", lambda u, o: None):
        with pytest.raises(MCPToolError):
            get_tool("synthetic_reload_fixtures").handler(user=user, opportunity_id=99999)
