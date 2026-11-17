"""What delivering a programme has actually cost per person, by model and country, from the opportunities Labs knows.

The models price a person at one figure for every country (Child Health Campaign $1.84 a child; Kangaroo Care $60 a
baby). Delivered opportunities say what it really cost where they ran: the real Child Health Campaign opportunities in
Nigeria came in near $0.80 a child, under half the model's figure, and in DRC, Kenya and Uganda near $1.00. That is
evidence about delivery cost, and it can disagree with the model in either direction (Kangaroo Care's three programmes
came in at $29 a baby in Uganda, $42 in Nigeria and $70 in Kenya, on a few hundred to five thousand babies each).

So it is OFFERED, never applied unasked: a scenario with ``cost_basis=observed`` prices a place where there is
evidence at ``blend``, and every other place, and every scenario that does not ask, at the model's own figure. Two
reasons it is not the default. A small early programme's cost per person is not what the same programme costs at a
million (fixed costs and learning both move it), and one country's opportunities are a handful of organisations, not a
price list. ``blend`` therefore leans on the model's figure until there is a body of delivery behind the observed one.

Pure: rows come in as ``effectiveness.implementation.ranking()`` makes them.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from connect_labs.effectiveness.implementation.catalog import iso3_for

#: Opportunities of evidence at which the observed price counts for as much as the model's: with ``n`` opportunities
#: the observed price has weight ``n / (n + HALF_WEIGHT_AT)``. Two means one opportunity counts a third, three a
#: 60%, ten over 80%.
HALF_WEIGHT_AT = 2


@dataclass(frozen=True)
class Observed:
    """What a model's delivery cost per person where its opportunities ran."""

    cost_per_person: float
    people: int
    opportunities: int
    spend: float

    @property
    def weight(self) -> float:
        return self.opportunities / (self.opportunities + HALF_WEIGHT_AT)


def from_rows(rows: Iterable[Mapping]) -> dict[str, dict[str, Observed]]:
    """``{model: {iso3: Observed}}`` from ranking rows that delivered something (a spend and people reached).

    The price is total spend over total people reached, so a large opportunity counts for more than a small one, and an
    opportunity whose cost was pooled into another (it says so in its status) is left out rather than counted twice.
    """
    spend: dict[tuple, float] = defaultdict(float)
    people: dict[tuple, int] = defaultdict(int)
    count: dict[tuple, int] = defaultdict(int)
    for r in rows:
        key_model = r.get("model")
        iso3 = iso3_for(r.get("country") or "")
        if (
            not key_model
            or not iso3
            or r.get("status") != "modelled"
            or not r.get("reached")
            or not r.get("total_usd")
        ):
            continue
        k = (key_model, iso3)
        spend[k] += float(r["total_usd"])
        people[k] += int(r["reached"])
        count[k] += 1
    out: dict[str, dict[str, Observed]] = defaultdict(dict)
    for (model, iso3), usd in spend.items():
        out[model][iso3] = Observed(usd / people[(model, iso3)], people[(model, iso3)], count[(model, iso3)], usd)
    return dict(out)


def blend(model_price: float, observed: Observed | None) -> float:
    """The price per person to use: the model's, moved toward the observed one by how much delivery backs it."""
    if observed is None:
        return model_price
    return model_price + observed.weight * (observed.cost_per_person - model_price)
