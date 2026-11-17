"""A send request and how it went.

Successes are counters on the Notification; only failures get a row. At a
million pushes a day, one row per success would be ~300 MB/day and the largest
cost in the whole system, while telling you nothing the counter does not.

`pending_batches` and `expand_done` drive completion: the expander raises
pending_batches as it enqueues each batch and sets expand_done after the last
page of devices; each batch lowers pending_batches when it settles. The
notification is complete when both say so.
"""

import uuid

from django.db import models

from src.tenants_app.models import Tenant


class Notification(models.Model):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    STATUSES = [(QUEUED, "Queued"), (PROCESSING, "Processing"), (COMPLETED, "Completed"), (CANCELLED, "Cancelled")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="notifications")
    idempotency_key = models.CharField(max_length=255, null=True, blank=True)
    target = models.JSONField()
    message = models.JSONField()
    send_at = models.DateTimeField()
    status = models.CharField(max_length=12, choices=STATUSES, default=QUEUED)
    expand_done = models.BooleanField(default=False)
    pending_batches = models.IntegerField(default=0)
    targeted_count = models.IntegerField(default=0)
    sent_count = models.IntegerField(default=0)
    failed_count = models.IntegerField(default=0)
    invalid_token_count = models.IntegerField(default=0)
    # Targeted, but unregistered/deactivated before its batch ran. When a notification
    # completes: targeted = sent + failed + invalid_token + skipped.
    skipped_count = models.IntegerField(default=0)
    opened_count = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "idempotency_key"], name="uniq_idempotency_key"),
        ]
        indexes = [models.Index(fields=["tenant", "-created_at"], name="notification_tenant_idx")]

    def __str__(self):
        title = (self.message or {}).get("title") or "(data only)"
        return f"{title} [{self.status}]"


class DeliveryFailure(models.Model):
    id = models.BigAutoField(primary_key=True)
    notification = models.ForeignKey(Notification, on_delete=models.CASCADE, related_name="failures")
    device = models.ForeignKey("devices_app.Device", on_delete=models.SET_NULL, null=True, blank=True)
    provider = models.CharField(max_length=8)
    status_code = models.IntegerField(null=True, blank=True)
    reason = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return f"{self.provider} {self.status_code} {self.reason}"
