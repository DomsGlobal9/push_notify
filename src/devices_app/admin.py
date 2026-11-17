from django.contrib import admin
from gateway.help import HelpButtonMixin
from unfold.admin import ModelAdmin, TabularInline
from unfold.contrib.filters.admin import RelatedDropdownFilter
from unfold.decorators import display

from .models import Device, DeviceTopic


class DeviceTopicInline(TabularInline):
    model = DeviceTopic
    extra = 0


@admin.register(Device)
class DeviceAdmin(HelpButtonMixin, ModelAdmin):
    help_topic = "devices"
    actions_list = ["how_it_works"]
    list_display = ["installation_id", "platform_badge", "tenant", "user", "status", "app_version",
                    "last_seen_at"]
    list_filter = ["is_active", "app__platform", ("tenant", RelatedDropdownFilter), "apns_environment"]
    list_filter_submit = True
    search_fields = ["installation_id", "user_external_id"]
    readonly_fields = ["tenant", "app", "installation_id", "token", "created_at", "last_seen_at"]
    inlines = [DeviceTopicInline]
    list_select_related = ["app", "tenant"]
    show_full_result_count = False
    list_fullwidth = True

    @display(description="Platform", label={"Android": "success", "iOS": "info"})
    def platform_badge(self, obj):
        return obj.app.get_platform_display()

    @display(description="User")
    def user(self, obj):
        return obj.user_external_id or "anonymous"

    @display(description="Status", label={"Active": "success", "Dead token": "danger", "Inactive": "warning"})
    def status(self, obj):
        if obj.is_active:
            return "Active"
        return "Dead token" if obj.deactivated_reason == "provider_rejected_token" else "Inactive"
