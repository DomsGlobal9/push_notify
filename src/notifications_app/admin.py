"""Notifications in the dashboard, with a "send test" form for operators."""

from django import forms
from django.contrib import admin, messages
from django.shortcuts import redirect, render
from django.urls import path

from src.tenants_app.models import Tenant

from . import services
from .models import DeliveryFailure, Notification


class DeliveryFailureInline(admin.TabularInline):
    model = DeliveryFailure
    extra = 0
    can_delete = False
    fields = ["provider", "status_code", "reason", "device", "created_at"]
    readonly_fields = fields
    max_num = 0
    show_change_link = False


class SendTestForm(forms.Form):
    tenant = forms.ModelChoiceField(Tenant.objects.all())
    user_id = forms.CharField(required=False, help_text="Leave empty to send to every device of the tenant")
    title = forms.CharField(max_length=200)
    body = forms.CharField(max_length=2000, widget=forms.Textarea(attrs={"rows": 3}))


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ["__str__", "tenant", "status", "targeted_count", "sent_count", "failed_count",
                    "invalid_token_count", "opened_count", "created_at"]
    list_filter = ["status", "tenant"]
    readonly_fields = [f.name for f in Notification._meta.fields]
    inlines = [DeliveryFailureInline]
    change_list_template = "admin/notifications_app/notification/change_list.html"

    def has_add_permission(self, request):
        return False

    def get_urls(self):
        return [path("send-test/", self.admin_site.admin_view(self.send_test), name="send-test")] + super().get_urls()

    def send_test(self, request):
        form = SendTestForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            d = form.cleaned_data
            target = {"user_ids": [d["user_id"]]} if d["user_id"] else {"all": True}
            n, _ = services.create(d["tenant"], target, {"title": d["title"], "body": d["body"],
                                                         "sound": "default", "priority": "high",
                                                         "ttl_seconds": 86400})
            messages.success(request, f"Queued notification {n.id}.")
            return redirect("admin:notifications_app_notification_change", n.id)
        return render(request, "admin/notifications_app/send_test.html",
                      {**self.admin_site.each_context(request), "form": form, "title": "Send a test notification"})
