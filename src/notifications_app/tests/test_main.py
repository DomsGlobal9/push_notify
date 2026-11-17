"""The send API: validation, idempotency, permissions, cancel, open tracking."""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from src.core.testing import World
from src.dispatch_app.models import Job
from src.notifications_app.models import Notification

MSG = {"title": "Order ready", "body": "Your blouse is ready for pickup"}


class SendApiTests(TestCase):
    def setUp(self):
        self.w = World()

    def send(self, target, message=MSG, client=None, **headers):
        return (client or self.w.server).post("/v1/notifications", {"target": target, "message": message},
                                              format="json", headers=headers)

    def test_send_returns_202_and_queues_expand(self):
        r = self.send({"user_ids": ["cust-1"]})
        self.assertEqual(r.status_code, 202, r.data)
        self.assertEqual(r.data["status"], "queued")
        job = Job.objects.get()
        self.assertEqual((job.kind, job.payload["notification_id"]), ("expand", r.data["id"]))

    def test_publishable_key_cannot_send(self):
        self.assertEqual(self.send({"all": True}, client=self.w.app_client).status_code, 403)

    def test_target_needs_exactly_one_selector(self):
        self.assertEqual(self.send({}).status_code, 400)
        self.assertEqual(self.send({"all": True, "user_ids": ["x"]}).status_code, 400)

    def test_message_needs_content_and_no_reserved_keys(self):
        self.assertEqual(self.send({"all": True}, {}).status_code, 400)
        self.assertEqual(self.send({"all": True}, {"data": {"_ngw_id": "x"}}).status_code, 400)
        self.assertEqual(self.send({"all": True}, {"data": {"order_id": "7"}}).status_code, 202)

    def test_idempotency_key_returns_the_original(self):
        first = self.send({"all": True}, **{"Idempotency-Key": "order-7-ready"})
        again = self.send({"all": True}, **{"Idempotency-Key": "order-7-ready"})
        self.assertEqual(first.status_code, 202)
        self.assertEqual(again.status_code, 200)
        self.assertEqual(first.data["id"], again.data["id"])
        self.assertEqual(Notification.objects.count(), 1)
        self.assertEqual(Job.objects.count(), 1)

    def test_scheduled_send_queues_for_later(self):
        when = timezone.now() + timedelta(hours=2)
        r = self.w.server.post("/v1/notifications", {"target": {"all": True}, "message": MSG,
                                                     "send_at": when.isoformat()}, format="json")
        self.assertEqual(r.status_code, 202)
        self.assertGreater(Job.objects.get().run_at, timezone.now() + timedelta(hours=1))

    def test_detail_list_and_cancel(self):
        nid = self.send({"all": True}).data["id"]
        self.assertEqual(self.w.server.get(f"/v1/notifications/{nid}").data["recent_failures"], [])
        self.assertEqual(len(self.w.server.get("/v1/notifications").data), 1)
        r = self.w.server.post(f"/v1/notifications/{nid}/cancel")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Job.objects.exists())
        self.assertEqual(self.w.server.post(f"/v1/notifications/{nid}/cancel").status_code, 409)

    def test_other_tenant_cannot_read_it(self):
        nid = self.send({"all": True}).data["id"]
        other = World("Other")
        self.assertEqual(other.server.get(f"/v1/notifications/{nid}").status_code, 404)
        self.assertEqual(other.server.post(f"/v1/notifications/{nid}/cancel").status_code, 404)

    def test_open_event_counts(self):
        nid = self.send({"all": True}).data["id"]
        r = self.w.app_client.post("/v1/events", {"notification_id": nid, "type": "opened"}, format="json")
        self.assertEqual(r.status_code, 202)
        self.assertEqual(Notification.objects.get().opened_count, 1)
