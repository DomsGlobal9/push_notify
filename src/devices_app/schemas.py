from rest_framework import serializers

from src.core.validators import validate_installation_id, validate_topics, validate_user_id

from .models import Device


class DeviceRegisterSerializer(serializers.Serializer):
    installation_id = serializers.CharField(min_length=8, max_length=128)
    platform = serializers.ChoiceField(choices=["android", "ios"])
    bundle_id = serializers.CharField(max_length=255)
    token = serializers.CharField(min_length=10, max_length=4096)
    apns_environment = serializers.ChoiceField(
        choices=[Device.PRODUCTION, Device.SANDBOX], default=Device.PRODUCTION)
    user_external_id = serializers.CharField(max_length=255, required=False, allow_null=True, allow_blank=True)
    user_token = serializers.CharField(max_length=128, required=False, allow_null=True, allow_blank=True)
    locale = serializers.CharField(max_length=35, required=False, allow_blank=True)
    timezone = serializers.CharField(max_length=64, required=False, allow_blank=True)
    app_version = serializers.CharField(max_length=50, required=False, allow_blank=True)
    topics = serializers.ListField(child=serializers.CharField(max_length=100), required=False,
                                   allow_null=True, max_length=200)

    def validate_topics(self, value):
        return None if value is None else validate_topics(value)

    def validate_installation_id(self, value):
        return validate_installation_id(value)

    def validate_user_external_id(self, value):
        return validate_user_id(value)


class TopicsUpdateSerializer(serializers.Serializer):
    bundle_id = serializers.CharField(max_length=255)
    add = serializers.ListField(child=serializers.CharField(max_length=100), default=list, max_length=200)
    remove = serializers.ListField(child=serializers.CharField(max_length=100), default=list, max_length=200)

    def validate_add(self, value):
        return validate_topics(value)

    def validate_remove(self, value):
        return validate_topics(value)


class DeviceSerializer(serializers.ModelSerializer):
    platform = serializers.CharField(source="app.platform")
    bundle_id = serializers.CharField(source="app.bundle_id")

    class Meta:
        model = Device
        fields = ["installation_id", "platform", "bundle_id", "user_external_id", "is_active",
                  "deactivated_reason", "app_version", "locale", "timezone", "last_seen_at", "created_at"]
