"""Per-tenant rate limits, so one product's burst cannot starve the others.

Keyed by tenant id, not by API key, so issuing more keys does not raise the cap.
The cache is per-process (LocMemCache), which makes the limit per web instance;
switch CACHES to Redis when running more than one web instance and you need a
global cap.
"""

from rest_framework.throttling import SimpleRateThrottle


class _TenantThrottle(SimpleRateThrottle):
    def get_cache_key(self, request, view):
        tenant = getattr(request.user, "tenant", None)
        if tenant is None:
            return None
        return self.cache_format % {"scope": self.scope, "ident": tenant.pk}


class TenantSendThrottle(_TenantThrottle):
    scope = "tenant_send"


class TenantDeviceThrottle(_TenantThrottle):
    scope = "tenant_device"
