"""Notifications in the dashboard, with a "Send test notification" button."""

from django import forms
from django.contrib import admin, messages
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.html import format_html
from unfold.admin import ModelAdmin, TabularInline
from unfold.contrib.filters.admin import RelatedDropdownFilter
from unfold.decorators import action, display
from unfold.enums import ActionVariant
from unfold.widgets import UnfoldAdminSelectWidget, UnfoldAdminTextareaWidget, UnfoldAdminTextInputWidget

from src.tenants_app.models import Tenant

from . import services
from .models import DeliveryFailure, Notification


class DeliveryFailureInline(TabularInline):
    model = DeliveryFailure
    extra = 0
    can_delete = False
    fields = ["provider", "status_code", "reason", "device", "created_at"]
    readonly_fields = fields
    max_num = 0
    tab = True
    verbose_name_plural = "Failures"


class SendTestForm(forms.Form):
    tenant = forms.ModelChoiceField(Tenant.objects.all(), label="Product", widget=UnfoldAdminSelectWidget)
    user_id = forms.CharField(required=False, label="User id", widget=UnfoldAdminTextInputWidget,
                              help_text="Leave empty to send to every device of the product")
    title = forms.CharField(max_length=200, widget=UnfoldAdminTextInputWidget)
    body = forms.CharField(max_length=2000, widget=UnfoldAdminTextareaWidget(attrs={"rows": 3}))
    route = forms.CharField(required=False, label="Open on tap", widget=UnfoldAdminTextInputWidget,
                            help_text="Optional in-app path, e.g. /orders or /notifications")


@admin.register(Notification)
class NotificationAdmin(ModelAdmin):
    list_display = ["message_title", "tenant", "status_badge", "progress", "sent", "failures",
                    "opened", "created_at"]
    list_filter = ["status", ("tenant", RelatedDropdownFilter)]
    search_fields = ["message__title"]
    list_filter_submit = True
    readonly_fields = [f.name for f in Notification._meta.fields]
    inlines = [DeliveryFailureInline]
    actions_list = ["send_test"]
    list_fullwidth = True
    list_select_related = ["tenant"]

    def has_add_permission(self, request):
        return False

    @display(description="Message")
    def message_title(self, obj):
        m = obj.message or {}
        return (m.get("title") or m.get("body") or "(data only)")[:60]

    @display(description="Status", label={"completed": "success", "processing": "info",
                                          "queued": "warning", "cancelled": "danger"})
    def status_badge(self, obj):
        return obj.status

    @display(description="Progress")
    def progress(self, obj):
        done = obj.sent_count + obj.failed_count + obj.invalid_token_count + obj.skipped_count
        pct = 100 if (obj.status == Notification.COMPLETED and not obj.targeted_count) else (
            round(100 * done / obj.targeted_count) if obj.targeted_count else 0)
        return format_html('<div class="pg-progress" title="{}%"><span style="width:{}%"></span></div>', pct, pct)

    @display(description="Sent", ordering="sent_count")
    def sent(self, obj):
        return f"{obj.sent_count} / {obj.targeted_count}"

    @display(description="Opened", ordering="opened_count")
    def opened(self, obj):
        return obj.opened_count

    @display(description="Failed")
    def failures(self, obj):
        n = obj.failed_count + obj.invalid_token_count
        return format_html('<span class="pg-bad">{}</span>', n) if n else 0

    @action(description="Send test notification", icon="send", url_path="send-test",
            variant=ActionVariant.PRIMARY)
    def send_test(self, request):
        form = SendTestForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            d = form.cleaned_data
            target = {"user_ids": [d["user_id"]]} if d["user_id"] else {"all": True}
            message = {"title": d["title"], "body": d["body"], "sound": "default", "priority": "high",
                       "ttl_seconds": 86400, "data": {"route": d["route"]} if d["route"] else {}}
            n, _ = services.create(d["tenant"], target, message)
            messages.success(request, f"Queued notification {n.id}.")
            return redirect(reverse("admin:notifications_app_notification_change", args=[n.id]))
        return TemplateResponse(request, "admin/notifications_app/send_test.html", {
            **self.admin_site.each_context(request),
            "form": form, "title": "Send a test notification", "opts": self.model._meta,
        })
