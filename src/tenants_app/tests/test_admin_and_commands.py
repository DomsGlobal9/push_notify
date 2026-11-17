"""The dashboard, the onboarding commands and crypto failure modes."""

import base64
import json
import os
import tempfile
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from src.core import crypto
from src.core.testing import World, apns_secret, fcm_secret
from src.devices_app.models import Device
from src.dispatch_app.models import Job
from src.notifications_app.models import Notification
from src.tenants_app.models import ApiKey, App, Credential, Tenant


class CryptoFailureTests(TestCase):
    def test_tampered_ciphertext_is_rejected(self):
        sealed = crypto.encrypt("secret")
        raw = bytearray(base64.b64decode(sealed[3:]))
        raw[-1] ^= 1
        with self.assertRaises(Exception):
            crypto.decrypt("v1:" + base64.b64encode(bytes(raw)).decode())

    def test_wrong_master_key_cannot_decrypt(self):
        sealed = crypto.encrypt("secret")
        with override_settings(MASTER_ENCRYPTION_KEY=base64.b64encode(os.urandom(32)).decode()):
            with self.assertRaises(Exception):
                crypto.decrypt(sealed)

    def test_bad_master_key_length(self):
        with override_settings(MASTER_ENCRYPTION_KEY=base64.b64encode(b"short").decode()):
            with self.assertRaises(ValueError):
                crypto.encrypt("x")

    def test_unknown_version(self):
        with self.assertRaises(ValueError):
            crypto.decrypt("v9:abcd")

    def test_keys_are_unique_and_prefixed(self):
        keys = {crypto.new_api_key("secret") for _ in range(200)}
        self.assertEqual(len(keys), 200)
        self.assertTrue(all(k.startswith("sk_") and len(k) > 40 for k in keys))


class CommandTests(TestCase):
    def run_cmd(self, *args):
        out = StringIO()
        call_command(*args, stdout=out)
        return out.getvalue()

    def files(self):
        d = tempfile.mkdtemp()
        fcm_path = os.path.join(d, "sa.json")
        p8_path = os.path.join(d, "AuthKey.p8")
        with open(fcm_path, "w") as f:
            json.dump(fcm_secret(), f)
        with open(p8_path, "w") as f:
            f.write(apns_secret()["p8"])
        return fcm_path, p8_path

    def test_full_onboarding_by_commands(self):
        fcm_path, p8_path = self.files()
        self.assertIn("Saved", self.run_cmd("add_credential", "fb", "--fcm-json", fcm_path))
        self.assertIn("Saved", self.run_cmd("add_credential", "apple", "--apns-p8", p8_path,
                                            "--team-id", "TEAM", "--key-id", "KEY"))
        out = self.run_cmd("create_tenant", "Delivery App")
        self.assertIn("sk_", out)
        self.assertIn("pk_", out)
        self.run_cmd("add_app", "Delivery App", "android", "com.example.delivery", "fb", "--channel", "orders")
        self.run_cmd("add_app", "Delivery App", "ios", "com.example.delivery", "apple")
        self.assertEqual(App.objects.count(), 2)

    def test_command_errors(self):
        fcm_path, p8_path = self.files()
        self.run_cmd("create_tenant", "T")
        with self.assertRaises(CommandError):
            self.run_cmd("create_tenant", "T")
        with self.assertRaises(CommandError):
            self.run_cmd("add_credential", "x")  # neither file
        with self.assertRaises(CommandError):
            self.run_cmd("add_credential", "x", "--apns-p8", p8_path)  # no team/key id
        self.run_cmd("add_credential", "fb", "--fcm-json", fcm_path)
        with self.assertRaises(CommandError):
            self.run_cmd("add_credential", "fb", "--fcm-json", fcm_path)  # exists, no --replace
        self.run_cmd("add_credential", "fb", "--fcm-json", fcm_path, "--replace")
        self.assertEqual(Credential.objects.get(name="fb").revision, 2)
        with self.assertRaises(CommandError):
            self.run_cmd("add_app", "Nope", "android", "com.x", "fb")
        with self.assertRaises(CommandError):
            self.run_cmd("add_app", "T", "android", "com.x", "nope")
        self.run_cmd("add_credential", "apple", "--apns-p8", p8_path, "--team-id", "T1", "--key-id", "K1")
        with self.assertRaises(CommandError):
            self.run_cmd("add_app", "T", "android", "com.x", "apple")  # android needs fcm

    def test_ensure_admin_user_creates_once_and_never_clobbers(self):
        from unittest import mock
        with mock.patch.dict(os.environ, {"ADMIN_USERNAME": "ops", "ADMIN_PASSWORD": "pw-one-1234"}):
            self.assertIn("Created", self.run_cmd("ensure_admin_user"))
        with mock.patch.dict(os.environ, {"ADMIN_USERNAME": "ops", "ADMIN_PASSWORD": "pw-two-5678"}):
            self.assertIn("unchanged", self.run_cmd("ensure_admin_user"))
        self.assertEqual(User.objects.count(), 1)
        self.assertTrue(User.objects.get().check_password("pw-one-1234"))  # a deploy never resets it
        with mock.patch.dict(os.environ, {"ADMIN_USERNAME": "ops", "ADMIN_PASSWORD": "pw-two-5678",
                                          "ADMIN_RESET_PASSWORD": "true"}):
            self.assertIn("Reset", self.run_cmd("ensure_admin_user"))
        self.assertTrue(User.objects.get().check_password("pw-two-5678"))
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ADMIN_USERNAME", None)
            self.assertIn("skipping", self.run_cmd("ensure_admin_user"))

    def test_run_dispatcher_once_and_purge(self):
        w = World()
        w.register("install-cmd001")
        from src.dispatch_app.providers import SendResult, registry

        class S:
            provider = "fcm"

            def send(self, *a):
                return SendResult("sent", 200)

            def close(self):
                pass
        registry.reset()
        registry.factory = lambda c: S()
        try:
            from src.notifications_app import services as ns
            n = ns.create(w.tenant, {"all": True}, {"title": "t"})[0]
            Job.objects.update(run_at="2000-01-01T00:00:00Z")
            self.assertIn("Processed 2 jobs", self.run_cmd("run_dispatcher", "--once"))
        finally:
            registry.factory = None
            registry.reset()
        n.refresh_from_db()
        self.assertEqual(n.sent_count, 1)
        self.assertIn("Deleted", self.run_cmd("purge_old_data"))


class AdminDashboardTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("ops", "ops@example.com", "pw")
        self.client.force_login(self.admin)

    def test_every_list_page_loads(self):
        w = World()
        w.register("install-adm001", user="u1", topics=["a"])
        from src.notifications_app import services as ns
        ns.create(w.tenant, {"all": True}, {"title": "t"})
        for url in ["/admin/", "/admin/tenants_app/tenant/", "/admin/tenants_app/app/",
                    "/admin/tenants_app/credential/", "/admin/devices_app/device/",
                    "/admin/notifications_app/notification/", "/admin/dispatch_app/job/",
                    "/admin/notifications_app/notification/send-test/",
                    f"/admin/tenants_app/tenant/{w.tenant.id}/change/",
                    f"/admin/devices_app/device/{Device.objects.get().id}/change/",
                    f"/admin/notifications_app/notification/{Notification.objects.get().id}/change/",
                    f"/admin/tenants_app/credential/{Credential.objects.first().id}/change/",
                    "/admin/tenants_app/credential/add/", "/admin/tenants_app/tenant/add/",
                    "/admin/auth/user/", f"/admin/auth/user/{self.admin.id}/change/"]:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_dashboard_shows_the_numbers(self):
        w = World()
        w.register("install-dash01")
        from src.notifications_app import services as ns
        ns.create(w.tenant, {"all": True}, {"title": "Dashboard check"})
        html = self.client.get("/admin/").content.decode()
        for text in ("Overview", "Active devices", "Last 7 days", "Recent notifications", "Dashboard check",
                     "Example Shop", "gateway/theme.css"):
            self.assertIn(text, html)

    def test_product_page_buttons_rotate_keys_and_show_secret(self):
        w = World()
        r = self.client.get(f"/admin/tenants_app/tenant/{w.tenant.id}/rotate-keys/", follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn("sk_", r.content.decode())
        self.assertEqual(w.server.get("/v1/notifications").status_code, 401)  # old keys revoked
        r = self.client.get(f"/admin/tenants_app/tenant/{w.tenant.id}/identity-secret/", follow=True)
        self.assertIn(w.secrets["identity_secret"], r.content.decode())

    def test_create_tenant_in_admin_issues_keys_once(self):
        r = self.client.post("/admin/tenants_app/tenant/add/", {
            "name": "Admin Made", "is_active": "on",
            "apps-TOTAL_FORMS": 0, "apps-INITIAL_FORMS": 0,
            "api_keys-TOTAL_FORMS": 0, "api_keys-INITIAL_FORMS": 0}, follow=True)
        self.assertEqual(r.status_code, 200)
        t = Tenant.objects.get(name="Admin Made")
        self.assertEqual(ApiKey.objects.filter(tenant=t).count(), 2)
        self.assertIn("sk_", r.content.decode())
        self.assertTrue(t.identity_secret)

    def test_credential_form_fcm_and_apns(self):
        r = self.client.post("/admin/tenants_app/credential/add/", {
            "name": "fb-admin", "provider": "fcm", "fcm_service_account_json": json.dumps(fcm_secret())})
        self.assertEqual(r.status_code, 302, r.content.decode()[:2000])
        a = apns_secret()
        r = self.client.post("/admin/tenants_app/credential/add/", {
            "name": "apple-admin", "provider": "apns", "apns_team_id": a["team_id"],
            "apns_key_id": a["key_id"], "apns_p8": a["p8"]})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Credential.objects.count(), 2)
        self.assertNotIn("PRIVATE", self.client.get("/admin/tenants_app/credential/").content.decode())

    def test_credential_form_rejects_garbage_and_keeps_secret_on_rename(self):
        r = self.client.post("/admin/tenants_app/credential/add/", {
            "name": "bad", "provider": "fcm", "fcm_service_account_json": "{not json"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Credential.objects.exists())
        self.client.post("/admin/tenants_app/credential/add/", {
            "name": "fb", "provider": "fcm", "fcm_service_account_json": json.dumps(fcm_secret())})
        cred = Credential.objects.get()
        before = cred.secret_enc
        r = self.client.post(f"/admin/tenants_app/credential/{cred.id}/change/",
                             {"name": "fb-renamed", "provider": "fcm", "fcm_service_account_json": ""})
        self.assertEqual(r.status_code, 302)
        cred.refresh_from_db()
        self.assertEqual((cred.name, cred.secret_enc, cred.revision), ("fb-renamed", before, 1))

    def test_switching_provider_requires_new_secret(self):
        self.client.post("/admin/tenants_app/credential/add/", {
            "name": "fb", "provider": "fcm", "fcm_service_account_json": json.dumps(fcm_secret())})
        cred = Credential.objects.get()
        r = self.client.post(f"/admin/tenants_app/credential/{cred.id}/change/", {"name": "fb", "provider": "apns"})
        self.assertEqual(r.status_code, 200)  # form error, not saved
        cred.refresh_from_db()
        self.assertEqual(cred.provider, "fcm")

    def test_app_form_blocks_android_on_apns(self):
        w = World()
        apple = Credential.objects.get(name="apple")
        r = self.client.post("/admin/tenants_app/app/add/", {
            "tenant": w.tenant.id, "platform": "android", "bundle_id": "com.example.other",
            "credential": apple.id, "android_channel_id": ""})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(App.objects.filter(bundle_id="com.example.other").exists())

    def test_send_test_form_queues(self):
        w = World()
        r = self.client.post("/admin/notifications_app/notification/send-test/",
                             {"tenant": w.tenant.id, "user_id": "", "title": "Hi", "body": "There",
                              "route": "/orders"})
        self.assertEqual(r.status_code, 302)
        n = Notification.objects.get()
        self.assertEqual((n.target, n.message["title"], n.message["data"]),
                         ({"all": True}, "Hi", {"route": "/orders"}))
        self.assertIn("Send test notification",
                      self.client.get("/admin/notifications_app/notification/").content.decode())

    def test_rotate_keys_action(self):
        w = World()
        r = self.client.post("/admin/tenants_app/tenant/", {
            "action": "rotate_keys_revoke_old", "_selected_action": [str(w.tenant.id)]}, follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(w.server.get("/v1/notifications").status_code, 401)

    def test_admin_requires_login(self):
        self.client.logout()
        self.assertEqual(self.client.get("/admin/tenants_app/credential/").status_code, 302)

    def test_requeue_job_action(self):
        job = Job.objects.create(kind="expand", payload={}, run_at="2000-01-01T00:00:00Z", status=Job.FAILED)
        self.client.post("/admin/dispatch_app/job/", {"action": "requeue", "_selected_action": [job.id]})
        job.refresh_from_db()
        self.assertEqual(job.status, Job.PENDING)


class HealthTests(TestCase):
    def test_healthz(self):
        r = self.client.get("/healthz")
        self.assertEqual((r.status_code, r.json()), (200, {"ok": True}))

    def test_unknown_url_404(self):
        self.assertEqual(self.client.get("/v1/does-not-exist").status_code, 404)
