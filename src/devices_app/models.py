"""One row per app install.

`installation_id` is a random id the app generates once and keeps in local
storage. It, not the push token, is the identity: tokens rotate (FCM refreshes
them, iOS changes them on restore), the install does not. A reinstall makes a
new installation_id, and the old row dies on its first 404/410.

Deactivated rows are kept, not deleted, so the admin can see *why* a device
stopped receiving (unregistered, token moved, bad token).
"""

from django.db import models

from src.tenants_app.models import App, Tenant


class Device(models.Model):
    PRODUCTION = "production"
    SANDBOX = "sandbox"
    APNS_ENVIRONMENTS = [(PRODUCTION, "Production"), (SANDBOX, "Sandbox (debug builds)")]

    id = models.BigAutoField(primary_key=True)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="devices", verbose_name="product")
    app = models.ForeignKey(App, on_delete=models.CASCADE, related_name="devices")
    installation_id = models.CharField(max_length=128)
    token = models.TextField()
    apns_environment = models.CharField(max_length=10, choices=APNS_ENVIRONMENTS, default=PRODUCTION)
    user_external_id = models.CharField(max_length=255, null=True, blank=True)
    locale = models.CharField(max_length=35, blank=True, default="")
    timezone = models.CharField(max_length=64, blank=True, default="")
    app_version = models.CharField(max_length=50, blank=True, default="")
    is_active = models.BooleanField(default=True)
    deactivated_reason = models.CharField(max_length=100, blank=True, default="")
    last_seen_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["app", "installation_id"], name="uniq_install_per_app"),
        ]
        indexes = [
            models.Index(fields=["tenant", "user_external_id"], name="device_user_idx",
                         condition=models.Q(is_active=True)),
            models.Index(fields=["tenant", "id"], name="device_tenant_active_idx",
                         condition=models.Q(is_active=True)),
            models.Index(fields=["app", "token"], name="device_token_idx"),
        ]

    def __str__(self):
        return f"{self.installation_id} ({self.app})"


class DeviceTopic(models.Model):
    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="topics")
    topic = models.CharField(max_length=100)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["device", "topic"], name="uniq_device_topic")]
        indexes = [models.Index(fields=["topic", "device"], name="topic_device_idx")]

    def __str__(self):
        return self.topic
