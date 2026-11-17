"""Products (tenants), their apps and keys, and provider credentials in the dashboard.

Secrets are write-only: a credential's form takes the service account JSON or the
.p8 text and encrypts it on save; lists show only non-secret facts (project id,
team id). New tenant keys appear once, in the success message.
"""

import json

from django import forms
from django.contrib import admin, messages
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group, User
from django.db.models import Count, Q
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.html import format_html, format_html_join
from gateway.help import HelpButtonMixin
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import action, display
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm
from unfold.widgets import UnfoldAdminTextareaWidget, UnfoldAdminTextInputWidget

from src.core import crypto

from . import services
from .models import ApiKey, App, Credential, Tenant

# ---------------------------------------------------------------- admin users
admin.site.unregister(User)
admin.site.unregister(Group)


@admin.register(User)
class UserAdmin(HelpButtonMixin, BaseUserAdmin, ModelAdmin):
    help_topic = "admin-users"
    actions_list = ["how_it_works"]
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm
    list_display = ["username", "email", "is_superuser", "is_active", "last_login"]


@admin.register(Group)
class GroupAdmin(BaseGroupAdmin, ModelAdmin):
    pass


# ---------------------------------------------------------------- helpers
def _show_once(request, title, secrets: dict):
    lines = format_html_join("", "<br><b>{}</b>: <code>{}</code>", secrets.items())
    messages.warning(request, format_html(
        "{} Copy these now; the keys will not be shown again.{}", title, lines))


# ---------------------------------------------------------------- tenants
class ApiKeyInline(TabularInline):
    model = ApiKey
    extra = 0
    can_delete = False
    fields = ["kind", "key_prefix", "created_at", "revoked_at"]
    readonly_fields = ["kind", "key_prefix", "created_at"]
    tab = True
    verbose_name_plural = "API keys"


class AppInline(TabularInline):
    model = App
    extra = 0
    fields = ["platform", "bundle_id", "credential", "android_channel_id"]
    tab = True


@admin.register(Tenant)
class TenantAdmin(HelpButtonMixin, ModelAdmin):
    help_topic = "products"
    actions_list = ["how_it_works"]
    list_display = ["name", "status", "apps_count", "devices_count", "created_at"]
    search_fields = ["name"]
    fields = ["name", "is_active"]
    inlines = [AppInline, ApiKeyInline]
    actions = ["rotate_keys_revoke_old", "show_identity_secret"]
    actions_detail = ["rotate_keys_detail", "identity_secret_detail"]
    list_fullwidth = True

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            _apps=Count("apps", distinct=True),
            _devices=Count("devices", filter=Q(devices__is_active=True), distinct=True),
        )

    @display(description="Status", label={"Active": "success", "Off": "danger"})
    def status(self, obj):
        return "Active" if obj.is_active else "Off"

    @display(description="Apps", ordering="_apps")
    def apps_count(self, obj):
        return obj._apps

    @display(description="Active devices", ordering="_devices")
    def devices_count(self, obj):
        return obj._devices

    def save_model(self, request, obj, form, change):
        if change:
            return super().save_model(request, obj, form, change)
        identity_secret = crypto.new_identity_secret()
        obj.identity_secret_enc = crypto.encrypt(identity_secret)
        super().save_model(request, obj, form, change)
        keys = services._issue_keys(obj)
        _show_once(request, f"Product {obj.name} created.", {
            "secret_key (servers only)": keys[ApiKey.SECRET],
            "publishable_key (apps)": keys[ApiKey.PUBLISHABLE],
            "identity_secret (servers only)": identity_secret,
        })

    @admin.action(description="Rotate API keys (revoke the old ones)")
    def rotate_keys_revoke_old(self, request, queryset):
        for tenant in queryset:
            _show_once(request, f"New keys for {tenant.name}.", services.rotate_keys(tenant, revoke_existing=True))

    @admin.action(description="Show identity secret")
    def show_identity_secret(self, request, queryset):
        for tenant in queryset:
            messages.info(request, format_html("{}: <code>{}</code>", tenant.name, tenant.identity_secret))

    @action(description="Rotate keys", icon="autorenew", url_path="rotate-keys")
    def rotate_keys_detail(self, request, object_id):
        tenant = Tenant.objects.get(pk=object_id)
        _show_once(request, f"New keys for {tenant.name}.", services.rotate_keys(tenant, revoke_existing=True))
        return redirect(reverse("admin:tenants_app_tenant_change", args=[object_id]))

    @action(description="Show identity secret", icon="visibility", url_path="identity-secret")
    def identity_secret_detail(self, request, object_id):
        tenant = Tenant.objects.get(pk=object_id)
        messages.info(request, format_html("Identity secret: <code>{}</code>", tenant.identity_secret))
        return redirect(reverse("admin:tenants_app_tenant_change", args=[object_id]))


