"""Binding a submission to the organisation that made it.

Several hundred historical submissions have already been reconciled by hand,
matched on name, acronym, email and email domain, with every non-exact case
reviewed by a person. **That work is authoritative and is never recomputed
here.** A matcher clever enough to re-derive it is also clever enough to
re-derive it differently.

So only two things match automatically, and both are exact: a contact email, and
a normalised organisation name. Everything else stays unmatched and visible.

The cost of a wrong match is higher than a missing one. `partner_names.py`
already states the rule for a related problem -- *a wrong parent name is worse
than a visible slug* -- and here it is worse still: a misattributed submission
puts one organisation's application on another organisation's record, where it
will be read as that organisation's own words.
"""

from __future__ import annotations

import re
import unicodedata


def normalise(name: str) -> str:
    """Case, accents, punctuation and legal suffixes folded away."""
    value = "".join(c for c in unicodedata.normalize("NFKD", name or "") if not unicodedata.combining(c))
    value = value.lower()
    value = re.sub(r"\((.*?)\)", " ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    value = re.sub(
        r"\b(ltd|limited|inc|llc|asbl|ongd|ong|ngo|org|organisation|organization|foundation|trust|initiative)\b",
        " ",
        value,
    )
    return re.sub(r"\s+", " ", value).strip()


def match_submission(submission, *, by_email: dict, by_name: dict, human: dict | None = None):
    """(org, state, basis) for one submission. `org` is None when unmatched.

    `human` maps "<round slug>:<row>" to (org | None, verdict, why), carrying a
    decision a person already reached. It outranks every inference, exactly as
    the curated slug mapping outranks the name matcher in pulse — including the
    verdict that a submission is not an organisation at all.
    """
    from connect_labs.solicitations.local_models import SolicitationResponse

    key = f"{getattr(submission, 'round_slug', '')}:{submission.source_row}"
    if human and key in human:
        from connect_labs.marketplace.directory import VERDICT_NOT_LLO

        org, verdict, why = human[key]
        state = SolicitationResponse.MATCH_NOT_LLO if verdict == VERDICT_NOT_LLO else SolicitationResponse.MATCH_HUMAN
        return org, state, why

    for email in submission.emails:
        org = by_email.get(email.lower())
        if org is not None:
            return org, SolicitationResponse.MATCH_EMAIL, f"exact contact email {email}"

    key_name = normalise(submission.org_name)
    if key_name:
        org = by_name.get(key_name)
        if org is not None:
            return org, SolicitationResponse.MATCH_NAME, f"exact organisation name {submission.org_name!r}"

    return None, SolicitationResponse.MATCH_UNMATCHED, ""
