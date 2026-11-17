"""Device registration: identity binding, token moves, tenant isolation."""

from django.test import TestCase

from src.core.testing import World
from src.devices_app.models import Device


class RegisterTests(TestCase):
    def setUp(self):
        self.w = World()

    def test_anonymous_register_and_refresh_is_an_upsert(self):
        self.assertEqual(self.w.register("install-0001").status_code, 200)
        self.assertEqual(self.w.register("install-0001", token="new-token-123456").status_code, 200)
        devices = Device.objects.filter(installation_id="install-0001")
        self.assertEqual(devices.count(), 1)
        self.assertEqual(devices.first().token, "new-token-123456")

    def test_pk_key_cannot_claim_a_user_without_a_valid_user_token(self):
        body = {"installation_id": "install-0002", "platform": "android", "bundle_id": "com.example.shop",
                "token": "tok-aaaaaaaaaaaa", "user_external_id": "victim"}
        r = self.w.app_client.post("/v1/devices", body, format="json")
        self.assertEqual(r.status_code, 403)
        r = self.w.app_client.post("/v1/devices", {**body, "user_token": "0" * 64}, format="json")
        self.assertEqual(r.status_code, 403)
        self.assertFalse(Device.objects.exists())

    def test_valid_user_token_links_the_user(self):
        self.assertEqual(self.w.register("install-0003", user="cust-42").status_code, 200)
        self.assertEqual(Device.objects.get().user_external_id, "cust-42")

    def test_secret_key_is_trusted_to_link_users(self):
        body = {"installation_id": "install-0004", "platform": "ios", "bundle_id": "com.example.shop.ios",
                "token": "apns-token-abcdef", "user_external_id": "staff-1", "apns_environment": "sandbox"}
        self.assertEqual(self.w.server.post("/v1/devices", body, format="json").status_code, 200)
        d = Device.objects.get()
        self.assertEqual((d.user_external_id, d.apns_environment), ("staff-1", "sandbox"))

    def test_token_moving_to_a_new_install_retires_the_old_row(self):
        self.w.register("install-old1", token="shared-token-1234")
        self.w.register("install-new1", token="shared-token-1234")
        old = Device.objects.get(installation_id="install-old1")
        self.assertFalse(old.is_active)
        self.assertEqual(old.deactivated_reason, "token_moved")
        self.assertTrue(Device.objects.get(installation_id="install-new1").is_active)

    def test_unknown_app_is_404(self):
        r = self.w.app_client.post("/v1/devices", {"installation_id": "install-0005", "platform": "android",
                                                   "bundle_id": "com.unknown", "token": "tok-123456789"},
                                   format="json")
        self.assertEqual(r.status_code, 404)

    def test_missing_key_is_401_with_error_shape(self):
        from rest_framework.test import APIClient
        r = APIClient().post("/v1/devices", {}, format="json")
        self.assertEqual(r.status_code, 401)
        self.assertIn("code", r.data)


class LifecycleTests(TestCase):
    def setUp(self):
        self.w = World()
        self.w.register("install-1111", user="cust-1")

    def test_logout_keeps_device_but_drops_user(self):
        r = self.w.app_client.post("/v1/devices/install-1111/logout?bundle_id=com.example.shop")
        self.assertEqual(r.status_code, 200)
        d = Device.objects.get()
        self.assertIsNone(d.user_external_id)
        self.assertTrue(d.is_active)

    def test_unregister_deletes(self):
        r = self.w.app_client.delete("/v1/devices/install-1111?bundle_id=com.example.shop")
        self.assertEqual(r.status_code, 204)
        self.assertFalse(Device.objects.exists())

    def test_bundle_id_required(self):
        self.assertEqual(self.w.app_client.delete("/v1/devices/install-1111").status_code, 400)

    def test_topics(self):
        r = self.w.app_client.post("/v1/devices/install-1111/topics",
                                   {"bundle_id": "com.example.shop", "add": ["offers", "hyd"]}, format="json")
        self.assertEqual(r.data["topics"], ["hyd", "offers"])
        r = self.w.app_client.post("/v1/devices/install-1111/topics",
                                   {"bundle_id": "com.example.shop", "remove": ["hyd"]}, format="json")
        self.assertEqual(r.data["topics"], ["offers"])
        r = self.w.app_client.post("/v1/devices/install-1111/topics",
                                   {"bundle_id": "com.example.shop", "add": ["bad topic!"]}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_user_endpoints_need_secret_key(self):
        self.assertEqual(self.w.app_client.get("/v1/users/cust-1/devices").status_code, 403)
        r = self.w.server.get("/v1/users/cust-1/devices")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data[0]["installation_id"], "install-1111")
        self.assertNotIn("token", r.data[0])

    def test_delete_user(self):
        self.assertEqual(self.w.server.delete("/v1/users/cust-1").data["deleted_devices"], 1)


class TenantIsolationTests(TestCase):
    def test_other_tenant_cannot_see_or_touch_devices(self):
        a = World("A")
        b = World("B")  # reuses the credentials, registers its own apps
        a.register("install-aaaa", user="u1")
        self.assertEqual(b.server.get("/v1/users/u1/devices").data, [])
        self.assertEqual(b.app_client.delete("/v1/devices/install-aaaa?bundle_id=com.example.shop").status_code, 404)
        self.assertEqual(b.server.delete("/v1/users/u1").data["deleted_devices"], 0)
        self.assertEqual(Device.objects.count(), 1)
