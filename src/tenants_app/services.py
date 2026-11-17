"""Onboarding: the one place tenants, keys, credentials and apps are created.

The admin API, the Django admin and the management commands all call these, so
the rules (keys hashed, secrets encrypted, Android needs FCM) live once.
"""

from django.db import transaction
from django.utils import timezone

from src.core import crypto

from .models import ApiKey, App, Credential, Tenant

FCM_REQUIRED = {"project_id", "client_email", "private_key"}
APNS_REQUIRED = {"team_id", "key_id", "p8"}


class OnboardingError(ValueError):
    pass


def _issue_keys(tenant: Tenant) -> dict:
    issued = {}
    for kind in (ApiKey.SECRET, ApiKey.PUBLISHABLE):
        raw = crypto.new_api_key(kind)
        ApiKey.objects.create(tenant=tenant, kind=kind, key_hash=crypto.hash_key(raw), key_prefix=raw[:10])
        issued[kind] = raw
    return issued


@transaction.atomic
def create_tenant(name: str) -> tuple[Tenant, dict]:
    """Returns the tenant and its plaintext secrets. They are never retrievable again,
    except the identity secret, which the operator can re-read (it must be shared
    with the tenant's backend)."""
    identity_secret = crypto.new_identity_secret()
    tenant = Tenant.objects.create(name=name, identity_secret_enc=crypto.encrypt(identity_secret))
    keys = _issue_keys(tenant)
    return tenant, {
        "secret_key": keys[ApiKey.SECRET],
        "publishable_key": keys[ApiKey.PUBLISHABLE],
        "identity_secret": identity_secret,
    }


@transaction.atomic
def rotate_keys(tenant: Tenant, revoke_existing: bool) -> dict:
    if revoke_existing:
        tenant.api_keys.filter(revoked_at__isnull=True).update(revoked_at=timezone.now())
    keys = _issue_keys(tenant)
    return {"secret_key": keys[ApiKey.SECRET], "publishable_key": keys[ApiKey.PUBLISHABLE]}


def validate_secret(provider: str, secret: dict) -> dict:
    required = FCM_REQUIRED if provider == Credential.FCM else APNS_REQUIRED
    missing = sorted(required - {k for k, v in secret.items() if v})
    if missing:
        raise OnboardingError(f"{provider} credential is missing {missing}")
    if provider == Credential.FCM:
        if secret.get("type", "service_account") != "service_account":
            raise OnboardingError("FCM needs a Firebase *service account* JSON (type=service_account)")
        return secret
    p8 = secret["p8"].strip()
    if "BEGIN PRIVATE KEY" not in p8:
        raise OnboardingError("APNs p8 must be the full contents of AuthKey_XXXX.p8, including the BEGIN line")
    return {"team_id": secret["team_id"].strip(), "key_id": secret["key_id"].strip(), "p8": p8}


def save_credential(name: str, provider: str, secret: dict, credential: Credential | None = None) -> Credential:
    secret = validate_secret(provider, secret)
    credential = credential or Credential(name=name, provider=provider)
    credential.name = name
    credential.provider = provider
    credential.set_secret(secret)
    credential.save()
    return credential


def create_app(tenant: Tenant, platform: str, bundle_id: str, credential: Credential,
               android_channel_id: str = "") -> App:
    if platform == App.ANDROID and credential.provider != Credential.FCM:
        raise OnboardingError("Android apps deliver through FCM; pick an fcm credential")
    if App.objects.filter(tenant=tenant, platform=platform, bundle_id=bundle_id).exists():
        raise OnboardingError(f"{bundle_id} ({platform}) is already registered for {tenant.name}")
    return App.objects.create(tenant=tenant, platform=platform, bundle_id=bundle_id,
                              credential=credential, android_channel_id=android_channel_id or "")
