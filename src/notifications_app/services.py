"""Creating notifications, resolving their audience, and settling them.

The audience is resolved lazily, a page at a time, by the dispatcher (see
`target_page`). Nothing here loads "all devices" into memory, so a broadcast to
a million installs costs the same RAM as one to ten.
"""

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from src.devices_app.models import Device
from src.dispatch_app import queue
from src.tenants_app.models import Tenant

from .models import DeliveryFailure, Notification


class DuplicateInFlight(Exception):
    pass


def create(tenant: Tenant, target: dict, message: dict, send_at=None,
           idempotency_key: str | None = None) -> tuple[Notification, bool]:
    """Returns (notification, created). A repeated Idempotency-Key returns the original."""
    if idempotency_key:
        existing = Notification.objects.filter(tenant=tenant, idempotency_key=idempotency_key).first()
        if existing:
            return existing, False
    try:
        with transaction.atomic():
            n = Notification.objects.create(
                tenant=tenant, idempotency_key=idempotency_key or None, target=target, message=message,
                send_at=send_at or timezone.now(),
            )
            queue.enqueue(queue.EXPAND, {"notification_id": str(n.id), "after": 0}, run_at=n.send_at)
    except IntegrityError:
        existing = Notification.objects.filter(tenant=tenant, idempotency_key=idempotency_key).first()
        if existing is None:
            raise DuplicateInFlight()
        return existing, False
    return n, True


@transaction.atomic
def cancel(notification: Notification) -> bool:
    updated = Notification.objects.filter(
        id=notification.id, status__in=[Notification.QUEUED, Notification.PROCESSING]
    ).update(status=Notification.CANCELLED, completed_at=timezone.now())
    if updated:
        queue.drop_pending_for(str(notification.id))
    return bool(updated)


def record_open(tenant: Tenant, notification_id) -> None:
    Notification.objects.filter(id=notification_id, tenant=tenant).update(opened_count=F("opened_count") + 1)


def target_page(notification: Notification, after: int, limit: int) -> list[int]:
    """The next `limit` active device ids for this notification, after id `after` (keyset paging)."""
    t = notification.target
    qs = Device.objects.filter(tenant_id=notification.tenant_id, is_active=True, id__gt=after)
    if t.get("user_ids"):
        qs = qs.filter(user_external_id__in=t["user_ids"])
    elif t.get("installation_ids"):
        qs = qs.filter(installation_id__in=t["installation_ids"])
    elif t.get("topics"):
        qs = qs.filter(topics__topic__in=t["topics"]).distinct()
    if t.get("platforms"):
        qs = qs.filter(app__platform__in=t["platforms"])
    if t.get("bundle_ids"):
        qs = qs.filter(app__bundle_id__in=t["bundle_ids"])
    return list(qs.order_by("id").values_list("id", flat=True)[:limit])


def settle_if_done(notification_id) -> None:
    Notification.objects.filter(
        id=notification_id, expand_done=True, pending_batches__lte=0, status=Notification.PROCESSING,
    ).update(status=Notification.COMPLETED, completed_at=timezone.now())


def record_failures(notification_id, rows: list[tuple]) -> None:
    """rows: (device_id, provider, status_code, reason)."""
    DeliveryFailure.objects.bulk_create([
        DeliveryFailure(notification_id=notification_id, device_id=d, provider=p, status_code=c,
                        reason=(r or "")[:500])
        for d, p, c, r in rows
    ])
