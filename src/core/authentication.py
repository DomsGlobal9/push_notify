"""Who is calling.

Two kinds of caller, two headers:

  X-API-Key: sk_...   a tenant's backend server (any of your products).
                      May send notifications and manage any of its devices.
  X-API-Key: pk_...   a tenant's mobile app. Safe to ship inside the app: it can
                      only register/unregister its own device and report opens.
  Authorization: Bearer <ADMIN_API_TOKEN>
                      the operator, for /admin-api/ automation. The Django admin
                      at /admin/ uses normal staff sign-in instead.

Every tenant-scoped query in the codebase filters by `request.user.tenant`, so a
key can never reach another tenant's rows.
"""

import hmac
from dataclasses import dataclass

from django.conf import settings
from rest_framework import authentication, exceptions

from src.core.crypto import hash_key


@dataclass
class TenantPrincipal:
    tenant: object
    key_kind: str  # "secret" | "publishable"

    is_authenticated = True
    is_operator = False

    @property
    def is_secret(self) -> bool:
        return self.key_kind == "secret"


@dataclass
class OperatorPrincipal:
    is_authenticated = True
    is_operator = True


class ApiKeyAuthentication(authentication.BaseAuthentication):
    header = "HTTP_X_API_KEY"

    def authenticate(self, request):
        raw = request.META.get(self.header)
        if not raw:
            return None
        from src.tenants_app.models import ApiKey

        key = (
            ApiKey.objects.select_related("tenant")
            .filter(key_hash=hash_key(raw.strip()), revoked_at__isnull=True, tenant__is_active=True)
            .first()
        )
        if key is None:
            raise exceptions.AuthenticationFailed("Invalid or revoked API key.")
        return TenantPrincipal(tenant=key.tenant, key_kind=key.kind), key

    def authenticate_header(self, request):
        return "X-API-Key"


class AdminTokenAuthentication(authentication.BaseAuthentication):
    def authenticate(self, request):
        header = request.META.get("HTTP_AUTHORIZATION", "")
        if not header.startswith("Bearer "):
            return None
        expected = settings.ADMIN_API_TOKEN
        if not expected or not hmac.compare_digest(header[7:].strip(), expected):
            raise exceptions.AuthenticationFailed("Invalid admin token.")
        return OperatorPrincipal(), None
