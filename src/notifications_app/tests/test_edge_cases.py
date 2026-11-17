"""Send API edge cases: payload limits, unicode, idempotency races, tenancy, throttling."""

import threading
import uuid
from datetime import timedelta

from django.core.cache import cache
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from src.core.testing import World
from src.dispatch_app.models import Job
from src.notifications_app.models import Notification


class PayloadEdgeCases(TestCase):
    def setUp(self):
        self.w = World()

    def send(self, message, target=None, **extra):
        return self.w.server.post("/v1/notifications", {"target": target or {"all": True}, "message": message,
                                                        **extra}, format="json")

    def test_unicode_and_emoji(self):
        r = self.send({"title": "ఆర్డర్ సిద్ధం 🎉", "body": "आपका ऑर्डर तैयार है", "data": {"k": "✓"}})
        self.assertEqual(r.status_code, 202)
        self.assertEqual(Notification.objects.get().message["title"], "ఆర్డర్ సిద్ధం 🎉")

    def test_size_limit_counts_bytes_not_characters(self):
        # 1,200 Telugu characters are ~3,600 bytes: over the limit despite being short in characters.
        r = self.send({"title": "x", "body": "ఆ" * 1200})
        self.assertEqual(r.status_code, 400)
        self.assertIn("bytes", str(r.data))
        self.assertEqual(self.send({"title": "x", "body": "a" * 2000}).status_code, 202)

    def test_big_data_rejected(self):
        data = {f"k{i}": "v" * 900 for i in range(5)}
        self.assertEqual(self.send({"data": data}).status_code, 400)

    def test_data_values_must_be_strings_or_coercible(self):
        self.assertEqual(self.send({"data": {"n": 7}}).status_code, 202)  # coerced to "7"
        self.assertEqual(Notification.objects.get().message["data"], {"n": "7"})
        self.assertEqual(self.send({"data": {"nested": {"a": 1}}}).status_code, 400)
        self.assertEqual(self.send({"data": {"list": [1]}}).status_code, 400)

    def test_reserved_data_keys(self):
        for key in ("_ngw_id", "_ngw_x", "google.c.a", "gcm.n.e", "from", "aps", "message_type"):
            self.assertEqual(self.send({"data": {key: "1"}}).status_code, 400, key)

    def test_blank_title_and_body_need_something_else(self):
        self.assertEqual(self.send({"title": "", "body": ""}).status_code, 400)
        self.assertEqual(self.send({"title": "", "body": "", "content_available": True}).status_code, 202)

    def test_field_bounds(self):
        self.assertEqual(self.send({"title": "t", "ttl_seconds": -1}).status_code, 400)
        self.assertEqual(self.send({"title": "t", "ttl_seconds": 2419201}).status_code, 400)
        self.assertEqual(self.send({"title": "t", "ttl_seconds": 0}).status_code, 202)
        self.assertEqual(self.send({"title": "t", "badge": -1}).status_code, 400)
        self.assertEqual(self.send({"title": "t", "priority": "urgent"}).status_code, 400)
        self.assertEqual(self.send({"title": "t", "image": "not a url"}).status_code, 400)
        self.assertEqual(self.send({"title": "t" * 201}).status_code, 400)

    def test_collapse_key_byte_limit(self):
        self.assertEqual(self.send({"title": "t", "collapse_key": "k" * 64}).status_code, 202)
        self.assertEqual(self.send({"title": "t", "collapse_key": "ఆ" * 30}).status_code, 400)  # 90 bytes

    def test_target_shapes(self):
        self.assertEqual(self.send({"title": "t"}, {"all": False}).status_code, 400)
        self.assertEqual(self.send({"title": "t"}, {"user_ids": []}).status_code, 400)
        self.assertEqual(self.send({"title": "t"}, {"topics": ["bad topic"]}).status_code, 400)
        self.assertEqual(self.send({"title": "t"}, {"all": True, "platforms": ["web"]}).status_code, 400)
        self.assertEqual(self.send({"title": "t"}, {"user_ids": ["u"] * 10001}).status_code, 400)
        self.assertEqual(self.send({"title": "t"}, {"user_ids": [f"u{i}" for i in range(10000)]}).status_code, 202)

    def test_send_at_past_and_naive(self):
        past = (timezone.now() - timedelta(days=1)).isoformat()
        self.assertEqual(self.send({"title": "t"}, send_at=past).status_code, 202)
        self.assertEqual(self.send({"title": "t"}, send_at="2030-01-01T09:00:00").status_code, 202)
        self.assertEqual(self.send({"title": "t"}, send_at="tomorrow").status_code, 400)

    def test_missing_message_or_target(self):
        self.assertEqual(self.w.server.post("/v1/notifications", {"target": {"all": True}},
                                            format="json").status_code, 400)
        self.assertEqual(self.w.server.post("/v1/notifications", {"message": {"title": "t"}},
                                            format="json").status_code, 400)


