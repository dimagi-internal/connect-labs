"""
Admin configuration for audit app.

All audit models have been migrated to the ExperimentRecord-based implementation.
ExperimentRecord is registered in the labs app admin.

See:
- experiment_models.py for proxy models
- labs/admin.py for ExperimentRecord admin registration
"""
