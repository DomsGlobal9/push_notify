from django.contrib import admin
from django.utils import timezone
from gateway.help import HelpButtonMixin
from unfold.admin import ModelAdmin
from unfold.decorators import display

from .models import Job


@admin.register(Job)
class JobAdmin(HelpButtonMixin, ModelAdmin):
    help_topic = "queue"
    actions_list = ["how_it_works"]
    list_display = ["id", "kind", "status_badge", "attempts", "run_at", "locked_at", "short_error"]
    list_filter = ["status", "kind"]
    readonly_fields = ["kind", "payload", "run_at", "attempts", "locked_at", "last_error", "created_at"]
    actions = ["requeue"]
    list_fullwidth = True

    @display(description="Status", label={"pending": "info", "running": "warning", "failed": "danger"})
    def status_badge(self, obj):
        return obj.status

    @display(description="Last error")
    def short_error(self, obj):
        return (obj.last_error or "")[:80]

    @admin.action(description="Requeue selected jobs now")
    def requeue(self, request, queryset):
        queryset.update(status=Job.PENDING, locked_at=None, run_at=timezone.now())
