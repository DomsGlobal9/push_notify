"""Shared test fixtures: tenants, credentials, apps and keys without boilerplate."""

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from rest_framework.test import APIClient

from src.tenants_app import services
from src.tenants_app.models import App, Credential


def rsa_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode()


def ec_p8() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode()


_RSA = None
_EC = None


def fcm_secret(project="demo-project"):
    global _RSA
    _RSA = _RSA or rsa_pem()
    return {"type": "service_account", "project_id": project, "client_email": "push@demo.iam.gserviceaccount.com",
            "private_key": _RSA, "private_key_id": "kid1", "token_uri": "https://oauth2.googleapis.com/token"}


def apns_secret():
    global _EC
    _EC = _EC or ec_p8()
    return {"team_id": "TEAM123456", "key_id": "KEY1234567", "p8": _EC}


class World:
    """One tenant with an Android app (FCM) and an iOS app (APNs), plus API clients."""

    def __init__(self, name="Example Shop"):
        self.tenant, self.secrets = services.create_tenant(name)
        self.fcm = Credential.objects.filter(name="firebase").first() or \
            services.save_credential("firebase", Credential.FCM, fcm_secret())
        self.apns = Credential.objects.filter(name="apple").first() or \
            services.save_credential("apple", Credential.APNS, apns_secret())
        self.android = services.create_app(self.tenant, App.ANDROID, "com.example.shop", self.fcm)
        self.ios = services.create_app(self.tenant, App.IOS, "com.example.shop.ios", self.apns)
        self.server = APIClient(HTTP_X_API_KEY=self.secrets["secret_key"])
        self.app_client = APIClient(HTTP_X_API_KEY=self.secrets["publishable_key"])

    def register(self, installation_id, platform="android", user=None, token=None, client=None, **extra):
        from src.core.crypto import user_token
        body = {
            "installation_id": installation_id, "platform": platform,
            "bundle_id": "com.example.shop" if platform == "android" else "com.example.shop.ios",
            "token": token or f"tok-{installation_id}-xxxxxxxx", **extra,
        }
        if user:
            body["user_external_id"] = user
            body["user_token"] = user_token(self.secrets["identity_secret"], user)
        return (client or self.app_client).post("/v1/devices", body, format="json")
