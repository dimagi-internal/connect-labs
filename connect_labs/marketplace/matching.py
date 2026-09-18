"""Binding a submission to the organisation that made it.

Several hundred historical submissions have already been reconciled by hand,
matched on name, acronym, email and email domain, with every non-exact case
reviewed by a person. **That work is authoritative and is never recomputed
here.** A matcher clever enough to re-derive it is also clever enough to
re-derive it differently.

So only three things match automatically:

* a contact email on the organisation's directory record;
* an email from one of the organisation's *earlier* submissions that was itself
  matched -- exact, and already verified -- but only when the name submitted
  now still agrees with the organisation's (see `names_agree`). The same inbox
  applying as "Nama Health Impact" and earlier as "Nama Wellness Community
  Centre" may be a sister organisation, and that is a person's call;
* a normalised organisation name, compared with spacing folded away and a
  trailing acronym dropped ("Friends of the Community Organization FOCO").

Everything else stays unmatched and visible.

The cost of a wrong match is higher than a missing one. `partner_names.py`
already states the rule for a related problem -- *a wrong parent name is worse
than a visible slug* -- and here it is worse still: a misattributed submission
puts one organisation's application on another organisation's record, where it
will be read as that organisation's own words.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

# A trailing acronym restating the name: "... - CHAU", "... FOCO", '"ASMO"'.
# Case-insensitive: the directory itself spells one "...Programme-Kcbhcp" where
# its submission says "...Programme-KCBHCP", and both sides are normalised the
# same way, so only the initials test decides.
_TRAILING_TOKEN = re.compile(r"""[\s\-–—:,"'“”]+["'“”]?([A-Za-z0-9][A-Za-z0-9-]{1,11})["'“”]?\s*$""")

# How closely a submitted name must agree with an organisation before an email
# from that organisation's earlier submission is trusted. Calibrated on the
# 2024-26 rounds: genuine variants ("EHA CLINIC REACH PROGRAM" / "EHA Clinics
# (REACH Program)") score 0.57 and up, the two pairs that need a person
# ("Nama Health Impact" / "Nama Wellness Community Centre") score 0.46.
NAME_AGREEMENT = 0.55


def _drop_trailing_acronym(name: str) -> str:
    """Remove a final token that is only the initials of the words before it."""
    m = _TRAILING_TOKEN.search(name or "")
    if not m:
        return name or ""
    head = name[: m.start()]
    initials = "".join(w[0] for w in re.findall(r"[A-Za-z0-9]+", head)).lower()
    letters = re.sub(r"[^a-z0-9]", "", m.group(1).lower())
    it = iter(initials)
    # Initials in order, stop-words allowed to be skipped: FOCO from "Friends
    # Of The Community Organization".
    if len(letters) >= 2 and all(ch in it for ch in letters):
        return head
    return name


def normalise(name: str) -> str:
    """Case, accents, punctuation and legal suffixes folded away."""
    value = _drop_trailing_acronym(name or "")
    value = "".join(c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c))
    value = value.lower()
    value = re.sub(r"\((.*?)\)", " ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    value = re.sub(
        r"\b(ltd|limited|inc|llc|asbl|ongd|ong|ngo|org|organisation|organization|foundation|trust|initiative)\b",
        " ",
        value,
    )
    return re.sub(r"\s+", " ", value).strip()


def names_agree(submitted: str, org_name: str) -> bool:
    """Whether a submitted name plausibly names this organisation.

    A blank submitted name agrees: the question was not asked (the 2026 RUTF
    RFP form never asks it), so the email is all there is.
    """
    a, b = normalise(submitted), normalise(org_name)
    if not a:
        return True
    if not b:
        return False
    return SequenceMatcher(None, a, b).ratio() >= NAME_AGREEMENT or set(a.split()) <= set(b.split())


def match_submission(
    submission, *, by_email: dict, by_name: dict, human: dict | None = None, by_earlier_email: dict | None = None
):
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

    for email in submission.emails:
        org = (by_earlier_email or {}).get(email.lower())
        if org is not None and names_agree(submission.org_name, org.name):
            return org, SolicitationResponse.MATCH_EMAIL, f"email {email} from an earlier matched submission"

    key_name = normalise(submission.org_name)
    if key_name:
        org = by_name.get(key_name)
        if org is None:
            # "N'Domakeh" / "Ndomakeh", "Well-being" / "Wellbeing".
            squeezed = key_name.replace(" ", "")
            hits = {id(v): v for k, v in by_name.items() if v is not None and k.replace(" ", "") == squeezed}
            org = next(iter(hits.values())) if len(hits) == 1 else None
        if org is not None:
            return org, SolicitationResponse.MATCH_NAME, f"exact organisation name {submission.org_name!r}"

    return None, SolicitationResponse.MATCH_UNMATCHED, ""
