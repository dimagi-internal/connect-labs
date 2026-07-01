"""Unit tests for the shared synthetic org/program derivation primitives."""
from connect_labs.labs.synthetic import org_tree
from connect_labs.labs.synthetic.models import SyntheticOpportunity


def test_slugify_basic():
    assert org_tree.slugify("Baobab Institute") == "baobab-institute"


def test_slugify_collapses_nonalnum_and_strips():
    assert org_tree.slugify("  A/B  C! ") == "a-b--c"  # spaces+symbols -> hyphens, ends trimmed


def test_slugify_empty_falls_back_to_labs():
    assert org_tree.slugify("") == "labs"
    assert org_tree.slugify("   ") == "labs"


def test_synthetic_org_slug_uses_org_name():
    opp = SyntheticOpportunity(opportunity_id=10001, org_name="Baobab Institute", labs_only=True)
    assert org_tree.synthetic_org_slug(opp) == "labs-synthetic-baobab-institute"


def test_synthetic_org_slug_defaults_when_blank():
    opp = SyntheticOpportunity(opportunity_id=10001, org_name="", labs_only=True)
    assert org_tree.synthetic_org_slug(opp) == "labs-synthetic-labs-synthetic"


def test_synthetic_program_id_prefers_program_id():
    opp = SyntheticOpportunity(opportunity_id=10001, program_id=10050, labs_only=True)
    assert org_tree.synthetic_program_id(opp) == 10050


def test_synthetic_program_id_falls_back_to_opp_id():
    opp = SyntheticOpportunity(opportunity_id=10001, program_id=None, labs_only=True)
    assert org_tree.synthetic_program_id(opp) == 10001
