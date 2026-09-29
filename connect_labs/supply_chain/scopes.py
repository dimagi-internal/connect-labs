"""Which programmes are synthetic, and what that permits.

One thing is gated on this: a synthetic programme's supply data may be
**purged wholesale**, which is what a seeder needs and what no real programme
may ever permit. It deliberately does NOT mean "less validated" -- synthetic
data goes in through the same operations with the same schemas, because a
seeder that can write a shape the API would reject produces demos of a system
that does not exist.

**Why this asks labs rather than checking the number.** It used to be
`program_id >= 10_000` and nothing else, which put this module in
disagreement with the rest of labs about the same word. `labs/synthetic`
requires a registered labs-only opportunity to exist under the programme
before it will call it labs-only, and that registration is what governs WHO
may see the programme at all (`allowed_domains`).

So a bare id in the reserved range satisfied the numeric rule while being
governed by nothing: labs' access check skipped it (not labs-only by labs'
rule) and there was no Connect membership to check either, because the
programme does not exist in Connect. Programme 10063 -- holding a real
supplier register -- was exactly that: destructible on the strength of a
number, and ungoverned as to who could touch it.

Delegating makes the two the same set. A programme that may be purged
wholesale is one somebody registered, and registering it is what says who it
belongs to. The floor is still necessary -- it is the reserved range -- but no
longer sufficient.
"""

from connect_labs.labs.synthetic.models import LABS_ONLY_OPP_ID_FLOOR

SYNTHETIC_FLOOR = LABS_ONLY_OPP_ID_FLOOR


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def is_synthetic(program_id) -> bool:
    """True for a REGISTERED labs-only programme.

    Fails closed twice over. An id that is not a number at all is not
    synthetic, and an id in the reserved range with no registered labs-only
    opportunity under it is not synthetic either -- because the only operation
    gated on this is destructive, and neither "we could not parse the scope"
    nor "nobody has said who this belongs to" may be the reason a purge is
    allowed.
    """
    numeric = _as_int(program_id)
    if numeric is None or numeric < SYNTHETIC_FLOOR:
        return False
    # Imported here rather than at module load: this module is imported by
    # data_access, and the backend imports the synthetic models.
    from connect_labs.labs.synthetic.local_records_backend import is_labs_only_program_id

    return is_labs_only_program_id(numeric)


def require_synthetic(program_id, action: str) -> None:
    """Raise unless this programme's data is safe to treat as disposable."""
    if not is_synthetic(program_id):
        raise ValueError(
            f"refusing to {action} for programme {program_id!r}: only labs-only "
            f"programmes (id >= {SYNTHETIC_FLOOR}) may be reset wholesale. "
            "Delete the specific records instead."
        )


def opportunity_problem(program_id, opportunity_id) -> str | None:
    """Why this programme may not read this opportunity's visits, or None.

    The synthetic guard above checks the PROGRAMME; the visits come from the
    OPPORTUNITY a dispensing rule names, and that is a separate number the
    caller supplies. A synthetic programme's rule naming a real opportunity
    would otherwise pull that opportunity's visits -- served from the shared
    visit cache with no Connect access check -- into the ledger. So the
    opportunity must itself be a registered labs-only one, and filed under
    this programme (the same test `is_labs_only_program_id` uses: its
    `program_id`, or, when that is unset, the opportunity is its own
    programme). Fails closed on anything unparseable.
    """
    from connect_labs.labs.synthetic.local_records_backend import is_labs_only_opportunity_id
    from connect_labs.labs.synthetic.models import SyntheticOpportunity

    opp = _as_int(opportunity_id)
    prog = _as_int(program_id)
    if opp is None or not is_labs_only_opportunity_id(opp):
        return (
            f"opportunity {opportunity_id!r} is not a registered labs-only opportunity: visits are read into "
            "the stock ledger only from synthetic opportunities"
        )
    registered = SyntheticOpportunity.objects.get(opportunity_id=opp)
    owner = registered.program_id if registered.program_id is not None else registered.opportunity_id
    if prog is None or owner != prog:
        return f"opportunity {opp} belongs to programme {owner}, not {program_id!r}"
    return None


def require_programme_opportunity(program_id, opportunity_id) -> None:
    """Raise unless `opportunity_id` is a labs-only opportunity of this programme."""
    problem = opportunity_problem(program_id, opportunity_id)
    if problem:
        raise ValueError(f"refusing to read visits: {problem}")
