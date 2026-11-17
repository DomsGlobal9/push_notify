from django.contrib import admin

from .models import Device, DeviceTopic


class DeviceTopicInline(admin.TabularInline):
    model = DeviceTopic
    extra = 0


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = ["installation_id", "app", "tenant", "user_external_id", "is_active",
                    "deactivated_reason", "app_version", "last_seen_at"]
    list_filter = ["is_active", "app__platform", "tenant", "apns_environment"]
    search_fields = ["installation_id", "user_external_id"]
    readonly_fields = ["tenant", "app", "installation_id", "token", "created_at", "last_seen_at"]
    inlines = [DeviceTopicInline]
    list_select_related = ["app", "tenant"]
    show_full_result_count = False
