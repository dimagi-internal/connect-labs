from .models import AuditLog


def log_action(request, action, obj_type, obj_id, detail=None):
    AuditLog.objects.create(
        user=request.user if request.user.is_authenticated else None,
        action=action,
        obj_type=obj_type,
        obj_id=str(obj_id),
        detail=detail or {},
    )
