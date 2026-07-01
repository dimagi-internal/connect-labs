"""
Admin configuration for tasks (ExperimentRecord-based).

For labs experiments using ExperimentRecord, admin registration
is handled at the labs app level. This file exists to satisfy
Django's app structure but doesn't register any models.

Old Django ORM model admin is preserved in admin_old.py for reference.
"""

# No admin registration needed for ExperimentRecord-based tasks
# ExperimentRecord is registered in the labs app admin
