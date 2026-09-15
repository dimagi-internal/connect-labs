"""Merging two rows that are one organisation.

The reference sweep is the whole risk. `recorded_by_org` is declared with
`related_name="+"`, so it does not appear in `_meta.related_objects` at all:
a merge built on that API sees three references where there are thirteen and
leaves every provenance row pointing at a deleted organisation. These tests
walk the real field scan rather than a list written here, so a fourteenth
reference added later is covered without anyone remembering.
"""

import pytest

from connect_labs.labs.models import LabsOrg
from connect_labs.labs.org_merge import merge_orgs, references_to_org

pytestmark = pytest.mark.django_db


@pytest.fixture
def two_orgs():
    keep = LabsOrg.objects.create(slug="dimagi", name="Dimagi")
    merge = LabsOrg.objects.create(slug="programme", name="Programme team")
    return keep, merge


class TestTheSweep:
    def test_it_finds_the_references_the_reverse_api_hides(self):
        """`related_name="+"` means no reverse accessor, so provenance links
        are invisible to `related_objects`. This is the check that the sweep
        does not inherit that blindness."""
        found = {f"{m._meta.label}.{f}" for m, f in references_to_org()}
        assert "supply_chain.Document.recorded_by_org" in found
        assert "supply_chain.Receipt.recorded_by_org" in found
        # And the ones the reverse API does show.
        assert "supply_chain.Contract.buyer_org" in found
        assert len(found) >= 13, sorted(found)

    def test_nothing_is_left_pointing_at_the_merged_away_row(self, two_orgs):
        """The property that matters, asserted over every reference the scan
        knows about rather than the two a test would think to check."""
        keep, merge = two_orgs
        merge_id = merge.pk
        merge_orgs(keep_id=keep.pk, merge_id=merge_id)

        for model, field in references_to_org():
            assert not model.objects.filter(**{field: merge_id}).exists(), f"{model._meta.label}.{field}"
        assert not LabsOrg.objects.filter(pk=merge_id).exists()


class TestWhatMoves:
    def test_a_provenance_reference_moves(self, two_orgs):
        from connect_labs.supply_chain.models import Document

        keep, merge = two_orgs
        doc = Document.objects.create(
            program_id=10501,
            kind="other",
            source="we_recorded",
            external_url="https://example.test/x.pdf",
            recorded_by_org=merge,
        )
        result = merge_orgs(keep_id=keep.pk, merge_id=merge.pk)

        doc.refresh_from_db()
        assert doc.recorded_by_org_id == keep.pk
        assert result["moved"]["supply_chain.Document.recorded_by_org"] == 1
        assert result["total_moved"] == 1

    def test_the_old_slug_survives_as_an_alias(self, two_orgs):
        """Somebody referred to the organisation by that name, so a lookup by
        it should still find the body it meant rather than nothing."""
        keep, merge = two_orgs
        merge_orgs(keep_id=keep.pk, merge_id=merge.pk)
        keep.refresh_from_db()
        assert "programme" in keep.aliases
        assert keep.matches(slug="programme")

    def test_a_link_the_other_row_carried_is_inherited(self):
        """A merge must not lose reconciliation work already done."""
        keep = LabsOrg.objects.create(slug="dimagi", name="Dimagi")
        merge = LabsOrg.objects.create(slug="dimagi-old", name="Dimagi", connect_organization_id=7)
        merge_orgs(keep_id=keep.pk, merge_id=merge.pk)
        keep.refresh_from_db()
        assert keep.connect_organization_id == 7


class TestRefusals:
    def test_two_different_connect_organisations_are_not_one_body(self):
        """The matching rule exists so a merge cannot be done on a
        resemblance. Two live links are positive evidence they differ."""
        a = LabsOrg.objects.create(slug="a", name="A", connect_organization_id=1)
        b = LabsOrg.objects.create(slug="b", name="B", connect_organization_id=2)
        with pytest.raises(ValueError, match="not one organisation"):
            merge_orgs(keep_id=a.pk, merge_id=b.pk)
        assert LabsOrg.objects.count() == 2

    def test_an_organisation_cannot_be_merged_into_itself(self, two_orgs):
        keep, _ = two_orgs
        with pytest.raises(ValueError, match="into itself"):
            merge_orgs(keep_id=keep.pk, merge_id=keep.pk)

    def test_a_missing_organisation_is_refused(self, two_orgs):
        keep, _ = two_orgs
        with pytest.raises(ValueError, match="does not exist"):
            merge_orgs(keep_id=keep.pk, merge_id=999999)


