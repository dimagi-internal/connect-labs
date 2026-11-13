"""Every opportunity Connect has delivered, ranked against Connect's cost-effectiveness models.

The models are Dimagi's own CEAs (dimagi-internal/connect-cea-models): CHC, MBW and
kangaroo care, each a published GiveWell chain changed only where Connect differs. Each
gives a multiple of GiveWell's benchmark per country, at the model's default price per
person. ``data/cea_models.json`` vendors those per-country results (the file names the
commit it came from); refresh it from that repo's ``planning/model_results.json``, never
by hand.

What this module adds is the opportunity's OWN price. A model's multiple is benefit per
person divided by cost per person, and every model here runs with GiveWell's leveraging
adjustment at 1 (``model_results.js`` asserts it), so re-pricing is exact:

    multiple at actual cost = model multiple x model cost per person / actual cost per person

The code is split along its seams, each module one concern:

* ``catalog``: which model values which delivery, and the models' vendored per-country results.
* ``reach``: what Pulse knows about an opportunity (people reached, where its visits were) and ``pooling``: where the
  spend of an opportunity that reached no one goes.
* ``delivery``: valuing one delivery with the models' own formulas.
* ``totals``: roll-ups (a portfolio, a programme, a partner, a country).
* ``build``: the ranking, every opportunity valued, pooled, placed and ordered.
* ``whatif``: the what-if, filters and ordering as pure functions; ``page``: the page's view of the ranking.
* ``detail``: one opportunity end to end.

Three inputs come from Pulse, and each is stated where it is shown:

* **Total cost**: everything paid for the opportunity, in USD -- worker pay and the
  organisation's per-service fee accrued on every completed work, plus fixed costs
  (Connect's custom invoices and costs entered in labs). `costs.opportunity_costs`.
* **People reached**: distinct beneficiaries with approved work. Each completed work
  carries a keyed hash of its beneficiary (``PulseWork.beneficiary_key``, see
  ``normalize.beneficiary_label``), so this counts each child, mother or baby once,
  across workers, visits and payment units -- whether the app pays per visit stage
  (MBW cohorts), per visit (MBW Nigeria) or per baby (KMC). Only approved work counts:
  Connect never approves work for a form without a deliver block, nor for a rejected,
  duplicate, over-limit or pending visit. ``works_per_person`` is approved work per
  person, a first look at how far through a longitudinal programme people got.
  Opportunities synced before the key existed fall back to the old proxy (approved
  works on the busiest payment unit), and say so in ``reach_basis``.
* **Where**: the opportunity's country, and the centre of its approved visits.

An opportunity whose delivery type has no model says "no model". One delivered outside
the models' 45 sub-Saharan countries says so too. Neither borrows another model."""

from __future__ import annotations

from connect_labs.effectiveness.implementation.build import (
    RANKING_CACHE_KEY,
    RANKING_CACHE_SECONDS,
    cached_ranking,
    ranking,
)
from connect_labs.effectiveness.implementation.catalog import (
    DATA,
    SERVICE_MODEL,
    STATUS_COST_POOLED,
    STATUS_MODELLED,
    STATUS_NO_COUNTRY,
    STATUS_NO_MODEL,
    STATUS_NOTHING_DELIVERED,
    STATUS_TEXT,
    iso3_for,
    models,
)
from connect_labs.effectiveness.implementation.delivery import value
from connect_labs.effectiveness.implementation.detail import opportunity_detail
from connect_labs.effectiveness.implementation.page import view
from connect_labs.effectiveness.implementation.reach import REACH_DISTINCT, REACH_PROXY
from connect_labs.effectiveness.implementation.totals import programmes, rollup
from connect_labs.effectiveness.implementation.whatif import (
    BASES,
    KNOB_DEFAULT,
    KNOBS,
    RANK_KEYS,
    apply_what_if,
    filters_from,
    passes,
    rank_rows,
    what_if_from,
)

__all__ = [
    "BASES",
    "DATA",
    "KNOBS",
    "KNOB_DEFAULT",
    "RANKING_CACHE_KEY",
    "RANKING_CACHE_SECONDS",
    "RANK_KEYS",
    "REACH_DISTINCT",
    "REACH_PROXY",
    "SERVICE_MODEL",
    "STATUS_COST_POOLED",
    "STATUS_MODELLED",
    "STATUS_NO_COUNTRY",
    "STATUS_NO_MODEL",
    "STATUS_NOTHING_DELIVERED",
    "STATUS_TEXT",
    "apply_what_if",
    "cached_ranking",
    "filters_from",
    "iso3_for",
    "models",
    "opportunity_detail",
    "passes",
    "programmes",
    "rank_rows",
    "ranking",
    "rollup",
    "value",
    "view",
    "what_if_from",
]