class IdempotencyEdgeCases(TestCase):
    def setUp(self):
        self.w = World()

    def send(self, key, title="t", client=None):
        return (client or self.w.server).post(
            "/v1/notifications", {"target": {"all": True}, "message": {"title": title}}, format="json",
            headers={"Idempotency-Key": key})

    def test_same_key_different_body_returns_original(self):
        a = self.send("k1", "first")
        b = self.send("k1", "second")
        self.assertEqual(a.data["id"], b.data["id"])
        self.assertEqual(b.data["message"]["title"], "first")

    def test_keys_are_per_tenant(self):
        other = World("Other Product")
        a = self.send("shared-key")
        b = self.send("shared-key", client=other.server)
        self.assertNotEqual(a.data["id"], b.data["id"])

    def test_overlong_and_blank_keys(self):
        self.assertEqual(self.send("k" * 256).status_code, 400)
        self.assertEqual(self.send("   ").status_code, 202)
        self.assertEqual(self.send("   ").status_code, 202)  # blank means "no key"
        self.assertEqual(Notification.objects.count(), 2)


class TenancyAndLookupEdgeCases(TestCase):
    def setUp(self):
        self.w = World()

    def test_unknown_and_malformed_ids(self):
        self.assertEqual(self.w.server.get(f"/v1/notifications/{uuid.uuid4()}").status_code, 404)
        self.assertEqual(self.w.server.get("/v1/notifications/not-a-uuid").status_code, 404)

    def test_list_limit_bounds(self):
        for _ in range(3):
            self.w.server.post("/v1/notifications", {"target": {"all": True}, "message": {"title": "t"}},
                               format="json")
        self.assertEqual(len(self.w.server.get("/v1/notifications?limit=2").data), 2)
        self.assertEqual(len(self.w.server.get("/v1/notifications?limit=0").data), 1)
        self.assertEqual(len(self.w.server.get("/v1/notifications?limit=abc").data), 3)
        self.assertEqual(len(self.w.server.get("/v1/notifications?limit=-5").data), 1)

    def test_events_from_other_tenant_do_not_count(self):
        nid = self.w.server.post("/v1/notifications", {"target": {"all": True}, "message": {"title": "t"}},
                                 format="json").data["id"]
        other = World("Other Product")
        r = other.app_client.post("/v1/events", {"notification_id": nid}, format="json")
        self.assertEqual(r.status_code, 202)
        self.assertEqual(Notification.objects.get().opened_count, 0)

    def test_event_for_unknown_or_bad_id(self):
        self.assertEqual(self.w.app_client.post("/v1/events", {"notification_id": str(uuid.uuid4())},
                                                format="json").status_code, 202)
        self.assertEqual(self.w.app_client.post("/v1/events", {"notification_id": "nope"},
                                                format="json").status_code, 400)
        self.assertEqual(self.w.app_client.post("/v1/events", {"notification_id": str(uuid.uuid4()),
                                                               "type": "clicked"}, format="json").status_code, 400)

    def test_cancel_scheduled_removes_its_job(self):
        when = (timezone.now() + timedelta(days=1)).isoformat()
        nid = self.w.server.post("/v1/notifications", {"target": {"all": True}, "message": {"title": "t"},
                                                       "send_at": when}, format="json").data["id"]
        self.assertEqual(Job.objects.count(), 1)
        self.w.server.post(f"/v1/notifications/{nid}/cancel")
        self.assertEqual(Job.objects.count(), 0)


class ThrottleTests(TestCase):
    def setUp(self):
        from unittest import mock

        from rest_framework.throttling import SimpleRateThrottle
        cache.clear()
        patcher = mock.patch.object(SimpleRateThrottle, "THROTTLE_RATES",
                                    {"tenant_send": "3/min", "tenant_device": "1000/min"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(cache.clear)

    def test_send_rate_is_per_tenant(self):
        a, b = World("A"), World("B")
        body = {"target": {"all": True}, "message": {"title": "t"}}
        codes = [a.server.post("/v1/notifications", body, format="json").status_code for _ in range(4)]
        self.assertEqual(codes, [202, 202, 202, 429])
        self.assertEqual(b.server.post("/v1/notifications", body, format="json").status_code, 202)
        self.assertEqual(a.server.get("/v1/notifications").status_code, 200)  # reads are not throttled


class IdempotencyRaceTests(TransactionTestCase):
    def test_concurrent_duplicates_create_one_notification(self):
        w = World()
        codes = []

        def go():
            try:
                r = w.server.post("/v1/notifications", {"target": {"all": True}, "message": {"title": "t"}},
                                  format="json", headers={"Idempotency-Key": "race-key"})
                codes.append(r.status_code)
            finally:
                connection.close()

        threads = [threading.Thread(target=go) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(Notification.objects.count(), 1)
        self.assertEqual(Job.objects.count(), 1)
        self.assertEqual(codes.count(202), 1, codes)
        self.assertTrue(all(c in (200, 202, 409) for c in codes), codes)
