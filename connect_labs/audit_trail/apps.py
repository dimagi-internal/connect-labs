from django.apps import AppConfig


class AuditTrailConfig(AppConfig):
    name = "connect_labs.audit_trail"
    verbose_name = "Audit Trail"

    def ready(self):
        from connect_labs.audit_trail import signals  # noqa: F401
