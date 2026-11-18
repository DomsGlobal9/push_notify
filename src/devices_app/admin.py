from django.contrib import admin
from django.db.models import Q
from gateway.help import HelpButtonMixin
from unfold.admin import ModelAdmin, TabularInline
from unfold.contrib.filters.admin import RelatedDropdownFilter
from unfold.decorators import display

from .models import Device, DeviceTopic


class LinkedFilter(admin.SimpleListFilter):
    """Phones tied to a signed-in person vs phones registered anonymously
    (an app build that did not receive or keep the person's identity)."""

    title = "linked to a user"
    parameter_name = "linked"

    def lookups(self, request, model_admin):
        return [("yes", "Linked to a user"), ("no", "Anonymous")]

    def queryset(self, request, queryset):
        empty = Q(user_external_id__isnull=True) | Q(user_external_id="")
        if self.value() == "yes":
            return queryset.exclude(empty)
        if self.value() == "no":
            return queryset.filter(empty)
        return queryset


class DeviceTopicInline(TabularInline):
    model = DeviceTopic
    extra = 0


@admin.register(Device)
class DeviceAdmin(HelpButtonMixin, ModelAdmin):
    help_topic = "devices"
    actions_list = ["how_it_works"]
    list_display = ["installation_id", "platform_badge", "tenant", "user", "status", "app_version",
                    "last_seen_at"]
    list_filter = [LinkedFilter, "is_active", "app__platform", ("tenant", RelatedDropdownFilter), "apns_environment"]
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
