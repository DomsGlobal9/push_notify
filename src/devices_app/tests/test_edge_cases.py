"""Device edge cases: odd input, user switching, cross-app and cross-tenant token reuse, races."""

import threading

from django.db import connection
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from src.core.testing import World
from src.devices_app.models import Device, DeviceTopic


class InputEdgeCases(TestCase):
    def setUp(self):
        self.w = World()

    def post(self, **overrides):
        body = {"installation_id": "install-edge-1", "platform": "android", "bundle_id": "com.example.shop",
                "token": "tok-edge-123456"}
        body.update(overrides)
        return self.w.app_client.post("/v1/devices", {k: v for k, v in body.items() if v is not ...},
                                      format="json")

    def test_missing_fields(self):
        for field in ("installation_id", "platform", "bundle_id", "token"):
            r = self.post(**{field: ...})
            self.assertEqual(r.status_code, 400, field)
            self.assertIn(field, r.data["details"])

    def test_bad_installation_ids(self):
        for bad in ("short", "has/slash-123", "has space 123", "x" * 129, "emoji-🙂-1234"):
            self.assertEqual(self.post(installation_id=bad).status_code, 400, bad)
        self.assertEqual(self.post(installation_id="Ok.id_with:chars-1").status_code, 200)

    def test_bad_user_ids_rejected_even_with_valid_hmac(self):
        for bad in ("a/b", "has space", "q?x", "frag#1", "pct%20"):
            r = self.w.register("install-badusr1", user=bad)
            self.assertEqual(r.status_code, 400, bad)
        self.assertEqual(self.w.register("install-okusr01", user="boutique:staff-12").status_code, 200)
        self.assertEqual(self.w.register("install-okusr02", user="ユーザー42").status_code, 200)

    def test_wrong_platform_for_bundle(self):
        self.assertEqual(self.post(platform="ios").status_code, 404)  # com.example.shop is android only

    def test_invalid_platform_and_env(self):
        self.assertEqual(self.post(platform="windows").status_code, 400)
        self.assertEqual(self.post(apns_environment="staging").status_code, 400)

    def test_token_whitespace_is_trimmed(self):
        self.assertEqual(self.post(token="  tok-with-spaces-1  ").status_code, 200)
        self.assertEqual(Device.objects.get().token, "tok-with-spaces-1")

    def test_too_short_or_huge_token(self):
        self.assertEqual(self.post(token="abc").status_code, 400)
        self.assertEqual(self.post(token="x" * 5000).status_code, 400)

    def test_blank_user_is_anonymous(self):
        self.assertEqual(self.post(user_external_id="", user_token="").status_code, 200)
        self.assertIsNone(Device.objects.get().user_external_id)

    def test_non_json_body(self):
        r = self.w.app_client.post("/v1/devices", "not json", content_type="application/json")
        self.assertEqual(r.status_code, 400)
        r = self.w.app_client.post("/v1/devices", "a=b", content_type="application/x-www-form-urlencoded")
        self.assertEqual(r.status_code, 415)

    def test_array_body(self):
        self.assertEqual(self.w.app_client.post("/v1/devices", [1, 2], format="json").status_code, 400)

    def test_topics_null_keeps_empty_clears(self):
        self.post(topics=["a", "b"])
        self.post(topics=None)
        self.assertEqual(DeviceTopic.objects.count(), 2)
        self.post()  # omitted: keep
        self.assertEqual(DeviceTopic.objects.count(), 2)
        self.post(topics=[])
        self.assertEqual(DeviceTopic.objects.count(), 0)

    def test_duplicate_topics_collapse(self):
        self.post(topics=["a", "a", "b"])
        self.assertEqual(DeviceTopic.objects.count(), 2)

    def test_too_many_topics(self):
        self.assertEqual(self.post(topics=[f"t{i}" for i in range(201)]).status_code, 400)

    def test_optional_fields_are_kept_when_omitted(self):
        self.post(locale="te-IN", timezone="Asia/Kolkata", app_version="1.2.0")
        self.post()  # a later refresh without them
        d = Device.objects.get()
        self.assertEqual((d.locale, d.timezone, d.app_version), ("te-IN", "Asia/Kolkata", "1.2.0"))


