"""A workflow may FOLLOW its deployed template's render instead of holding a copy.

Every workflow held a copy of its render code, pushed forward after each deploy
(`workflow_sync_from_deployed_template`); one that was missed quietly showed an older
report. The real and synthetic KMC reports held four copies of one render between
them on 2026-09-11. A follower renders the deployed template itself, so a deploy
reaches it with nothing to sync -- and edits to its unused stored copy are refused,
not silently accepted.
"""

from unittest.mock import MagicMock

import pytest

from connect_labs.workflow import render_source as rs
from connect_labs.workflow.templates import TEMPLATES

KEY = "__tv_render_follow__"


@pytest.fixture(autouse=True)
def template():
    TEMPLATES[KEY] = {"key": KEY, "name": KEY, "description": "d", "render_code": "function T(){return 'deployed'}"}
    yield
    TEMPLATES.pop(KEY, None)


def _definition(render_source=None, template_type=KEY):
    d = MagicMock()
    d.id = 7
    d.data = {"config": {"templateType": template_type}}
    if render_source is not None:
        d.data["render_source"] = render_source
    return d


def _dao(stored="function S(){return 'stored'}", version=4):
    dao = MagicMock()
    record = MagicMock()
    record.data = {"component_code": stored}
    record.version = version
    dao.get_render_code.return_value = record
    return dao


class TestWhatThePageRenders:
    def test_a_follower_renders_the_deployed_template(self):
        code, source = rs.resolve_render_code(_dao(), _definition({"template": KEY}))
        assert "deployed" in code and source == {"source": "template", "template": KEY}

    def test_a_workflow_that_does_not_follow_renders_its_stored_copy(self):
        code, source = rs.resolve_render_code(_dao(), _definition())
        assert "stored" in code and source == {"source": "stored", "version": 4}

    def test_following_a_template_that_is_not_deployed_falls_back_to_the_copy(self):
        code, source = rs.resolve_render_code(_dao(), _definition({"template": "gone"}))
        assert "stored" in code and source["source"] == "stored"


class TestEditsToAFollower:
    def test_an_edit_is_refused_with_the_way_out(self):
        with pytest.raises(rs.RenderFollowsTemplate) as e:
            rs.refuse_edit_if_following(_definition({"template": KEY}))
        assert "render_source to null" in str(e.value)

    def test_an_edit_to_a_workflow_with_its_own_copy_is_allowed(self):
        rs.refuse_edit_if_following(_definition())


class TestChoosingARenderSource:
    def test_null_means_the_stored_copy(self):
        assert rs.validate_render_source(None, _definition()) is None

    def test_a_workflow_may_follow_its_own_template(self):
        assert rs.validate_render_source({"template": KEY}, _definition()) == {"template": KEY}

    def test_not_another_templates_render(self):
        other = "__tv_render_other__"
        TEMPLATES[other] = {"key": other, "render_code": "x"}
        try:
            with pytest.raises(ValueError, match="only its own template"):
                rs.validate_render_source({"template": other}, _definition())
        finally:
            TEMPLATES.pop(other, None)

    @pytest.mark.parametrize("bad", [{"template": 3}, {"workflow_id": 1}, "kmc", {"template": "nope"}])
    def test_malformed_or_unknown_is_refused(self, bad):
        with pytest.raises(ValueError):
            rs.validate_render_source(bad, _definition())
