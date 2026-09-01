"""The retired Muac Picture Audit's two extra verdicts still have to READ back.

That workflow's review screen was the only writer of per-image ``result`` values
of "duplicate" and "fake" -- every other screen writes the combined
"duplicate_fake". #1385 removed the screen and deliberately did NOT migrate the
stored values: the one-off analysis is finished, but its conclusions are the
record, and collapsing duplicate-vs-fake is irreversible for no benefit.

So the shared review screen has to accept all three on read, and the failure mode
if it does not is quiet rather than loud: ``tally_assessment`` counts a legacy
value in the duplicate_fake bucket, so the SUMMARY of an old report stays
correct, while the per-image control highlights only on an exact
"duplicate_fake" match. The report would then claim N flagged images and render
every one of them as unreviewed, with no error anywhere.

These tests pin the coupling that prevents that -- the list the template reads
from must be a subset of what the server already treats as a duplicate/fake
verdict. A future edit that adds a value to one side and not the other fails
here instead of in front of an auditor.
"""

from connect_labs.audit.data_access import _AUDIT_VERDICTS
from connect_labs.audit.models import new_assessment_bucket, tally_assessment
from connect_labs.audit.prior_audit_models import AUDIT_VERDICTS
from connect_labs.audit.views import LEGACY_DUPLICATE_FAKE_RESULTS


def _count(result):
    bucket = new_assessment_bucket()
    tally_assessment(bucket, {"result": result})
    return bucket


class TestALegacyVerdictCountsAsDuplicateFake:
    """The summary half -- already true before #1385, pinned so it stays true."""

    def test_every_legacy_value_lands_in_the_duplicate_fake_bucket(self):
        for result in LEGACY_DUPLICATE_FAKE_RESULTS:
            assert _count(result)["duplicate_fake"] == 1, result

    def test_a_legacy_value_is_never_counted_as_pending(self):
        """Pending is the bucket a *missed* value falls into.

        This is the assertion that actually catches the regression: an
        unrecognised result is not an error, it is silently "unreviewed".
        """
        for result in LEGACY_DUPLICATE_FAKE_RESULTS:
            assert _count(result)["pending"] == 0, result

    def test_the_combined_value_still_counts_the_same_way(self):
        assert _count("duplicate_fake")["duplicate_fake"] == 1


class TestTheTemplateListStaysInStepWithTheServer:
    """The coupling #1385 introduced, made enforceable rather than a comment.

    ``LEGACY_DUPLICATE_FAKE_RESULTS`` is handed to bulk_assessment.html and drives
    both the per-image Duplicate/Fake highlight and the status filter. If it ever
    names a value the server does not treat as a verdict, the screen offers a
    state the rest of the system will not honour.
    """

    def test_every_legacy_value_is_a_recognised_verdict(self):
        for result in LEGACY_DUPLICATE_FAKE_RESULTS:
            assert result in _AUDIT_VERDICTS, result
            assert result in AUDIT_VERDICTS, result

    def test_the_list_does_not_include_the_combined_value(self):
        """The template checks it separately; duplicating it here would be dead weight."""
        assert "duplicate_fake" not in LEGACY_DUPLICATE_FAKE_RESULTS

    def test_the_list_is_not_empty(self):
        """A revert that empties this list silently un-does the read-compat."""
        assert LEGACY_DUPLICATE_FAKE_RESULTS