class LifecycleEdgeCases(TestCase):
    def setUp(self):
        self.w = World()

    def test_user_switch_on_one_phone(self):
        self.w.register("install-shared1", user="alice")
        self.w.app_client.post("/v1/devices/install-shared1/logout?bundle_id=com.example.shop")
        self.w.register("install-shared1", user="bob")
        d = Device.objects.get()
        self.assertEqual(d.user_external_id, "bob")
        self.assertEqual(self.w.server.get("/v1/users/alice/devices").data, [])

    def test_login_as_other_user_without_logout_relinks(self):
        self.w.register("install-shared2", user="alice")
        self.w.register("install-shared2", user="bob")
        self.assertEqual(Device.objects.get().user_external_id, "bob")

    def test_reregistering_without_user_unlinks(self):
        """The SDK re-registers with the stored user; registering without one means signed out."""
        self.w.register("install-shared3", user="alice")
        self.w.register("install-shared3")
        self.assertIsNone(Device.objects.get().user_external_id)

    def test_deactivated_device_comes_back_on_register(self):
        self.w.register("install-back001")
        Device.objects.update(is_active=False, deactivated_reason="provider_rejected_token")
        self.w.register("install-back001", token="fresh-token-0001")
        d = Device.objects.get()
        self.assertTrue(d.is_active)
        self.assertEqual(d.deactivated_reason, "")

    def test_token_moved_back_and_forth(self):
        self.w.register("install-a000001", token="same-token-00001")
        self.w.register("install-b000001", token="same-token-00001")
        self.w.register("install-a000001", token="same-token-00001")
        active = list(Device.objects.filter(is_active=True).values_list("installation_id", flat=True))
        self.assertEqual(active, ["install-a000001"])

    def test_same_installation_id_in_two_apps_are_separate(self):
        self.w.register("install-both001", platform="android")
        self.w.register("install-both001", platform="ios")
        self.assertEqual(Device.objects.filter(installation_id="install-both001").count(), 2)
        self.w.app_client.delete("/v1/devices/install-both001?bundle_id=com.example.shop")
        self.assertEqual(Device.objects.get().app.platform, "ios")

    def test_same_token_in_other_tenant_is_not_touched(self):
        other = World("Other Product")
        self.w.register("install-mine001", token="collide-token-001")
        other.register("install-theirs1", token="collide-token-001")
        self.assertEqual(Device.objects.filter(is_active=True).count(), 2)

    def test_logout_unknown_device_404(self):
        r = self.w.app_client.post("/v1/devices/install-nope0001/logout?bundle_id=com.example.shop")
        self.assertEqual(r.status_code, 404)

    def test_topics_for_unknown_device_404(self):
        r = self.w.app_client.post("/v1/devices/install-nope0001/topics",
                                   {"bundle_id": "com.example.shop", "add": ["x"]}, format="json")
        self.assertEqual(r.status_code, 404)

    def test_delete_user_with_no_devices(self):
        self.assertEqual(self.w.server.delete("/v1/users/ghost").data["deleted_devices"], 0)

    def test_user_devices_lists_inactive_with_reason(self):
        self.w.register("install-gone001", user="u")
        Device.objects.update(is_active=False, deactivated_reason="provider_rejected_token")
        row = self.w.server.get("/v1/users/u/devices").data[0]
        self.assertEqual((row["is_active"], row["deactivated_reason"]), (False, "provider_rejected_token"))


class AuthEdgeCases(TestCase):
    def setUp(self):
        self.w = World()

    def test_key_with_surrounding_whitespace_works(self):
        c = APIClient(HTTP_X_API_KEY=f"  {self.w.secrets['secret_key']} ")
        self.assertEqual(c.get("/v1/notifications").status_code, 200)

    def test_empty_and_garbage_keys(self):
        for key in ("", "sk_", "pk_nothing", "Bearer x", "' OR 1=1 --"):
            r = APIClient(HTTP_X_API_KEY=key).get("/v1/notifications")
            self.assertIn(r.status_code, (401, 403), key)

    def test_admin_token_cannot_use_tenant_endpoints(self):
        from django.test import override_settings
        with override_settings(ADMIN_API_TOKEN="adm"):
            c = APIClient(HTTP_AUTHORIZATION="Bearer adm")
            self.assertEqual(c.get("/v1/notifications").status_code, 403)
            self.assertEqual(c.post("/v1/devices", {}, format="json").status_code, 403)

    def test_revoked_publishable_key(self):
        from django.utils import timezone

        from src.core.crypto import hash_key
        from src.tenants_app.models import ApiKey
        ApiKey.objects.filter(key_hash=hash_key(self.w.secrets["publishable_key"])).update(revoked_at=timezone.now())
        self.assertEqual(self.w.register("install-rev0001").status_code, 401)
        self.assertEqual(self.w.server.get("/v1/notifications").status_code, 200)  # sk still fine

    def test_user_token_from_other_tenant_rejected(self):
        other = World("Other Product")
        from src.core.crypto import user_token
        forged = user_token(other.secrets["identity_secret"], "alice")
        r = self.w.app_client.post("/v1/devices", {
            "installation_id": "install-forge01", "platform": "android", "bundle_id": "com.example.shop",
            "token": "tok-forge-123456", "user_external_id": "alice", "user_token": forged}, format="json")
        self.assertEqual(r.status_code, 403)


class RegistrationRaceTests(TransactionTestCase):
    """The SDK can fire register twice at once (app start + token refresh)."""

    def test_concurrent_registration_of_one_install(self):
        w = World()
        results = []

        def go(i):
            try:
                r = w.register("install-race001", token=f"race-token-{i:04}")
                results.append(r.status_code)
            finally:
                connection.close()

        threads = [threading.Thread(target=go, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(results.count(200), 8, results)
        self.assertEqual(Device.objects.filter(installation_id="install-race001").count(), 1)
