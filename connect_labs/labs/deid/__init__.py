"""What in a submitted form identifies someone, and how to make a copy that does not.

One place for the rules that used to live in three: the synthetic profiler (which
fields may never reach a clone), the Data page's field index (which values may be
shown), and the coach charts' peer-name check (which words would name someone).
The de-identified export (``labsdata.exports``) reads the same rules, so a field the
cloner refuses to copy is a field the export drops by default.

* ``fieldscan`` -- the one walk over ``form_json``: every leaf with its path, repeat
  groups as ``[]``, and the app build a form came from.
* ``classify`` -- what kind of value a field holds (an identifier, free text, a GPS
  point, a case id, a date, a number, a category...), from its path and the values
  seen, and the setting that kind gets by default.
* ``transforms`` -- the settings themselves (keep, drop, round, pseudonymise, month,
  shift, coarsen, scrub), keyed so the same input always gives the same output.
* ``leaks`` -- the words that would identify someone, and a search for them.

Pure functions over dicts and strings: no Django models, no numpy, so any module can
import them without pulling in the generator.
"""
