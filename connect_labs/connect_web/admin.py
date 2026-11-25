from django import forms
from django.contrib import admin

from .models import PoolWorker, WorkerSession


class PoolWorkerForm(forms.ModelForm):
    """The backup code and PIN are write-only: never rendered back, kept when left blank."""

    new_backup_code = forms.CharField(
        label="Backup code",
        required=False,
        strip=True,
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
        help_text="Leave blank to keep the saved code. Saving a new one resumes a paused sign-in.",
    )
    new_pin = forms.CharField(
        label="PIN",
        required=False,
        strip=True,
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
        help_text="Leave blank to keep the saved PIN.",
    )

    class Meta:
        model = PoolWorker
        fields = ["display_name", "phone_number", "personalid_username", "active", "sign_in_blocked_reason"]

    def clean(self):
        data = super().clean()
        if not self.instance.pk and not data.get("new_backup_code"):
            self.add_error("new_backup_code", "A new pool worker needs a backup code.")
        return data

    def save(self, commit=True):
        worker = super().save(commit=False)
        if code := self.cleaned_data.get("new_backup_code"):
            worker.backup_code = code
            worker.sign_in_blocked_reason = ""
        if pin := self.cleaned_data.get("new_pin"):
            worker.pin = pin
        if commit:
            worker.save()
        return worker


@admin.register(PoolWorker)
class PoolWorkerAdmin(admin.ModelAdmin):
    form = PoolWorkerForm
    list_display = ("display_name", "phone_number", "personalid_username", "active", "sign_in_blocked_reason")
    list_filter = ("active",)
    search_fields = ("display_name", "phone_number", "personalid_username")
    readonly_fields = ("device_id", "created_at", "updated_at")


@admin.register(WorkerSession)
class WorkerSessionAdmin(admin.ModelAdmin):
    list_display = ("pk", "pool_worker", "user", "started_at", "last_used_at", "ended_at")
    list_filter = ("ended_at",)
    search_fields = ("pool_worker__display_name", "user__username", "user__email")
    # Secrets (password, tokens) are never shown or editable here.
    fields = ("pool_worker", "user", "started_at", "last_used_at", "ended_at")
    readonly_fields = fields

    def has_add_permission(self, request):
        return False
