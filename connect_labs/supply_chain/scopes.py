"""Which programmes are synthetic, and what that permits.

Labs has one convention for "this is made-up data": an id at or above
`LABS_ONLY_OPP_ID_FLOOR` (10,000). Elsewhere in labs that floor decides
whether `LabsRecordAPIClient` talks to production Connect or to an in-process
local backend. Supply has no such fork any more -- every row is in the labs
database either way -- so the floor means something narrower here, and
saying exactly what it means is the point of this module:

  - a synthetic programme's supply data may be **purged wholesale**, which is
    what a seeder needs and what no real programme may ever permit;
  - a synthetic programme's data may be **excluded from aggregates**, so a
    demo round can never inflate a real figure.

It deliberately does NOT mean "less validated". Synthetic data goes in
through the same operations with the same schemas, because a seeder that can
write a shape the API would reject produces demos of a system that does not
exist.
"""

from connect_labs.labs.synthetic.models import LABS_ONLY_OPP_ID_FLOOR

SYNTHETIC_FLOOR = LABS_ONLY_OPP_ID_FLOOR


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def is_synthetic(program_id) -> bool:
    """True for a labs-only programme id.

    Fails closed: an id that is not a number at all is not treated as
    synthetic, because the only operation gated on this is destructive and
    "we could not parse the scope" must never be the reason a purge is
    allowed.
    """
    numeric = _as_int(program_id)
    return numeric is not None and numeric >= SYNTHETIC_FLOOR


def require_synthetic(program_id, action: str) -> None:
    """Raise unless this programme's data is safe to treat as disposable."""
    if not is_synthetic(program_id):
        raise ValueError(
            f"refusing to {action} for programme {program_id!r}: only labs-only "
            f"programmes (id >= {SYNTHETIC_FLOOR}) may be reset wholesale. "
            "Delete the specific records instead."
        )
