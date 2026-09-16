"""Turning a directory name into a stable organisation row.

The importer runs daily. If a name produced a different slug on the second run,
every organisation would be duplicated, so determinism here is not a nicety.
"""
import pytest

from connect_labs.labs.models import LabsOrg
from connect_labs.marketplace.identity import ensure_org, slug_for


class TestSlugFor:
    def test_is_deterministic(self):
        assert slug_for("Harbourside Health Initiative") == slug_for("Harbourside Health Initiative")

    def test_is_stable_across_incidental_differences(self):
        """Whitespace and case drift in a hand-maintained sheet must not fork a row."""
        assert slug_for("  Harbourside Health Initiative ") == slug_for("Harbourside Health Initiative")
        assert slug_for("HARBOURSIDE HEALTH INITIATIVE") == slug_for("Harbourside Health Initiative")

    def test_strips_accents_and_punctuation(self):
        assert slug_for("Santé Générale (SG)") == "sante-generale-sg"

    def test_distinct_names_get_distinct_slugs(self):
        assert slug_for("Fenwick Trust") != slug_for("Fenwick Trust International")

    def test_falls_back_when_a_name_has_no_usable_characters(self):
        """A row whose name is punctuation still needs a handle rather than ''."""
        assert slug_for("!!!").startswith("org-")

    def test_is_truncated_to_fit_the_column(self):
        assert len(slug_for("Organisation " * 40)) <= 120


@pytest.mark.django_db
class TestEnsureOrg:
    def test_creates_once_and_returns_the_same_row(self):
        first = ensure_org("Harbourside Health Initiative", short_name="HHI", country="NG")
        second = ensure_org("Harbourside Health Initiative")
        assert first.pk == second.pk
        assert LabsOrg.objects.count() == 1

    def test_updates_identity_fields_it_is_given(self):
        ensure_org("Harbourside Health Initiative")
        org = ensure_org("Harbourside Health Initiative", short_name="HHI", country="NG")
        assert org.short_name == "HHI"
        assert org.country == "NG"

    def test_does_not_blank_a_known_value_with_an_empty_one(self):
        """A sheet cell someone cleared must not wipe a value another source set."""
        ensure_org("Harbourside Health Initiative", short_name="HHI")
        org = ensure_org("Harbourside Health Initiative", short_name="")
        assert org.short_name == "HHI"

    def test_two_names_colliding_on_a_slug_do_not_merge(self):
        """Merging two organisations on the strength of a truncated string would
        fold two histories together. Distinct names stay distinct rows."""
        long_a = "Organisation " * 12 + "Alpha"
        long_b = "Organisation " * 12 + "Beta"
        a, b = ensure_org(long_a), ensure_org(long_b)
        assert a.pk != b.pk
        assert a.slug != b.slug
