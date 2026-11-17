from rest_framework import serializers

from .models import App, Credential, Tenant


class TenantCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100)

    def validate_name(self, value):
        if Tenant.objects.filter(name=value).exists():
            raise serializers.ValidationError("A tenant with this name already exists.")
        return value


class TenantSerializer(serializers.ModelSerializer):
    active_devices = serializers.IntegerField(read_only=True)

    class Meta:
        model = Tenant
        fields = ["id", "name", "is_active", "created_at", "active_devices"]


class CredentialCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100)
    provider = serializers.ChoiceField(choices=[Credential.FCM, Credential.APNS])
    fcm_service_account = serializers.DictField(required=False)
    apns = serializers.DictField(child=serializers.CharField(), required=False)

    def validate_name(self, value):
        if Credential.objects.filter(name=value).exists():
            raise serializers.ValidationError("A credential with this name already exists.")
        return value

    def validate(self, attrs):
        secret = attrs.get("fcm_service_account") if attrs["provider"] == Credential.FCM else attrs.get("apns")
        if not secret:
            field = "fcm_service_account" if attrs["provider"] == Credential.FCM else "apns"
            raise serializers.ValidationError({field: "Required for this provider."})
        attrs["secret"] = secret
        return attrs


class CredentialSerializer(serializers.ModelSerializer):
    summary = serializers.SerializerMethodField()

    class Meta:
        model = Credential
        fields = ["id", "name", "provider", "summary", "created_at", "updated_at"]

    def get_summary(self, obj):
        return obj.public_summary()


class AppCreateSerializer(serializers.Serializer):
    tenant_id = serializers.PrimaryKeyRelatedField(queryset=Tenant.objects.all(), source="tenant")
    platform = serializers.ChoiceField(choices=[App.ANDROID, App.IOS])
    bundle_id = serializers.CharField(max_length=255)
    credential_id = serializers.PrimaryKeyRelatedField(queryset=Credential.objects.all(), source="credential")
    android_channel_id = serializers.CharField(max_length=100, required=False, allow_blank=True)


class AppSerializer(serializers.ModelSerializer):
    provider = serializers.CharField(read_only=True)

    class Meta:
        model = App
        fields = ["id", "tenant", "platform", "bundle_id", "credential", "provider",
                  "android_channel_id", "created_at"]
