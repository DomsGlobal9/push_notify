"""Operator API under /admin-api/ for scripted onboarding.

Everything here is also possible from the Django admin (/admin/) and from the
management commands; this exists so onboarding can be automated from CI or a
script. Guarded by ADMIN_API_TOKEN and nothing else.
"""

from django.db.models import Count, Q
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from src.core.exceptions import Conflict
from src.core.permissions import Operator

from . import services
from .models import App, Credential, Tenant
from .schemas import (
    AppCreateSerializer, AppSerializer, CredentialCreateSerializer, CredentialSerializer,
    TenantCreateSerializer, TenantSerializer,
)


class TenantViewSet(viewsets.GenericViewSet):
    permission_classes = [Operator]
    serializer_class = TenantSerializer

    def get_queryset(self):
        return Tenant.objects.annotate(
            active_devices=Count("devices", filter=Q(devices__is_active=True))
        )

    def list(self, request):
        return Response(TenantSerializer(self.get_queryset(), many=True).data)

    def retrieve(self, request, pk=None):
        return Response(TenantSerializer(self.get_object()).data)

    def create(self, request):
        body = TenantCreateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        tenant, secrets = services.create_tenant(body.validated_data["name"])
        return Response(
            {"id": str(tenant.id), "name": tenant.name, **secrets,
             "note": "Store these now. The keys are shown only once."},
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"], url_path="rotate-keys")
    def rotate_keys(self, request, pk=None):
        revoke = str(request.data.get("revoke_existing", "false")).lower() in ("1", "true", "yes")
        return Response(services.rotate_keys(self.get_object(), revoke), status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"], url_path="identity-secret")
    def identity_secret(self, request, pk=None):
        return Response({"identity_secret": self.get_object().identity_secret})


class CredentialViewSet(viewsets.GenericViewSet):
    permission_classes = [Operator]
    queryset = Credential.objects.all()
    serializer_class = CredentialSerializer

    def list(self, request):
        return Response(CredentialSerializer(self.get_queryset(), many=True).data)

    def create(self, request):
        body = CredentialCreateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        try:
            cred = services.save_credential(data["name"], data["provider"], data["secret"])
        except services.OnboardingError as exc:
            return Response({"error": str(exc), "code": "invalid_credential"}, status=422)
        return Response(CredentialSerializer(cred).data, status=status.HTTP_201_CREATED)


class AppViewSet(viewsets.GenericViewSet):
    permission_classes = [Operator]
    serializer_class = AppSerializer

    def get_queryset(self):
        qs = App.objects.select_related("credential", "tenant")
        tenant_id = self.request.query_params.get("tenant_id")
        return qs.filter(tenant_id=tenant_id) if tenant_id else qs

    def list(self, request):
        return Response(AppSerializer(self.get_queryset(), many=True).data)

    def create(self, request):
        body = AppCreateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        d = body.validated_data
        try:
            app = services.create_app(d["tenant"], d["platform"], d["bundle_id"], d["credential"],
                                      d.get("android_channel_id", ""))
        except services.OnboardingError as exc:
            raise Conflict(str(exc))
        return Response(AppSerializer(app).data, status=status.HTTP_201_CREATED)
