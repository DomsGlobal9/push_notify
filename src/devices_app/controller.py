"""Device endpoints.

  POST   /v1/devices                                  register / refresh (pk_ or sk_)
  POST   /v1/devices/<installation_id>/logout?bundle_id=…    unlink the user (pk_ or sk_)
  DELETE /v1/devices/<installation_id>?bundle_id=…           forget this install (pk_ or sk_)
  POST   /v1/devices/<installation_id>/topics         subscribe / unsubscribe (pk_ or sk_)
  GET    /v1/users/<user_id>/devices                  a user's devices (sk_)
  DELETE /v1/users/<user_id>                          delete a user's devices (sk_)

installation_id is a random id only the install knows, so holding it is the
proof of ownership for the pk_ endpoints.
"""

from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from src.core.permissions import SecretKey, TenantKey
from src.core.throttles import TenantDeviceThrottle

from . import services
from .models import Device
from .schemas import DeviceRegisterSerializer, DeviceSerializer, TopicsUpdateSerializer


def _bundle_id(request):
    bundle_id = request.query_params.get("bundle_id")
    if not bundle_id:
        raise ValidationError({"bundle_id": "Pass ?bundle_id=<package or bundle id>."})
    return bundle_id


class DeviceRegisterView(APIView):
    permission_classes = [TenantKey]
    throttle_classes = [TenantDeviceThrottle]

    def post(self, request):
        body = DeviceRegisterSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        try:
            device = services.register(request.user.tenant, trusted=request.user.is_secret,
                                       data=body.validated_data)
        except services.IdentityError as exc:
            raise PermissionDenied(str(exc))
        return Response({"ok": True, "installation_id": device.installation_id})


class DeviceDetailView(APIView):
    permission_classes = [TenantKey]
    throttle_classes = [TenantDeviceThrottle]

    def delete(self, request, installation_id):
        services.unregister(services.find_device(request.user.tenant, _bundle_id(request), installation_id))
        return Response(status=status.HTTP_204_NO_CONTENT)


class DeviceLogoutView(APIView):
    permission_classes = [TenantKey]
    throttle_classes = [TenantDeviceThrottle]

    def post(self, request, installation_id):
        services.logout(services.find_device(request.user.tenant, _bundle_id(request), installation_id))
        return Response({"ok": True})


class DeviceTopicsView(APIView):
    permission_classes = [TenantKey]
    throttle_classes = [TenantDeviceThrottle]

    def post(self, request, installation_id):
        body = TopicsUpdateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        d = body.validated_data
        device = services.find_device(request.user.tenant, d["bundle_id"], installation_id)
        return Response({"topics": services.update_topics(device, d["add"], d["remove"])})


class UserDevicesView(APIView):
    permission_classes = [SecretKey]

    def get(self, request, user_external_id):
        devices = (Device.objects.select_related("app")
                   .filter(tenant=request.user.tenant, user_external_id=user_external_id)
                   .order_by("-last_seen_at"))
        return Response(DeviceSerializer(devices, many=True).data)


class UserDetailView(APIView):
    permission_classes = [SecretKey]

    def delete(self, request, user_external_id):
        return Response({"deleted_devices": services.delete_user(request.user.tenant, user_external_id)})
