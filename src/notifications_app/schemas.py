import json

from rest_framework import serializers

from src.core.validators import validate_topics

MAX_CONTENT_BYTES = 3500

from .models import DeliveryFailure, Notification


class TargetSerializer(serializers.Serializer):
    user_ids = serializers.ListField(child=serializers.CharField(max_length=255), required=False, max_length=10000)
    installation_ids = serializers.ListField(child=serializers.CharField(max_length=128), required=False,
                                             max_length=10000)
    topics = serializers.ListField(child=serializers.CharField(max_length=100), required=False, max_length=50)
    all = serializers.BooleanField(default=False)
    platforms = serializers.ListField(child=serializers.ChoiceField(choices=["android", "ios"]), required=False)
    bundle_ids = serializers.ListField(child=serializers.CharField(max_length=255), required=False)

    def validate_topics(self, value):
        return validate_topics(value)

    def validate(self, attrs):
        chosen = [bool(attrs.get("user_ids")), bool(attrs.get("installation_ids")),
                  bool(attrs.get("topics")), attrs.get("all", False)]
        if sum(chosen) != 1:
            raise serializers.ValidationError("Set exactly one of user_ids, installation_ids, topics or all.")
        return {k: v for k, v in attrs.items() if v not in (None, [], False)}


class MessageSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=200, required=False, allow_blank=True)
    body = serializers.CharField(max_length=2000, required=False, allow_blank=True)
    data = serializers.DictField(child=serializers.CharField(allow_blank=True, max_length=1000), required=False)
    image = serializers.URLField(required=False)
    sound = serializers.CharField(max_length=100, required=False, default="default")
    badge = serializers.IntegerField(min_value=0, required=False)
    android_channel_id = serializers.CharField(max_length=100, required=False)
    ttl_seconds = serializers.IntegerField(min_value=0, max_value=2419200, default=86400)
    collapse_key = serializers.CharField(max_length=64, required=False)
    priority = serializers.ChoiceField(choices=["high", "normal"], default="high")
    content_available = serializers.BooleanField(default=False)

    def validate_data(self, value):
        reserved = [k for k in value if k.startswith(("_ngw", "google.", "gcm.")) or k in ("from", "aps",
                                                                                           "message_type")]
        if reserved:
            raise serializers.ValidationError(f"Reserved data keys: {reserved}")
        return value

    def validate_collapse_key(self, value):
        if len(value.encode()) > 64:
            raise serializers.ValidationError("collapse_key must be at most 64 bytes (APNs limit).")
        return value

    def validate(self, attrs):
        if not (attrs.get("title") or attrs.get("body") or attrs.get("data") or attrs.get("content_available")):
            raise serializers.ValidationError("A message needs a title, body, data or content_available.")
        # FCM and APNs both cap a push at 4096 bytes. Reject here, once, instead of
        # failing on every device at send time. 3,500 leaves room for the envelope.
        size = len(json.dumps({k: attrs.get(k) for k in ("title", "body", "data", "image")},
                              ensure_ascii=False).encode())
        if size > MAX_CONTENT_BYTES:
            raise serializers.ValidationError(
                f"Title, body, data and image together are {size} bytes; the limit is {MAX_CONTENT_BYTES}. "
                "Send an id in data and let the app fetch the rest.")
        return attrs


class NotificationCreateSerializer(serializers.Serializer):
    target = TargetSerializer()
    message = MessageSerializer()
    send_at = serializers.DateTimeField(required=False)


class DeliveryFailureSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeliveryFailure
        fields = ["provider", "status_code", "reason", "created_at"]


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ["id", "status", "target", "message", "send_at", "created_at", "completed_at",
                  "targeted_count", "sent_count", "failed_count", "invalid_token_count", "skipped_count",
                  "opened_count"]


class EventSerializer(serializers.Serializer):
    notification_id = serializers.UUIDField()
    installation_id = serializers.CharField(max_length=128, required=False)
    type = serializers.ChoiceField(choices=["opened"], default="opened")
