"""The operator dashboard at /admin/.

Secrets are write-only here: a credential's form takes the service account JSON
or the .p8 text and encrypts it on save; the list shows only non-secret facts
(project id, team id). New tenant keys appear once, in the success message.
"""

import json

from django import forms
from django.contrib import admin, messages
from django.utils.html import format_html, format_html_join

from src.core import crypto

from . import services
from .models import ApiKey, App, Credential, Tenant


def _show_once(request, title, secrets: dict):
    lines = format_html_join("", "<br><b>{}</b>: <code>{}</code>", secrets.items())
    messages.warning(request, format_html(
        "{} Copy these now; the keys will not be shown again.{}", title, lines))


class ApiKeyInline(admin.TabularInline):
    model = ApiKey
    extra = 0
    can_delete = False
    fields = ["kind", "key_prefix", "created_at", "revoked_at"]
    readonly_fields = ["kind", "key_prefix", "created_at"]


class AppInline(admin.TabularInline):
    model = App
    extra = 0
    fields = ["platform", "bundle_id", "credential", "android_channel_id"]


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ["name", "is_active", "created_at"]
    search_fields = ["name"]
    fields = ["name", "is_active"]
    inlines = [AppInline, ApiKeyInline]
    actions = ["rotate_keys_revoke_old", "show_identity_secret"]

    def save_model(self, request, obj, form, change):
        if change:
            return super().save_model(request, obj, form, change)
        identity_secret = crypto.new_identity_secret()
        obj.identity_secret_enc = crypto.encrypt(identity_secret)
        super().save_model(request, obj, form, change)
        keys = services._issue_keys(obj)
        _show_once(request, f"Tenant {obj.name} created.", {
            "secret_key (servers only)": keys[ApiKey.SECRET],
            "publishable_key (apps)": keys[ApiKey.PUBLISHABLE],
            "identity_secret (servers only)": identity_secret,
        })

    @admin.action(description="Rotate API keys (revoke the old ones)")
    def rotate_keys_revoke_old(self, request, queryset):
        for tenant in queryset:
            keys = services.rotate_keys(tenant, revoke_existing=True)
            _show_once(request, f"New keys for {tenant.name}.", keys)

    @admin.action(description="Show identity secret")
    def show_identity_secret(self, request, queryset):
        for tenant in queryset:
            messages.info(request, format_html("{}: <code>{}</code>", tenant.name, tenant.identity_secret))


class CredentialForm(forms.ModelForm):
    fcm_service_account_json = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 8}),
        help_text="FCM only: paste the Firebase service account JSON. Leave blank to keep the current one.",
    )
    apns_team_id = forms.CharField(required=False, help_text="APNs only: Apple Team ID")
    apns_key_id = forms.CharField(required=False, help_text="APNs only: Key ID of the .p8 key")
    apns_p8 = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 6}),
        help_text="APNs only: contents of AuthKey_XXXX.p8. Leave blank to keep the current one.",
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
class CredentialAdmin(admin.ModelAdmin):
    form = CredentialForm
    list_display = ["name", "provider", "summary", "updated_at"]

    @admin.display(description="Details")
    def summary(self, obj):
        try:
            return ", ".join(f"{k}={v}" for k, v in obj.public_summary().items())
        except Exception:  # undecryptable after a wrong master key; show, don't crash the list
            return "(cannot decrypt: check MASTER_ENCRYPTION_KEY)"


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
class AppAdmin(admin.ModelAdmin):
    form = AppForm
    list_display = ["bundle_id", "platform", "tenant", "credential", "created_at"]
    list_filter = ["platform", "tenant"]
    search_fields = ["bundle_id"]
