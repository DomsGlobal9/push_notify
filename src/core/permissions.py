"""Who may call what. One class per caller kind; views pick one, never combine ad hoc."""

from rest_framework.permissions import BasePermission


def _principal(request):
    return request.user if getattr(request.user, "is_authenticated", False) else None


class TenantKey(BasePermission):
    """Any tenant key, pk_ or sk_. Device-side endpoints."""

    message = "Send a tenant key in the X-API-Key header."

    def has_permission(self, request, view):
        p = _principal(request)
        return bool(p and not p.is_operator)


class SecretKey(BasePermission):
    """sk_ keys only. Sending, user-level reads and deletes."""

    message = "This endpoint needs a secret (sk_) key. Never ship that key inside an app."

    def has_permission(self, request, view):
        p = _principal(request)
        return bool(p and not p.is_operator and p.is_secret)


class Operator(BasePermission):
    """The ADMIN_API_TOKEN bearer. Onboarding tenants, credentials and apps."""

    message = "Admin token required."

    def has_permission(self, request, view):
        p = _principal(request)
        return bool(p and p.is_operator)
