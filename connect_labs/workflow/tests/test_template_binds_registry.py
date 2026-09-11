"""A workflow created from a semantic template computes from a LIVE registry record.

An unbound workflow computes from the on-disk registry, whose definitions change
only on a deploy -- an edit to any registry record never reaches it. Every template
creation used to produce exactly that: on 2026-09-11 both the real and the synthetic
KMC reports had to be bound to their records by hand, and a report created from the
template tomorrow would have silently ignored every indicator change after it.

So creation binds: the record the caller names, or a fresh record seeded from the
template's on-disk registry in the workflow's own scope. Never the disk copy.
"""

from unittest.mock import MagicMock, patch

import pytest

from connect_labs.workflow.templates import TEMPLATES, create_workflow_from_template

from .test_template_companions import _data_access, _pipeline_access, _template

SEMANTIC = "__tv_semantic_primary__"
PLAIN = "__tv_plain__"


@pytest.fixture
def registry():
    TEMPLATES[SEMANTIC] = _template(SEMANTIC, semantic_registry="kmc")
    TEMPLATES[PLAIN] = _template(PLAIN)
    yield
    for key in (SEMANTIC, PLAIN):
        TEMPLATES.pop(key, None)


def _create(dao, key, **kw):
    registry_access = MagicMock()
    registry_access.create_registry.return_value = MagicMock(id=7777)
    with patch("connect_labs.workflow.data_access.PipelineDataAccess", return_value=_pipeline_access()), patch(
        "connect_labs.workflow.data_access.SemanticRegistryDataAccess", return_value=registry_access
    ) as ctor:
        definition, _render, _pipeline = create_workflow_from_template(dao, key, request=None, **kw)
    return definition, registry_access, ctor


@pytest.mark.usefixtures("registry")
class TestASemanticTemplateIsBornBound:
    def test_with_nothing_named_a_new_record_is_seeded_from_disk_and_bound(self):
        definition, access, _ctor = _create(_data_access(), SEMANTIC)
        assert definition.data["registry_source"] == {"registry_id": 7777}
        kwargs = access.create_registry.call_args.kwargs
        # The seed's real documents, validated -- not an empty shell.
        assert kwargs["properties"]["properties"] and kwargs["indicators"]["measures"]
        assert kwargs["deployment"]["llo_map"], "the seed's deployment facts travel with it"

    def test_the_seeded_record_lives_in_the_workflows_own_scope(self):
        _definition, _access, ctor = _create(_data_access(opportunity_id=523), SEMANTIC)
        assert ctor.call_args.kwargs["opportunity_id"] == 523
        _definition, _access, ctor = _create(_data_access(opportunity_id=None, program_id=46), SEMANTIC)
        assert ctor.call_args.kwargs["program_id"] == 46

    def test_a_named_record_is_bound_and_nothing_is_seeded(self):
        source = {"registry_id": 5500}
        definition, access, _ctor = _create(_data_access(), SEMANTIC, registry_source=source)
        assert definition.data["registry_source"] == {"registry_id": 5500}
        access.create_registry.assert_not_called()

    def test_a_named_record_keeps_its_home_scope(self):
        source = {"registry_id": 19784, "organization_id": 179}
        definition, _access, _ctor = _create(_data_access(), SEMANTIC, registry_source=source)
        assert definition.data["registry_source"] == source

    @pytest.mark.parametrize("disk", [{"name": "kmc"}, {"organization_id": 179}])
    def test_a_disk_binding_is_refused_and_nothing_is_created(self, disk):
        dao = _data_access()
        with pytest.raises(ValueError, match="never the on-disk copy"):
            _create(dao, SEMANTIC, registry_source=disk)
        dao.create_definition.assert_not_called()


@pytest.mark.usefixtures("registry")
class TestATemplateWithNoIndicatorsIsUntouched:
    def test_no_record_is_seeded_and_no_binding_is_written(self):
        definition, access, _ctor = _create(_data_access(), PLAIN)
        assert "registry_source" not in definition.data
        access.create_registry.assert_not_called()

    def test_naming_a_registry_for_it_is_an_error(self):
        with pytest.raises(ValueError, match="computes no semantic indicators"):
            _create(_data_access(), PLAIN, registry_source={"registry_id": 5500})


def test_every_template_built_on_the_semantic_snapshot_declares_its_registry():
    """The declaration is what triggers the binding. A semantic template without it
    would be created unbound -- the static state this exists to prevent."""
    from connect_labs.workflow.templates import list_templates  # noqa: F401 -- populates TEMPLATES

    semantic = [
        key
        for key, t in TEMPLATES.items()
        if not key.startswith("__tv_") and (t.get("snapshot_inputs") or {}).get("builder") == "semantic_snapshot"
    ]
    assert semantic, "the KMC programme template, at least"
    assert [k for k in semantic if not TEMPLATES[k].get("semantic_registry")] == []
