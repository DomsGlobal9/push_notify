"""Onboarding and keys: who can get in, and that secrets never leave encrypted."""

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from src.core import crypto
from src.core.testing import World, apns_secret, fcm_secret
from src.tenants_app import services
from src.tenants_app.models import ApiKey, App, Credential, Tenant

ADMIN = {"HTTP_AUTHORIZATION": "Bearer test-admin-token"}


class CryptoTests(TestCase):
    def test_round_trip_and_version_prefix(self):
        sealed = crypto.encrypt("hello")
        self.assertTrue(sealed.startswith("v1:"))
        self.assertNotIn("hello", sealed)
        self.assertEqual(crypto.decrypt(sealed), "hello")

    def test_user_token_is_bound_to_the_user(self):
        t = crypto.user_token("secret", "user-1")
        self.assertTrue(crypto.verify_user_token("secret", "user-1", t))
        self.assertFalse(crypto.verify_user_token("secret", "user-2", t))
        self.assertFalse(crypto.verify_user_token("other", "user-1", t))


@override_settings(ADMIN_API_TOKEN="test-admin-token")
class AdminApiTests(TestCase):
    def setUp(self):
        self.client = APIClient(**ADMIN)

    def test_admin_api_needs_the_token(self):
        self.assertEqual(APIClient().get("/admin-api/tenants").status_code, 401)
        bad = APIClient(HTTP_AUTHORIZATION="Bearer nope")
        self.assertEqual(bad.get("/admin-api/tenants").status_code, 401)

    def test_tenant_keys_cannot_use_admin_api(self):
        w = World()
        self.assertEqual(w.server.get("/admin-api/tenants").status_code, 403)

    @override_settings(ADMIN_API_TOKEN="")
    def test_empty_admin_token_disables_admin_api(self):
        self.assertEqual(APIClient(HTTP_AUTHORIZATION="Bearer ").get("/admin-api/tenants").status_code, 401)

    def test_full_onboarding(self):
        r = self.client.post("/admin-api/tenants", {"name": "Example CRM"}, format="json")
        self.assertEqual(r.status_code, 201)
        tenant_id = r.data["id"]
        self.assertTrue(r.data["secret_key"].startswith("sk_"))
        self.assertTrue(r.data["publishable_key"].startswith("pk_"))
        # stored hashed only
        self.assertFalse(ApiKey.objects.filter(key_hash=r.data["secret_key"]).exists())
        self.assertTrue(ApiKey.objects.filter(key_hash=crypto.hash_key(r.data["secret_key"])).exists())

        r = self.client.post("/admin-api/credentials", {"name": "fb", "provider": "fcm",
                                                        "fcm_service_account": fcm_secret()}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["summary"]["project_id"], "demo-project")
        self.assertNotIn("private_key", str(r.data))
        cred_id = r.data["id"]

        r = self.client.post("/admin-api/apps", {"tenant_id": tenant_id, "platform": "android",
                                                 "bundle_id": "com.crm.b", "credential_id": cred_id}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(self.client.get("/admin-api/tenants").data[0]["name"], "Example CRM")

    def test_duplicate_tenant_rejected(self):
        self.client.post("/admin-api/tenants", {"name": "X"}, format="json")
        r = self.client.post("/admin-api/tenants", {"name": "X"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data["code"], "invalid")

    def test_rotate_keys_revokes_old(self):
        w = World()
        r = self.client.post(f"/admin-api/tenants/{w.tenant.id}/rotate-keys", {"revoke_existing": True},
                             format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(w.server.get("/v1/notifications").status_code, 401)
        self.assertEqual(APIClient(HTTP_X_API_KEY=r.data["secret_key"]).get("/v1/notifications").status_code, 200)

    def test_identity_secret_readable_by_operator(self):
        w = World()
        r = self.client.get(f"/admin-api/tenants/{w.tenant.id}/identity-secret")
        self.assertEqual(r.data["identity_secret"], w.secrets["identity_secret"])


class OnboardingRuleTests(TestCase):
    def test_android_app_needs_fcm(self):
        tenant, _ = services.create_tenant("T")
        apns = services.save_credential("apple", Credential.APNS, apns_secret())
        with self.assertRaises(services.OnboardingError):
            services.create_app(tenant, App.ANDROID, "com.x", apns)

    def test_credential_validation(self):
        with self.assertRaises(services.OnboardingError):
            services.save_credential("bad", Credential.FCM, {"project_id": "x"})
        with self.assertRaises(services.OnboardingError):
            services.save_credential("bad", Credential.APNS, {"team_id": "a", "key_id": "b", "p8": "nope"})

    def test_secret_is_encrypted_at_rest_and_revision_bumps(self):
        cred = services.save_credential("fb", Credential.FCM, fcm_secret())
        self.assertNotIn("BEGIN PRIVATE KEY", cred.secret_enc)
        self.assertEqual(cred.revision, 1)
        services.save_credential("fb", Credential.FCM, fcm_secret("other"), cred)
        cred.refresh_from_db()
        self.assertEqual(cred.revision, 2)
        self.assertEqual(cred.get_secret()["project_id"], "other")

    def test_inactive_tenant_keys_rejected(self):
        w = World()
        Tenant.objects.filter(id=w.tenant.id).update(is_active=False)
        self.assertEqual(w.server.get("/v1/notifications").status_code, 401)
