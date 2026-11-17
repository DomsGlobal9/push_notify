"""Sending and tracking.

  POST /v1/notifications                 send now or at send_at (sk_). 202 + the record.
                                         Header Idempotency-Key makes retries safe.
  GET  /v1/notifications                 recent sends with counters (sk_)
  GET  /v1/notifications/<id>            one send, with its latest failures (sk_)
  POST /v1/notifications/<id>/cancel     stop a queued or running send (sk_)
  POST /v1/events                        the app reports an open (pk_ or sk_)
"""

from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from src.core.exceptions import Conflict, NotFound
from src.core.permissions import SecretKey, TenantKey
from src.core.throttles import TenantDeviceThrottle, TenantSendThrottle

from . import services
from .models import Notification
from .schemas import DeliveryFailureSerializer, EventSerializer, NotificationCreateSerializer, NotificationSerializer


def _get(request, notification_id) -> Notification:
    n = Notification.objects.filter(id=notification_id, tenant=request.user.tenant).first()
    if n is None:
        raise NotFound("Notification not found.")
    return n


class NotificationListView(APIView):
    permission_classes = [SecretKey]

    def get_throttles(self):
        return [TenantSendThrottle()] if self.request.method == "POST" else []

    def post(self, request):
        body = NotificationCreateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        d = body.validated_data
        key = (request.headers.get("Idempotency-Key") or "").strip() or None
        if key and len(key) > 255:
            raise ValidationError({"Idempotency-Key": "At most 255 characters."})
        try:
            n, created = services.create(request.user.tenant, d["target"], d["message"], d.get("send_at"), key)
        except services.DuplicateInFlight:
            raise Conflict("A request with this Idempotency-Key is in progress.")
        return Response(NotificationSerializer(n).data,
                        status=status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK)

    def get(self, request):
        try:
            limit = max(1, min(int(request.query_params.get("limit", 50)), 200))
        except ValueError:
            limit = 50
        qs = Notification.objects.filter(tenant=request.user.tenant)[:limit]
        return Response(NotificationSerializer(qs, many=True).data)


class NotificationDetailView(APIView):
    permission_classes = [SecretKey]

    def get(self, request, notification_id):
        n = _get(request, notification_id)
        data = NotificationSerializer(n).data
        data["recent_failures"] = DeliveryFailureSerializer(n.failures.all()[:20], many=True).data
        return Response(data)


class NotificationCancelView(APIView):
    permission_classes = [SecretKey]

    def post(self, request, notification_id):
        if not services.cancel(_get(request, notification_id)):
            raise Conflict("Notification already finished.")
        return Response({"ok": True, "status": Notification.CANCELLED})


class EventView(APIView):
    permission_classes = [TenantKey]
    throttle_classes = [TenantDeviceThrottle]

    def post(self, request):
        body = EventSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        services.record_open(request.user.tenant, body.validated_data["notification_id"])
        return Response({"ok": True}, status=status.HTTP_202_ACCEPTED)
