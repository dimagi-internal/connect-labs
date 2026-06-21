import pytest

from commcare_connect.labs.synthetic.cohort import CohortSpec, load_cohort_spec, save_cohort_spec

_SPEC = """
program_id: 10010
program_name: KMC (Synthetic)
org_name: Dimagi-KMC (Synthetic)
bundle_root: "gdrive:"
opportunity_ids: [523, 524, 1790]
"""


def test_from_yaml_parses_fields():
    spec = CohortSpec.from_yaml(_SPEC)
    assert spec.opportunity_ids == [523, 524, 1790]
    assert spec.program_id == 10010
    assert spec.program_name == "KMC (Synthetic)"
    assert spec.org_name == "Dimagi-KMC (Synthetic)"
    assert spec.bundle_root == "gdrive:"


def test_requires_opportunity_ids():
    with pytest.raises(ValueError):
        CohortSpec.from_yaml("program_name: X\n")


def test_program_id_optional_defaults_none():
    spec = CohortSpec.from_yaml("opportunity_ids: [1, 2]\n")
    assert spec.program_id is None
    assert spec.bundle_root == "gdrive:"  # default


def test_roundtrip_yaml_preserves_values():
    spec = CohortSpec.from_yaml(_SPEC)
    spec.bundle_root = "gdrive:run123"  # as Phase 1 would record
    again = CohortSpec.from_yaml(spec.to_yaml())
    assert again.bundle_root == "gdrive:run123"
    assert again.opportunity_ids == [523, 524, 1790]
    assert again.program_id == 10010


def test_save_then_load(tmp_path):
    spec = CohortSpec.from_yaml(_SPEC)
    spec.bundle_root = "gdrive:abc"
    path = tmp_path / "kmc.yaml"
    save_cohort_spec(path, spec)
    loaded = load_cohort_spec(path)
    assert loaded.bundle_root == "gdrive:abc"
    assert loaded.opportunity_ids == [523, 524, 1790]


def test_cohort_spec_curate_roundtrips():
    from commcare_connect.labs.synthetic.cohort import CohortSpec

    spec = CohortSpec.from_yaml("opportunity_ids: [1, 2]\ncurate: true\n")
    assert spec.curate is True
    assert "curate: true" in spec.to_yaml()
    assert CohortSpec.from_yaml(spec.to_yaml()).curate is True
    # default stays False
    assert CohortSpec.from_yaml("opportunity_ids: [1]\n").curate is False


def test_mirror_flag_parses_and_defaults_false():
    assert CohortSpec.from_yaml("opportunity_ids: [1]\n").mirror is False
    assert CohortSpec.from_yaml("opportunity_ids: [1]\nmirror: true\n").mirror is True


def test_mirror_survives_yaml_roundtrip():
    spec = CohortSpec.from_yaml("opportunity_ids: [1, 2]\nmirror: true\n")
    assert CohortSpec.from_yaml(spec.to_yaml()).mirror is True
