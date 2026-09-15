"""The disclosure rules. Everything published passes through here, once.

Anonymisation happens at publish time, server-side, before any row is written,
so everything downstream reads already-anonymous data.

The rules, and what each defends against:

  R1  >= min_peers contributing opportunities, or the indicator is withheld.
  R2  a peer contributes only with denominator >= min_denominator; a rate over
      three babies is both noise and a fingerprint.
  R3  denominators are never returned. Opportunity sizes (100 .. 2,189 cases)
      are visible on the programme report, so n identifies instantly.
  R4  peers are sorted by value WITHIN each indicator, so the index carries no
      identity and cannot be joined across indicators into a profile.
  R5  a series period is published only where >= min_peers peers qualify, which
      is what stops a launch date naming the partner who launched then.
  R6  a series with a hole inside the window is dropped whole; gaps fingerprint.
  R7  anything the registry suppressed never enters the set at all.

Accepted residual risk (Jonathan, 2026-09-14): R1-R7 reduce but do not
eliminate re-identification for per-peer SERIES -- a line's shape is durable in
a way a re-sortable bar is not. See the spec's "Accepted residual risk".
"""

from __future__ import annotations

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


def _eligible(observations, *, min_denominator: int) -> list[PeerObservation]:
    """R7 then R2: drop suppressed peers, then peers with too thin a base."""
    return [o for o in observations if not o.suppressed and o.denominator >= min_denominator]


def anonymise_point(observations, *, min_peers: int, min_denominator: int) -> list[tuple[int, float, int]]:
    """`(peer_index, value, opportunity_id)` per surviving peer, or [] if withheld.

    The opportunity id is returned for the publisher to store as provenance; it
    is never part of what a viewer sees (see BenchmarkValue.to_public).
    """
    eligible = _eligible(observations, min_denominator=min_denominator)
    if len(eligible) < min_peers:  # R1
        return []
    # R4: ordered by value within THIS indicator, so the index is not stable
    # across indicators. Ties break on value only, never on opportunity id --
    # an id tiebreak would reintroduce a stable order.
    ordered = sorted(eligible, key=lambda o: o.value)
    # R3: denominator is deliberately not carried into the output.
    return [(i, o.value, o.opportunity_id) for i, o in enumerate(ordered)]


def anonymise_series(observations_by_period, *, min_peers: int, min_denominator: int):
    """`{period: [(peer_index, value, opportunity_id), ...]}` for a whole series.

    R5 establishes the common window, then R6 requires a peer to be present in
    EVERY period of it -- so no series starts late, ends early or has a hole.
    """
    eligible_by_period = {
        period: _eligible(obs, min_denominator=min_denominator) for period, obs in observations_by_period.items()
    }
    # R5: the window is the periods enough peers reached.
    window = {p for p, obs in eligible_by_period.items() if len(obs) >= min_peers}
    if not window:
        return {}
    # R6: only peers present in every in-window period survive.
    per_period_ids = [{o.opportunity_id for o in eligible_by_period[p]} for p in window]
    complete = set.intersection(*per_period_ids)
    if len(complete) < min_peers:  # R1, re-checked after R6 thins the set
        return {}
    out = {}
    for period in sorted(window):
        kept = [o for o in eligible_by_period[period] if o.opportunity_id in complete]
        # R4 applies per period as well as per indicator.
        ordered = sorted(kept, key=lambda o: o.value)
        out[period] = [(i, o.value, o.opportunity_id) for i, o in enumerate(ordered)]
    return out
