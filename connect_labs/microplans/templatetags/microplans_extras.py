"""Template helpers exposing the canonical sampling defaults to the UI.

Both the microplans plan-creation form (``microplans/review.html``) and the
rooftop-surveys setup page (``rooftop_surveys/setup.html``) render their default
knob values + a JSON blob from these tags, so the only place a default lives is
``microplans/sampling/defaults.py``.

Usage::

    {% load microplans_extras %}
    <input id="cfg-target-clusters" value="{% sampling_default 'target_clusters' %}">
    {% sampling_defaults_json %}   {# emits <script id="sampling-defaults"> for page JS #}
"""

from __future__ import annotations

from django import template
from django.utils.html import json_script

from connect_labs.microplans.sampling.defaults import SAMPLING_DEFAULTS

register = template.Library()


@register.simple_tag
def sampling_default(key: str):
    """One canonical default knob, e.g. ``{% sampling_default 'primary_per_psu' %}``."""
    return SAMPLING_DEFAULTS[key]


@register.simple_tag
def sampling_defaults_json():
    """Emit the full defaults dict as a JSON script tag the page JS reads for its
    fallbacks: ``JSON.parse(document.getElementById('sampling-defaults').textContent)``.
    ``json_script`` escapes ``<``/``>``/``&`` so the embedded JSON is XSS-safe + valid."""
    return json_script(SAMPLING_DEFAULTS, "sampling-defaults")