class TestTheSweepIsComplete:
    """Both of these are latent: there is no OneToOneField to LabsOrg today,
    and a duplicate alias only appears after a chain of merges. They are
    pinned because the cost of the sweep being wrong is silent -- a row left
    pointing at a deleted organisation, or a name list that grows a copy of
    itself every time two rows are folded together."""

    def test_the_sweep_catches_a_one_to_one_not_just_a_foreign_key(self):
        """`many_to_one` is False for a OneToOneField, so an FK-only sweep
        skips it -- the same shape of miss as reading
        `_meta.related_objects`, which this sweep exists to avoid.

        Tested against the predicate with stub fields rather than against the
        sweep's source text: the first version of this asserted that the word
        "one_to_one" appeared in the function, which stayed true after the
        clause using it was deleted.
        """
        from connect_labs.labs.models import LabsOrg
        from connect_labs.labs.org_merge import points_at_org

        class Field:
            def __init__(self, **kw):
                self.concrete = kw.get("concrete", True)
                self.related_model = kw.get("related_model", LabsOrg)
                self.many_to_one = kw.get("many_to_one", False)
                self.one_to_one = kw.get("one_to_one", False)

        assert points_at_org(Field(many_to_one=True)) is True
        assert points_at_org(Field(one_to_one=True)) is True, "a OneToOneField to LabsOrg must be swept"
        # and the things that must NOT match
        assert points_at_org(Field(many_to_one=True, related_model=object)) is False
        assert points_at_org(Field(one_to_one=True, concrete=False)) is False, "a reverse relation is not ours to move"
        assert points_at_org(Field()) is False

    def test_every_reference_really_moves(self, db):
        from connect_labs.labs.models import LabsOrg
        from connect_labs.labs.org_merge import merge_orgs, references_to_org
        from connect_labs.supply_chain.models import Document

        keep = LabsOrg.objects.create(slug="dimagi", name="Dimagi")
        merge = LabsOrg.objects.create(slug="programme", name="Programme team")
        doc = Document.objects.create(
            kind="other",
            title="x",
            program_id=1,
            source="we_recorded",
            external_url="https://example.invalid/x.pdf",
            recorded_by_org=merge,
        )

        result = merge_orgs(keep_id=keep.pk, merge_id=merge.pk)

        doc.refresh_from_db()
        assert doc.recorded_by_org_id == keep.pk
        assert result["total_moved"] == 1
        # 13 references today; the assertion is that the sweep found the one
        # that mattered, not that the number is frozen.
        assert len(references_to_org()) >= 13

    def test_a_chain_of_merges_does_not_accumulate_duplicate_aliases(self, db):
        """b's slug becomes an alias on a; merging a into c then carried both
        a's aliases AND a's slug, so a name could land twice."""
        from connect_labs.labs.models import LabsOrg
        from connect_labs.labs.org_merge import merge_orgs

        a = LabsOrg.objects.create(slug="alpha", name="Alpha")
        b = LabsOrg.objects.create(slug="beta", name="Beta")
        merge_orgs(keep_id=a.pk, merge_id=b.pk)
        a.refresh_from_db()
        assert a.aliases == ["beta"]

        c = LabsOrg.objects.create(slug="gamma", name="Gamma")
        merge_orgs(keep_id=c.pk, merge_id=a.pk)
        c.refresh_from_db()
        assert sorted(c.aliases) == ["alpha", "beta"]
        assert len(c.aliases) == len(set(c.aliases)), f"duplicate aliases: {c.aliases}"

    def test_an_organisation_never_lists_its_own_slug_as_an_alias(self, db):
        from connect_labs.labs.models import LabsOrg
        from connect_labs.labs.org_merge import merge_orgs

        keep = LabsOrg.objects.create(slug="dimagi", name="Dimagi")
        merge = LabsOrg.objects.create(slug="other", name="Other", aliases=["dimagi"])
        merge_orgs(keep_id=keep.pk, merge_id=merge.pk)
        keep.refresh_from_db()
        assert "dimagi" not in keep.aliases
        assert keep.aliases == ["other"]
