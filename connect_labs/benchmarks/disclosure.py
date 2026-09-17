"""The disclosure rules. Everything published passes through here, once.

Anonymisation happens at publish time, server-side, before any row is written,
so everything downstream reads already-anonymous data.

The rules, and what each defends against:

  R1  >= min_peers contributing OPPORTUNITIES (distinct ids, never rows), or
      the indicator is withheld. Both public functions also refuse
      min_peers < 3. Two is not a floor: the reader of a benchmark is one of
      the two contributors, so the one remaining bar is a named peer's exact
      value -- anonymous only to someone outside the cohort, and nobody
      outside the cohort can read it.
  R2  a peer contributes only with denominator >= min_denominator; a rate over
      three babies is both noise and a fingerprint.
  R3  denominators are never returned. Opportunity sizes (100 .. 2,189 cases)
      are visible on the programme report, so n identifies instantly.
  R4  peers are sorted by value WITHIN each indicator. Ties break on a hash of
      a caller-supplied `tie_salt` (which must vary per indicator) and the
      opportunity id -- never on opportunity id alone, which at this cohort
      size ties constantly (rounded rates) and would otherwise reintroduce a
      stable, joinable cross-indicator order via sort stability. Both public
      functions reject an empty or whitespace-only `tie_salt` outright, since
      that degenerates to the same constant order for every indicator; a pure
      function cannot verify the salt actually varies across calls, so that
      part of the contract belongs to (and is enforced by) the caller.
  R5  a series period is published only where >= min_peers peers qualify, which
      is what stops a launch date naming the partner who launched then.
  R6  a series with a hole inside the window is dropped whole; gaps fingerprint.
      (A gap common to every peer -- a period outside the window entirely --
      fingerprints nobody, so that case is not a "hole" and is left alone.)
  R7  anything the registry suppressed never enters the set at all.

A single observation set (one call's worth of rows for one indicator, or one
period of a series) must never carry the same opportunity_id twice -- that
would let R1 count rows instead of peers, and silently choosing one of two
values for the same partner would misrepresent them. Both are data-safety
boundaries, so a duplicate raises rather than being resolved silently. The
publisher controls its input.

Accepted residual risk (Jonathan, 2026-09-14): R1-R7 reduce but do not
eliminate re-identification for per-peer SERIES -- a line's shape is durable in
a way a re-sortable bar is not. `anonymise_series` deliberately keeps one
peer_index constant across every period of a series (required for a line to
exist at all -- a consumer cannot connect points into a trend otherwise); that
is this same accepted risk, not a widening of it. Across indicators the
ordering still differs (different values, different `tie_salt`), which is what
R4 protects. See the spec's "Accepted residual risk".
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class PeerObservation:
    """One opportunity's value for one indicator (and period, for a series)."""

    opportunity_id: int
    value: float
    denominator: int
    # True when the semantic registry's gates say this figure must not be
    # published for this scope (credibility or input availability). R7.
    suppressed: bool = False


def _tie_key(tie_salt: str, opportunity_id: int) -> str:
    """A deterministic-but-salted tiebreaker for R4 -- never opportunity_id alone."""
    return hashlib.blake2b(f"{tie_salt}:{opportunity_id}".encode(), digest_size=8).hexdigest()


def _eligible(observations, *, min_denominator: int) -> list[PeerObservation]:
    """R7 then R2: drop suppressed peers, then peers with too thin a base.

    Raises if the same opportunity appears twice in this observation set: R1
    must count distinct peers, not rows, and silently keeping one of two rows
    for the same partner would misrepresent them. This is a data-safety
    boundary -- fail loudly. The publisher controls its input.
    """
    observations = list(observations)  # a generator would otherwise be drained by the loop below and yield []
    seen: set[int] = set()
    for o in observations:
        if o.opportunity_id in seen:
            raise ValueError(f"duplicate opportunity {o.opportunity_id} in one observation set")
        seen.add(o.opportunity_id)
    return [o for o in observations if not o.suppressed and o.denominator >= min_denominator]


