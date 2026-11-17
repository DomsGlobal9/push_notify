from django.contrib import admin

from .models import Job


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = ["id", "kind", "status", "attempts", "run_at", "locked_at", "short_error"]
    list_filter = ["status", "kind"]
    readonly_fields = ["kind", "payload", "run_at", "attempts", "locked_at", "last_error", "created_at"]
    actions = ["requeue"]

    @admin.display(description="Last error")
    def short_error(self, obj):
        return (obj.last_error or "")[:80]

    @admin.action(description="Requeue selected jobs now")
    def requeue(self, request, queryset):
        from django.utils import timezone
        queryset.update(status=Job.PENDING, locked_at=None, run_at=timezone.now())
