"""Coaching charts: generative pictures in Connect's style.

An agent (or a person) decides WHAT a chart shows -- a named type and its params, or
a Vega-Lite spec of its own; Labs supplies every number (from the run's grading and
history) and draws it in Connect's theme. See WORKFLOW_REFERENCE.md, "Coaching charts".

* ``theme.py``  -- Connect's design tokens and the Vega-Lite config every chart gets;
* ``types.py``  -- the named chart types (the style library);
* ``render.py`` -- the spec plus its data, themed, as a PNG (vl-convert);
* ``datasets.py`` -- Labs' named datasets (``worker_topics``, ``peers``, ``history``),
  peers anonymous;
* ``custom.py`` -- an agent's own spec, stripped to Labs' data and Connect's look;
* ``chart.py``  -- a request (type + params, or a custom spec) to a frozen chart.
"""