def anonymise_point(
    observations, *, min_peers: int, min_denominator: int, tie_salt: str
) -> list[tuple[int, float, int]]:
    """`(peer_index, value, opportunity_id)` per surviving peer, or [] if withheld.

    The opportunity id is returned for the publisher to store as provenance; it
    is never part of what a viewer sees (see BenchmarkValue.to_public).

    `tie_salt` must be unique per indicator (e.g. include the indicator id) so
    that peers tied on value -- common at this cohort size -- are not ordered
    the same way in every indicator. See R4.
    """
    if min_peers < 3:
        raise ValueError("min_peers must be at least 3")
    if not tie_salt or not tie_salt.strip():
        raise ValueError("tie_salt must be a non-empty string that varies per indicator")
    eligible = _eligible(observations, min_denominator=min_denominator)
    if len({o.opportunity_id for o in eligible}) < min_peers:  # R1: distinct peers, not rows
        return []
    # R4: ordered by value within THIS indicator; ties broken via a per-indicator
    # salted hash, never via opportunity id alone (sort stability would
    # otherwise keep tied peers in the same, joinable order everywhere).
    ordered = sorted(eligible, key=lambda o: (o.value, _tie_key(tie_salt, o.opportunity_id)))
    # R3: denominator is deliberately not carried into the output.
    return [(i, o.value, o.opportunity_id) for i, o in enumerate(ordered)]


def anonymise_series(
    observations_by_period,
    *,
    min_peers: int,
    min_denominator: int,
    tie_salt: str,
    require_complete: bool = True,
) -> dict[str, list[tuple[int, float, int]]]:
    """`{period: [(peer_index, value, opportunity_id), ...]}` for a whole series.

    R5 establishes the common window, then R6 requires a peer to be present in
    EVERY period of it -- so no series starts late, ends early or has a hole.
    `require_complete=False` drops R6 alone (R1 and R5 still hold), which a
    cohort may choose when complete lines would leave it with no series at all;
    see the comment at R6 for exactly what that costs on a tenure axis.

    A single ordering is computed ONCE for the whole series (by each surviving
    peer's mean value across the in-window periods, tie-broken the same way as
    R4) and reused in every period, so peer_index N denotes the SAME peer in
    every period -- without that, a consumer cannot connect points into a line,
    which is the whole point of a series. `tie_salt` must be unique per
    indicator, same contract as `anonymise_point`.
    """
    if min_peers < 3:
        raise ValueError("min_peers must be at least 3")
    if not tie_salt or not tie_salt.strip():
        raise ValueError("tie_salt must be a non-empty string that varies per indicator")
    eligible_by_period = {
        period: _eligible(obs, min_denominator=min_denominator) for period, obs in observations_by_period.items()
    }
    # R5: the window is the periods enough distinct peers reached.
    window = {p for p, obs in eligible_by_period.items() if len({o.opportunity_id for o in obs}) >= min_peers}
    if not window:
        return {}
    # R6: only peers present in every in-window period survive -- unless the
    # cohort has switched that off.
    #
    # Complete lines are the safer default and were the original rule: a line
    # with a hole, or one that stops early, says something about that peer's
    # own history. But it is also what makes a series unpublishable for a real
    # cohort -- members join at different times, so the intersection of "in
    # every period" collapses to whoever has been running longest.
    #
    # The AXIS is what decides how much relaxing it costs, and it is worth being
    # exact. A period is a TENURE WEEK -- that opportunity's own Nth week of
    # delivering -- so no period names a date, whichever way this is set.
    #
    # With R6 ON every surviving line spans the same window, so no line's extent
    # says anything about the peer it belongs to. With R6 OFF a line's extent is
    # that peer's tenure, and for a peer still delivering (whose last point is
    # near the publication's as-of date) tenure dates its start to within a week.
    # That is a real disclosure, it is why ON is the default, and it is why a
    # cohort of real delivery partners should leave it on. OFF is for a cohort
    # that would otherwise have no series at all -- members join at different
    # times, so the intersection collapses to whoever has run longest.
    #
    # R5 still holds per period either way, so no period is published that too
    # few peers reached.
    per_period_ids = [{o.opportunity_id for o in eligible_by_period[p]} for p in window]
    complete = set.intersection(*per_period_ids) if require_complete else set.union(*per_period_ids)
    if len(complete) < min_peers:  # R1, re-checked after R6 thins the set
        return {}
    # One ordering for the whole series: each surviving peer's mean value
    # across the in-window periods, tie-broken via the same salted hash as R4.
    values_by_id: dict[int, list[float]] = {}
    for period in window:
        for o in eligible_by_period[period]:
            if o.opportunity_id in complete:
                values_by_id.setdefault(o.opportunity_id, []).append(o.value)
    mean_value = {oid: sum(vals) / len(vals) for oid, vals in values_by_id.items()}
    ordered_ids = sorted(complete, key=lambda oid: (mean_value[oid], _tie_key(tie_salt, oid)))
    peer_index = {oid: i for i, oid in enumerate(ordered_ids)}

    out = {}
    for period in sorted(window):
        kept = sorted(
            (o for o in eligible_by_period[period] if o.opportunity_id in complete),
            key=lambda o: peer_index[o.opportunity_id],
        )
        out[period] = [(peer_index[o.opportunity_id], o.value, o.opportunity_id) for o in kept]
    return out
