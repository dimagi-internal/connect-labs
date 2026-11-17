# Reaching one million Kangaroo Care cases at the best cost-effectiveness

What the Market size page and `cea_market_size` say on 17 November 2026, on production data, and how far to believe it. Every
figure comes from the scenarios below (change the link's keys to rerun one); none is a forecast.

## The question

Which places should a Kangaroo Care programme work in to reach one million low-birthweight newborns a year at the best
cost-effectiveness, if no more than half of a place's cases can be reached?

## The answer, three ways

All three: Kangaroo Care, 455 territories in 41 countries, `target=1000000`, `grain=territory`, reach capped at 50% of a place's
cases, $60 a baby from the model, GiveWell's bar 6x.

| scenario | places | countries | cost a year | deaths averted a year | per death | multiple |
| --- | --- | --- | --- | --- | --- | --- |
| the model as published (`place_risk=0`: the same 13% risk everywhere) | 38 | 11 | $60.0M | 17,680 | $3,394 | 10.3x |
| risk scaled to each place's newborn mortality (the default) | 145 | 23 | $60.0M | 23,077 | $2,600 | 13.5x |
| as above, held to conflict at most 10 per 100,000 and at most 40% of people beyond two hours' walk of care, priced from delivered programmes where there are some | 117 | 25 | $52.1M | 22,014 | $2,368 | 14.8x |

Choosing the places is worth about a third more deaths averted than spreading the same million evenly (the second row: 23,077
against 16,834). Under the model's own flat risk every place looks alike, so the page cannot say where; that is why the risk is
scaled, and it is the first thing to question (below).

## What the plan looks like (second row)

* Nigeria supplies 575,000 of the million at 14.2x across 37 states; Mozambique 57,000, Mali 51,000, Chad 43,000, Niger 41,000
  (the best territories: Niger's Dosso at 21.2x, Tillaberi 18.7x and Maradi 18.5x; Cote d'Ivoire's Denguele 18.9x; Nigeria's
  Kebbi 15.8x).
* It takes at least six countries to reach a million at all with half of each place's cases.
* Concentrating costs little: the plan at half trust in the place figures keeps 100% of the deaths (22,122 of 22,194).

## How far to trust it

1. **The risk scaling is Labs' reading, not the model's.** The model's owner holds the risk of an enrolled small baby at 13%
   everywhere (30 October 2026). Labs scales it by each place's newborn mortality, anchored on the model's own three country
   scenarios (Nigeria 18%, Liberia 15.2%, Uganda 9.6%: each is 4.5 to 4.6 times the country's newborn mortality per 1,000, so
   13% sits at newborn mortality 28.4). Without it the page cannot tell places apart. It needs the owner's agreement before the
   numbers are quoted as the model's.
2. **Newborn mortality is estimated, with wide intervals.** The UN's subnational model gives a 90% interval, and territory
   figures are pulled toward their country's by as much as the interval warrants. Redrawing the mortality within its intervals,
   the plan averts 20,833 to 26,273 deaths (90%), and 87% of its people are in the best plan on a typical redraw.
   Territories are also levelled to the country's current UN figure where every unit of the country has one (11 countries; factors
   0.70 to 1.13); a factor beyond 1.5 is flagged on the row.
3. **730,000 of the million rest on a regional low-birthweight rate.** UNICEF-WHO publish no national figure for Chad, Guinea,
   Mali, Niger and Nigeria, so their counts use the subregion's. Our births totals are within 15% of the UN's in every country.
4. **The $60 a baby is the model's figure, and delivery differs.** The delivered Kangaroo Care programmes in Labs came in at $29
   in Uganda (7 opportunities, about 5,500 babies), $42 in Nigeria (2, 370 babies) and $70 in Kenya (2, 450 babies). They are small
   and early, so the third row uses them only as evidence, weighted by how much delivery stands behind each (one opportunity counts
   a third). That row's cheaper price is why it is cheaper per death.
5. **Conflict and remoteness are ceilings, not scores.** Reported conflict deaths (UCDP, the last three years) leave out 17
   territories at 10 per 100,000, and the travel-time surface leaves out 100 at 40% of people beyond two hours' walk. A reported
   figure is a floor where few report. Neither changes a multiple, and a territory with no figure is kept.

## Not modelled

Existing Kangaroo Care coverage (a place already served), facility capacity or the supply of trained staff, whether the effect of
the intervention differs by place, and roads or seasons. The plan says where the need and the price are favourable, not whether a
team can deliver there.

## Rerun

On `/labs/cea/market/`, or `cea_market_size` with `model=kc, indicator=all, grain=territory, target=1000000` and, for the third
row, `max_conflict=10, max_remote=40, cost_basis=observed`; `place_risk=0` gives the first. The page's link carries the scenario.
