"""Device lifecycle: register, refresh, log out, unregister, topics, user deletion.

Rules that matter:

  * Registration is an upsert on (app, installation_id). Calling it on every app
    start is expected and cheap; it also refreshes last_seen_at.
  * A push token belongs to one install. If it shows up on a new installation_id
    (reinstall that kept the token, restored backup), the old row is retired so
    the user does not get every notification twice.
  * Linking a user to a device with a pk_ key needs a user_token signed by the
    tenant backend. With an sk_ key the backend is trusted directly.
  * Logout unlinks the user but keeps the device, so broadcasts still arrive.
"""

from django.db import transaction
from django.utils import timezone

from src.core import crypto
from src.core.exceptions import NotFound
from src.tenants_app.models import App, Tenant

from .models import Device, DeviceTopic


class IdentityError(PermissionError):
    pass


def find_app(tenant: Tenant, bundle_id: str, platform: str | None = None) -> App:
    qs = App.objects.select_related("credential").filter(tenant=tenant, bundle_id=bundle_id)
    if platform:
        qs = qs.filter(platform=platform)
    apps = list(qs[:2])
    if not apps:
        raise NotFound(f"App {bundle_id} is not registered for this tenant.")
    if len(apps) > 1:
        raise NotFound(f"{bundle_id} exists on both platforms; pass platform.")
    return apps[0]


def find_device(tenant: Tenant, bundle_id: str, installation_id: str) -> Device:
    app = find_app(tenant, bundle_id)
    device = Device.objects.filter(app=app, installation_id=installation_id).first()
    if device is None:
        raise NotFound("Device not found.")
    return device


def check_identity(tenant: Tenant, trusted: bool, user_external_id: str | None, user_token: str | None):
    if not user_external_id or trusted:
        return
    if not crypto.verify_user_token(tenant.identity_secret, user_external_id, user_token or ""):
        raise IdentityError("user_token is missing or does not match user_external_id.")


@transaction.atomic
def register(tenant: Tenant, *, trusted: bool, data: dict) -> Device:
    check_identity(tenant, trusted, data.get("user_external_id"), data.get("user_token"))
    app = find_app(tenant, data["bundle_id"], data["platform"])

    Device.objects.filter(app=app, token=data["token"], is_active=True).exclude(
        installation_id=data["installation_id"]
    ).update(is_active=False, deactivated_reason="token_moved")

    device, _ = Device.objects.select_for_update().get_or_create(
        app=app, installation_id=data["installation_id"],
        defaults={"tenant": tenant, "token": data["token"]},
    )
    device.token = data["token"]
    device.apns_environment = data.get("apns_environment") or Device.PRODUCTION
    device.user_external_id = data.get("user_external_id") or None
    for field in ("locale", "timezone", "app_version"):
        if data.get(field):
            setattr(device, field, data[field])
    device.is_active = True
    device.deactivated_reason = ""
    device.save()

    if data.get("topics") is not None:
        set_topics(device, data["topics"])
    return device


def set_topics(device: Device, topics: list[str]) -> None:
    DeviceTopic.objects.filter(device=device).exclude(topic__in=topics).delete()
    DeviceTopic.objects.bulk_create(
        [DeviceTopic(device=device, topic=t) for t in topics], ignore_conflicts=True
    )


@transaction.atomic
def update_topics(device: Device, add: list[str], remove: list[str]) -> list[str]:
    DeviceTopic.objects.bulk_create([DeviceTopic(device=device, topic=t) for t in add], ignore_conflicts=True)
    if remove:
        DeviceTopic.objects.filter(device=device, topic__in=remove).delete()
    return list(device.topics.order_by("topic").values_list("topic", flat=True))


def logout(device: Device) -> None:
    device.user_external_id = None
    device.save(update_fields=["user_external_id", "last_seen_at"])


def unregister(device: Device) -> None:
    device.delete()


def deactivate(device_ids: list[int], reason: str) -> int:
    """Called by the dispatcher when a provider says a token is dead."""
    if not device_ids:
        return 0
    return Device.objects.filter(id__in=device_ids, is_active=True).update(
        is_active=False, deactivated_reason=reason[:100], last_seen_at=timezone.now()
    )


def delete_user(tenant: Tenant, user_external_id: str) -> int:
    deleted, _ = Device.objects.filter(tenant=tenant, user_external_id=user_external_id).delete()
    return deleted
