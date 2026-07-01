"""Deterministic synthetic data generator for labs synthetic opportunities.

Three layers:
  - ``core``     — pure, dependency-free reusable libs (survey simulation +
    survey-quality metrics). No Django/DB/IO.
  - ``fixtures`` — the manifest -> CommCare-form fixture pipeline. Public entry:
    ``fixtures.engine.generate(manifest, opportunity_detail, form_schema)``
    which returns the five fixture dicts the labs synthetic system serves.
  - ``io``       — the Django + GDrive boundary (``io.uploader``).
"""
