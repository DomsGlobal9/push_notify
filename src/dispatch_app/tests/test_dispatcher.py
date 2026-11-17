"""End-to-end through the queue with fake providers: paging, retries, dead tokens, completion."""

from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from src.core.testing import World
from src.devices_app.models import Device
from src.dispatch_app import dispatcher, queue
from src.dispatch_app.models import Job
from src.dispatch_app.providers import SendResult, registry
from src.notifications_app import services as notification_services
from src.notifications_app.models import DeliveryFailure, Notification


class FakeSender:
    """Answers per token from a script; default is 'sent'."""

    def __init__(self, provider, script):
        self.provider = provider
        self.script = script
        self.calls = []

    def send(self, target, message, nid):
        self.calls.append((target.token, message.get("title"), nid))
        outcome = self.script.get(target.token)
        if callable(outcome):
            return outcome()
        return outcome or SendResult("sent", 200)

    def close(self):
        pass


PUSH = {"BATCH_SIZE": 3, "SEND_THREADS": 4, "MAX_SEND_ATTEMPTS": 3, "STALE_JOB_MINUTES": 10,
        "FAILURE_RETENTION_DAYS": 14, "NOTIFICATION_RETENTION_DAYS": 90, "POLL_SECONDS": 0.01}


@override_settings(PUSH=PUSH)
class DispatcherTests(TestCase):
    def setUp(self):
        self.w = World()
        self.script = {}
        self.senders = {}

        def factory(cred):
            s = FakeSender(cred.provider, self.script)
            self.senders[cred.provider] = s
            return s

        registry.reset()
        registry.factory = factory

    def tearDown(self):
        registry.factory = None
        registry.reset()

    def drain(self):
        """Run every job that is due, making delayed retries due immediately."""
        for _ in range(100):
            Job.objects.filter(status=Job.PENDING).update(run_at=timezone.now() - timedelta(seconds=1))
            if not dispatcher.run_once(limit=10):
                return
        self.fail("queue did not drain")

    def send(self, target, **message):
        n, _ = notification_services.create(self.w.tenant, target, {"title": "Hi", **message})
        return n

    def test_broadcast_pages_through_every_device(self):
        for i in range(7):
            self.w.register(f"android-{i:04}")
        for i in range(2):
            self.w.register(f"ios-dev-{i:04}", platform="ios")
        n = self.send({"all": True})
        self.drain()
        n.refresh_from_db()
        self.assertEqual(n.status, Notification.COMPLETED)
        self.assertEqual((n.targeted_count, n.sent_count, n.failed_count), (9, 9, 0))
        self.assertEqual(n.pending_batches, 0)
        self.assertEqual(len(self.senders["fcm"].calls), 7)
        self.assertEqual(len(self.senders["apns"].calls), 2)
        self.assertFalse(Job.objects.exists())

    def test_targets_only_the_users_devices(self):
        self.w.register("install-u1-a", user="u1")
        self.w.register("install-u1-b", platform="ios", user="u1")
        self.w.register("install-u2-a", user="u2")
        n = self.send({"user_ids": ["u1"]})
        self.drain()
        n.refresh_from_db()
        self.assertEqual(n.sent_count, 2)

    def test_topic_and_platform_filters(self):
        self.w.register("install-t1", topics=["offers"])
        self.w.register("install-t2", platform="ios", topics=["offers"])
        self.w.register("install-t3", topics=["other"])
        n = self.send({"topics": ["offers"], "platforms": ["android"]})
        self.drain()
        n.refresh_from_db()
        self.assertEqual((n.targeted_count, n.sent_count), (1, 1))

    def test_dead_token_deactivates_the_device(self):
        self.w.register("install-dead", token="dead-token-0001")
        self.w.register("install-live", token="live-token-0001")
        self.script["dead-token-0001"] = SendResult("invalid_token", 404, "UNREGISTERED")
        n = self.send({"all": True})
        self.drain()
        n.refresh_from_db()
        self.assertEqual((n.sent_count, n.invalid_token_count, n.failed_count), (1, 1, 0))
        self.assertFalse(Device.objects.get(installation_id="install-dead").is_active)
        self.assertEqual(DeliveryFailure.objects.get().status_code, 404)
        # next send does not even try the dead device
        self.send({"all": True})
        self.drain()
        self.assertEqual([c[0] for c in self.senders["fcm"].calls].count("dead-token-0001"), 1)

    def test_transient_error_retries_then_succeeds(self):
        self.w.register("install-flaky", token="flaky-token-001")
        answers = iter([SendResult("retry", 503, "UNAVAILABLE"), SendResult("sent", 200)])
        self.script["flaky-token-001"] = lambda: next(answers)
        n = self.send({"all": True})
        self.drain()
        n.refresh_from_db()
        self.assertEqual((n.status, n.sent_count, n.failed_count), (Notification.COMPLETED, 1, 0))

    def test_gives_up_after_max_attempts(self):
        self.w.register("install-down", token="down-token-0001")
        self.script["down-token-0001"] = SendResult("retry", 503, "UNAVAILABLE")
        n = self.send({"all": True})
        self.drain()
        n.refresh_from_db()
        self.assertEqual((n.status, n.sent_count, n.failed_count), (Notification.COMPLETED, 0, 1))
        self.assertEqual(len(self.senders["fcm"].calls), 3)
        self.assertIn("gave up after 3 attempts", DeliveryFailure.objects.get().reason)
        self.assertTrue(Device.objects.get().is_active)

    def test_retry_delay_respects_retry_after(self):
        self.w.register("install-slow", token="slow-token-0001")
        self.script["slow-token-0001"] = SendResult("retry", 429, "QUOTA_EXCEEDED", retry_after=120)
        self.send({"all": True})
        dispatcher.run_once()  # expand
        dispatcher.run_once()  # first send
        retry = Job.objects.get(kind=queue.SEND_BATCH)
        self.assertGreaterEqual(retry.run_at, timezone.now() + timedelta(seconds=110))

    def test_provider_crash_costs_one_device_not_the_batch(self):
        self.w.register("install-boom", token="boom-token-0001")
        self.w.register("install-fine", token="fine-token-0001")

        def boom():
            raise RuntimeError("bug")
        self.script["boom-token-0001"] = boom
        n = self.send({"all": True})
        self.drain()
        n.refresh_from_db()
        self.assertEqual((n.sent_count, n.failed_count), (1, 1))

    def test_empty_audience_completes(self):
        n = self.send({"user_ids": ["nobody"]})
        self.drain()
        n.refresh_from_db()
        self.assertEqual((n.status, n.targeted_count), (Notification.COMPLETED, 0))

    def test_scheduled_send_waits(self):
        self.w.register("install-later")
        n, _ = notification_services.create(self.w.tenant, {"all": True}, {"title": "Later"},
                                            send_at=timezone.now() + timedelta(hours=1))
        self.assertEqual(dispatcher.run_once(), 0)
        n.refresh_from_db()
        self.assertEqual(n.status, Notification.QUEUED)

    def test_cancel_mid_flight_stops_sending(self):
        for i in range(6):
            self.w.register(f"install-c{i:03}")
        n = self.send({"all": True})
        dispatcher.run_once(limit=1)  # expand page 1 -> one batch + next expand
        notification_services.cancel(n)
        self.drain()
        self.assertNotIn("fcm", self.senders)  # nothing was sent
        n.refresh_from_db()
        self.assertEqual(n.status, Notification.CANCELLED)

    def test_message_reaches_provider_with_notification_id(self):
        self.w.register("install-msg1")
        n = self.send({"all": True})
        self.drain()
        token, title, nid = self.senders["fcm"].calls[0]
        self.assertEqual((title, nid), ("Hi", str(n.id)))

    def test_stale_running_jobs_are_recovered(self):
        job = queue.enqueue(queue.EXPAND, {"notification_id": "x"})
        Job.objects.filter(id=job.id).update(status=Job.RUNNING,
                                             locked_at=timezone.now() - timedelta(minutes=30))
        self.assertEqual(queue.recover_stale(10), 1)
        self.assertEqual(Job.objects.get().status, Job.PENDING)

    def test_purge_old_data(self):
        self.w.register("install-old")
        n = self.send({"all": True})
        self.drain()
        Notification.objects.filter(id=n.id).update(created_at=timezone.now() - timedelta(days=200))
        self.assertEqual(dispatcher.purge_old_data()["notifications"], 1)
