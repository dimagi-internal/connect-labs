"""Supply's report templates are followed: a deploy reaches every tab drawn from them.

A stored copy froze each Supply tab at the code it was created with, so merged
label and number fixes never reached the tabs already pinned.
"""

import pytest

from connect_labs.workflow.templates import TEMPLATES, create_workflow_from_template

from .test_template_companions import _data_access


@pytest.mark.parametrize("key", ["supply_stock_review", "supply_stock_forecast"])
def test_a_supply_report_follows_the_deployed_template(key):
    dao = _data_access()
    definition, _render, _pipelines = create_workflow_from_template(dao, key, request=None, opportunity_ids=[10113])
    assert definition.data["render_source"] == {"template": key}
    assert TEMPLATES[key]["follow_template"] is True