# ---------------------------------------------------------------- credentials
class CredentialForm(forms.ModelForm):
    fcm_service_account_json = forms.CharField(
        label="Service account JSON", required=False,
        widget=UnfoldAdminTextareaWidget(attrs={"rows": 8}),
        help_text="FCM only: paste the whole Firebase service account JSON. Leave blank to keep the current one.",
    )
    apns_team_id = forms.CharField(label="Team ID", required=False, widget=UnfoldAdminTextInputWidget,
                                   help_text="APNs only: Apple Team ID (10 characters)")
    apns_key_id = forms.CharField(label="Key ID", required=False, widget=UnfoldAdminTextInputWidget,
                                  help_text="APNs only: Key ID of the .p8 key")
    apns_p8 = forms.CharField(
        label=".p8 key", required=False, widget=UnfoldAdminTextareaWidget(attrs={"rows": 6}),
        help_text="APNs only: the full contents of AuthKey_XXXX.p8. Leave blank to keep the current one.",
    )

    class Meta:
        model = Credential
        fields = ["name", "provider"]

    def clean(self):
        data = super().clean()
        provider = data.get("provider")
        creating = not self.instance.pk
        if provider == Credential.FCM:
            raw = data.get("fcm_service_account_json", "").strip()
            if raw:
                try:
                    self._secret = json.loads(raw)
                except json.JSONDecodeError:
                    raise forms.ValidationError("The service account JSON is not valid JSON.")
            elif creating or self.instance.provider != provider:
                raise forms.ValidationError("Paste the service account JSON.")
        elif provider == Credential.APNS:
            fields = [data.get("apns_team_id"), data.get("apns_key_id"), data.get("apns_p8")]
            if any(fields):
                self._secret = {"team_id": fields[0] or "", "key_id": fields[1] or "", "p8": fields[2] or ""}
            elif creating or self.instance.provider != provider:
                raise forms.ValidationError("Fill Team ID, Key ID and the .p8 contents.")
        secret = getattr(self, "_secret", None)
        if secret is not None:
            try:
                self._secret = services.validate_secret(provider, secret)
            except services.OnboardingError as exc:
                raise forms.ValidationError(str(exc))
        return data

    def save(self, commit=True):
        obj = super().save(commit=False)
        if getattr(self, "_secret", None) is not None:
            obj.set_secret(self._secret)
        if commit:
            obj.save()
        return obj


@admin.register(Credential)
class CredentialAdmin(HelpButtonMixin, ModelAdmin):
    help_topic = "credentials"
    actions_list = ["how_it_works"]
    form = CredentialForm
    list_display = ["name", "provider_badge", "summary", "apps_using", "updated_at"]
    fieldsets = [
        (None, {"fields": ["name", "provider"]}),
        ("Firebase (Android)", {"fields": ["fcm_service_account_json"]}),
        ("Apple (iOS)", {"fields": ["apns_team_id", "apns_key_id", "apns_p8"]}),
    ]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_apps=Count("apps"))

    @display(description="Provider", label={"FCM": "info", "APNs": "warning"})
    def provider_badge(self, obj):
        return "FCM" if obj.provider == Credential.FCM else "APNs"

    @display(description="Details")
    def summary(self, obj):
        try:
            return ", ".join(f"{k}={v}" for k, v in obj.public_summary().items())
        except Exception:  # undecryptable after a wrong master key; show, don't crash the list
            return "(cannot decrypt: check MASTER_ENCRYPTION_KEY)"

    @display(description="Apps", ordering="_apps")
    def apps_using(self, obj):
        return obj._apps


# ---------------------------------------------------------------- apps
class AppForm(forms.ModelForm):
    class Meta:
        model = App
        fields = ["tenant", "platform", "bundle_id", "credential", "android_channel_id"]

    def clean(self):
        data = super().clean()
        if data.get("platform") == App.ANDROID and data.get("credential") and \
                data["credential"].provider != Credential.FCM:
            raise forms.ValidationError("Android apps deliver through FCM; pick an fcm credential.")
        return data


@admin.register(App)
class AppAdmin(HelpButtonMixin, ModelAdmin):
    help_topic = "apps"
    actions_list = ["how_it_works"]
    form = AppForm
    list_display = ["bundle_id", "platform_badge", "tenant", "credential", "devices_count", "created_at"]
    list_filter = ["platform", "tenant"]
    search_fields = ["bundle_id"]
    list_select_related = ["tenant", "credential"]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            _devices=Count("devices", filter=Q(devices__is_active=True)))

    @display(description="Platform", label={"Android": "success", "iOS": "info"})
    def platform_badge(self, obj):
        return obj.get_platform_display()

    @display(description="Active devices", ordering="_devices")
    def devices_count(self, obj):
        return obj._devices
