"""Canonical microplan sampling defaults — the SINGLE source of truth.

Every place that needs a default sampling knob reads from here, so changing a
value propagates everywhere at once:

  * the engine — ``FrameConfig`` field defaults + ``FrameConfig.from_payload``
    fallbacks (``commcare_connect/microplans/sampling/frame.py``);
  * the plan-creation UI — the ``{% sampling_default %}`` / ``{% sampling_defaults_json %}``
    template tags (``microplans/templatetags/microplans_extras.py``) render the
    input values + a ``<script id="sampling-defaults">`` blob the page JS reads for
    its fallbacks (``static/microplans/review.js``, ``rooftop_surveys/setup.html``);
  * the synthetic study — ``demo_config.json`` leaves ``study.sampling`` empty, so
    the study seeder inherits these via ``FrameConfig.from_payload``.

Best practice (WHO EPI / DHS / MICS two-stage cluster sampling):

  * ``target_clusters`` 30 — enough PSUs to keep the design effect low and the
    variance estimable (the WHO/EPI cluster-survey standard).
  * ``primary_per_psu`` 10 — a modest fixed take per PSU; with PPS selection this
    keeps the sample self-weighting, and it reliably fits under the 15 m household
    separation (~300 completed interviews per arm).
  * ``alternates_per_psu`` 5 — ranked non-response replacements (~50% reserve).
  * ``size_balance_bands`` 3 — size-stratified systematic PPS (DHS/MICS), so two
    arms are comparable on cluster size by construction.
  * ``min_confidence`` 0.7, ``area_min_m2`` 15, ``area_max_m2`` 330 — keep real
    residential footprints (drop tiny sheds + oversized non-dwellings).
"""

from __future__ import annotations

SAMPLING_DEFAULTS: dict = {
    "target_clusters": 30,
    "primary_per_psu": 10,
    "alternates_per_psu": 5,
    "size_balance_bands": 3,
    "min_confidence": 0.7,
    "area_min_m2": 15.0,
    "area_max_m2": 330.0,
}
