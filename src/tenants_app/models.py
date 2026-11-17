"""Tenants and what they send with.

Tenancy is row-level (a `tenant` foreign key), not schema-per-tenant like CRM-B.
A tenant here is one of *your* products, so there are a handful of them, the
tables are narrow, and the dispatcher must scan devices across all of them in
one query. Separate schemas would buy isolation we already get from the API
key scoping, at the cost of a fan-out query per tenant.

    Tenant      one of your products (a shop app, a CRM, a delivery app, ...)
    ApiKey      sk_ (servers) and pk_ (apps) keys for a tenant, stored hashed
    Credential  an FCM service account or an APNs .p8 key, encrypted. Shared:
                one Firebase project and one Apple team serve many apps.
    App         an Android package or iOS bundle id inside a tenant, pointing
                at the credential that delivers to it
"""

import json
import uuid

from django.db import models

from src.core import crypto


class Tenant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    identity_secret_enc = models.TextField(editable=False)
    is_active = models.BooleanField("active", default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "product"
        verbose_name_plural = "products"

    def __str__(self):
        return self.name

    @property
    def identity_secret(self) -> str:
        return crypto.decrypt(self.identity_secret_enc)


class ApiKey(models.Model):
    SECRET = "secret"
    PUBLISHABLE = "publishable"
    KINDS = [(SECRET, "Secret (sk_)"), (PUBLISHABLE, "Publishable (pk_)")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="api_keys")
    kind = models.CharField(max_length=12, choices=KINDS)
    key_hash = models.CharField(max_length=64, unique=True, editable=False)
    key_prefix = models.CharField(max_length=12, editable=False)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.key_prefix}… ({self.kind})"


class Credential(models.Model):
    FCM = "fcm"
    APNS = "apns"
    PROVIDERS = [(FCM, "Firebase Cloud Messaging"), (APNS, "Apple Push Notification service")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    provider = models.CharField(max_length=8, choices=PROVIDERS)
    secret_enc = models.TextField(editable=False)
    # Bumped on every secret change so running dispatchers drop their cached sender.
    revision = models.PositiveIntegerField(default=1, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.provider})"

    def set_secret(self, secret: dict) -> None:
        if self.secret_enc:
            self.revision += 1
        self.secret_enc = crypto.encrypt(json.dumps(secret))

    def get_secret(self) -> dict:
        return json.loads(crypto.decrypt(self.secret_enc))

    def public_summary(self) -> dict:
        """Non-secret facts for the admin screen, read from the decrypted blob."""
        s = self.get_secret()
        if self.provider == self.FCM:
            return {"project_id": s.get("project_id"), "client_email": s.get("client_email")}
        return {"team_id": s.get("team_id"), "key_id": s.get("key_id")}


class App(models.Model):
    ANDROID = "android"
    IOS = "ios"
    PLATFORMS = [(ANDROID, "Android"), (IOS, "iOS")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="apps", verbose_name="product")
    platform = models.CharField(max_length=8, choices=PLATFORMS)
    bundle_id = models.CharField(max_length=255, help_text="Android package name or iOS bundle id")
    credential = models.ForeignKey(Credential, on_delete=models.PROTECT, related_name="apps")
    android_channel_id = models.CharField(
        max_length=100, blank=True, default="",
        help_text="Default Android notification channel for this app",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["tenant__name", "bundle_id"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "platform", "bundle_id"], name="uniq_app_per_tenant"),
        ]

    def __str__(self):
        return f"{self.bundle_id} ({self.platform})"

    @property
    def provider(self) -> str:
        return self.credential.provider
