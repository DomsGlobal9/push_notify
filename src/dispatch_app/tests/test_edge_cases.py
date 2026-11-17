"""Dispatcher worst cases: things changing mid-flight, crashes, two dispatchers at once.

Every scenario ends with the accounting invariant:
    targeted = sent + failed + invalid_token + skipped, and the notification is settled.
"""

import threading
from datetime import timedelta
from unittest import mock

from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from src.core.testing import World, fcm_secret
from src.devices_app.models import Device
from src.dispatch_app import dispatcher, queue
from src.dispatch_app.models import Job
from src.dispatch_app.providers import SendResult, registry
from src.notifications_app import services as ns
from src.notifications_app.models import DeliveryFailure, Notification
from src.tenants_app import services as ts
from src.tenants_app.models import Credential, Tenant

PUSH = {"BATCH_SIZE": 3, "SEND_THREADS": 4, "MAX_SEND_ATTEMPTS": 3, "STALE_JOB_MINUTES": 10,
        "FAILURE_RETENTION_DAYS": 14, "NOTIFICATION_RETENTION_DAYS": 90, "POLL_SECONDS": 0.01}


class Fake:
    def __init__(self, cred, script, log):
        self.provider = cred.provider
        self.revision = cred.revision
        self.script = script
        self.log = log

    def send(self, target, message, nid):
        self.log.append((target.token, self.revision))
        out = self.script.get(target.token)
        return out() if callable(out) else (out or SendResult("sent", 200))

    def close(self):
        pass


class Base:
    def setup_fakes(self):
        self.script, self.log = {}, []
        registry.reset()
        registry.factory = lambda cred: Fake(cred, self.script, self.log)
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        registry.factory = None
        registry.reset()

    def drain(self, limit=10):
        for _ in range(200):
            Job.objects.filter(status=Job.PENDING).update(run_at=timezone.now() - timedelta(seconds=1))
            if not dispatcher.run_once(limit=limit):
                return
        raise AssertionError("queue did not drain")

    def assert_settled(self, n):
        n.refresh_from_db()
        self.assertIn(n.status, (Notification.COMPLETED, Notification.CANCELLED))
        if n.status == Notification.COMPLETED:
            self.assertEqual(n.targeted_count,
                             n.sent_count + n.failed_count + n.invalid_token_count + n.skipped_count,
                             f"unbalanced: {n.__dict__}")
            self.assertEqual(n.pending_batches, 0)
        return n


@override_settings(PUSH=PUSH)
class MidFlightTests(Base, TestCase):
    def setUp(self):
        self.w = World()
        self.setup_fakes()

    def send(self, target=None, **msg):
        return ns.create(self.w.tenant, target or {"all": True}, {"title": "t", **msg})[0]

    def expand_only(self):
        dispatcher.run_once(limit=1)

    def test_device_deactivated_after_paging_is_skipped(self):
        self.w.register("install-0001")
        self.w.register("install-0002")
        n = self.send()
        self.expand_only()
        Device.objects.filter(installation_id="install-0001").update(is_active=False)
        self.drain()
        n = self.assert_settled(n)
        self.assertEqual((n.sent_count, n.skipped_count), (1, 1))

    def test_device_deleted_after_paging_is_skipped(self):
        self.w.register("install-0001")
        n = self.send()
        self.expand_only()
        Device.objects.all().delete()
        self.drain()
        n = self.assert_settled(n)
        self.assertEqual((n.targeted_count, n.skipped_count), (1, 1))

    def test_token_refreshed_after_paging_uses_new_token(self):
        self.w.register("install-0001", token="old-token-00001")
        n = self.send()
        self.expand_only()
        self.w.register("install-0001", token="new-token-00001")
        self.drain()
        self.assertEqual([t for t, _ in self.log], ["new-token-00001"])
        self.assert_settled(n)

    def test_audience_exact_multiple_of_batch_size(self):
        for i in range(6):
            self.w.register(f"install-{i:04}")
        n = self.send()
        self.drain()
        n = self.assert_settled(n)
        self.assertEqual((n.targeted_count, n.sent_count), (6, 6))

    def test_devices_registered_during_a_broadcast(self):
        for i in range(4):
            self.w.register(f"install-{i:04}")
        n = self.send()
        self.expand_only()  # page 1: ids 1-3
        self.w.register("install-late01")  # higher id: picked up by the next page
        self.drain()
        n = self.assert_settled(n)
        self.assertEqual(n.sent_count, 5)

    def test_device_in_two_matching_topics_gets_one_push(self):
        self.w.register("install-0001", topics=["a", "b"])
        n = self.send({"topics": ["a", "b"]})
        self.drain()
        self.assertEqual(len(self.log), 1)
        self.assert_settled(n)

    def test_installation_ids_hit_both_platforms(self):
        self.w.register("install-same01", platform="android")
        self.w.register("install-same01", platform="ios")
        n = self.send({"installation_ids": ["install-same01"]})
        self.drain()
        self.assertEqual(self.assert_settled(n).sent_count, 2)

    def test_filters_matching_nothing(self):
        self.w.register("install-0001")
        n = self.send({"all": True, "bundle_ids": ["com.nope"]})
        self.drain()
        self.assertEqual(self.assert_settled(n).targeted_count, 0)

    def test_mixed_outcomes_in_one_batch_balance(self):
        toks = {"ok-token-000001": None,
                "dead-token-00001": SendResult("invalid_token", 410, "Unregistered"),
                "bad-token-000001": SendResult("failed", 400, "PayloadTooLarge"),
                "slow-token-00001": SendResult("retry", 503, "down")}
        for i, t in enumerate(toks):
            self.w.register(f"install-{i:04}", token=t)
        self.script.update({k: v for k, v in toks.items() if v})
        n = self.send()
        self.drain()
        n = self.assert_settled(n)
        self.assertEqual((n.sent_count, n.invalid_token_count, n.failed_count), (1, 1, 2))
        self.assertEqual(DeliveryFailure.objects.filter(notification=n).count(), 3)

    def test_every_device_dead(self):
        for i in range(5):
            self.w.register(f"install-{i:04}", token=f"dead-token-{i:05}")
            self.script[f"dead-token-{i:05}"] = SendResult("invalid_token", 404, "UNREGISTERED")
        n = self.send()
        self.drain()
        n = self.assert_settled(n)
        self.assertEqual(n.invalid_token_count, 5)
        self.assertFalse(Device.objects.filter(is_active=True).exists())

    def test_device_unregistered_while_waiting_for_retry(self):
        self.w.register("install-0001", token="flaky-token-001")
        self.script["flaky-token-001"] = SendResult("retry", 503, "down")
        n = self.send()
        dispatcher.run_once()  # expand
        dispatcher.run_once()  # attempt 1 -> retry batch
        Device.objects.all().delete()
        self.drain()
        n = self.assert_settled(n)
        self.assertEqual((n.failed_count, n.skipped_count), (0, 1))

    def test_cancel_while_retry_pending(self):
        self.w.register("install-0001", token="flaky-token-001")
        self.script["flaky-token-001"] = SendResult("retry", 503, "down")
        n = self.send()
        dispatcher.run_once()
        dispatcher.run_once()
        ns.cancel(n)
        self.drain()
        self.assertEqual(self.assert_settled(n).status, Notification.CANCELLED)
        self.assertEqual(len(self.log), 1)

    def test_tenant_deleted_mid_flight(self):
        self.w.register("install-0001")
        self.send()
        self.expand_only()
        Tenant.objects.all().delete()
        self.drain()
        self.assertFalse(Job.objects.exists())

    def test_credential_rotated_mid_flight_uses_new_secret(self):
        self.w.register("install-0001")
        self.w.register("install-0002")
        self.w.register("install-0003")
        self.w.register("install-0004")
        n = self.send()
        dispatcher.run_once(limit=1)  # expand page 1
        dispatcher.run_once(limit=1)  # send page 1 with revision 1
        ts.save_credential("firebase", Credential.FCM, fcm_secret("rotated"), Credential.objects.get(name="firebase"))
        self.drain()
        self.assertEqual(sorted({rev for _, rev in self.log}), [1, 2])
        self.assert_settled(n)

    def test_scheduled_send_fires_when_due(self):
        self.w.register("install-0001")
        n = ns.create(self.w.tenant, {"all": True}, {"title": "t"}, send_at=timezone.now() + timedelta(hours=1))[0]
        self.assertEqual(dispatcher.run_once(), 0)
        Job.objects.update(run_at=timezone.now() - timedelta(seconds=1))
        self.drain()
        self.assertEqual(self.assert_settled(n).sent_count, 1)


@override_settings(PUSH=PUSH)
class CrashTests(Base, TestCase):
    def setUp(self):
        self.w = World()
        self.setup_fakes()

    def test_unknown_job_kind_fails_without_crashing(self):
        queue.enqueue("mystery", {"x": 1})
        self.drain()
        self.assertEqual(Job.objects.get().status, Job.FAILED)

    def test_expand_that_always_crashes_is_abandoned_and_settles(self):
        self.w.register("install-0001")
        n = ns.create(self.w.tenant, {"all": True}, {"title": "t"})[0]
        with mock.patch.object(ns, "target_page", side_effect=RuntimeError("db hiccup")):
            self.drain()
        n = self.assert_settled(n)
        self.assertEqual(n.status, Notification.COMPLETED)
        self.assertIn("audience paging abandoned", DeliveryFailure.objects.get().reason)
        self.assertEqual(Job.objects.get().attempts, 5)

    def test_send_batch_that_always_crashes_is_abandoned_and_settles(self):
        for i in range(2):
            self.w.register(f"install-{i:04}")
        n = ns.create(self.w.tenant, {"all": True}, {"title": "t"})[0]
        dispatcher.run_once()  # expand ok
        with mock.patch.object(registry, "sender_for", side_effect=RuntimeError("bad credential blob")):
            self.drain()
        n = self.assert_settled(n)
        self.assertEqual((n.status, n.failed_count), (Notification.COMPLETED, 2))

    def test_crash_then_recovery_succeeds(self):
        self.w.register("install-0001")
        n = ns.create(self.w.tenant, {"all": True}, {"title": "t"})[0]
        real = ns.target_page
        calls = {"n": 0}

        def flaky(*a, **k):
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("transient")
            return real(*a, **k)
        with mock.patch.object(ns, "target_page", side_effect=flaky):
            self.drain()
        self.assertEqual(self.assert_settled(n).sent_count, 1)

    def test_worker_died_mid_job_is_recovered_and_sent(self):
        self.w.register("install-0001")
        n = ns.create(self.w.tenant, {"all": True}, {"title": "t"})[0]
        dispatcher.run_once()  # expand
        claimed = queue.claim(1)  # a worker claims the batch... and dies
        self.assertEqual(len(claimed), 1)
        self.assertEqual(dispatcher.run_once(), 0)  # nobody else can take it
        Job.objects.update(locked_at=timezone.now() - timedelta(minutes=30))
        queue.recover_stale(10)
        self.drain()
        self.assertEqual(self.assert_settled(n).sent_count, 1)


@override_settings(PUSH={**PUSH, "FAILURE_RETENTION_DAYS": 14, "NOTIFICATION_RETENTION_DAYS": 90})
class RetentionTests(Base, TestCase):
    def test_boundaries(self):
        w = World()
        old_done = ns.create(w.tenant, {"all": True}, {"title": "a"})[0]
        old_queued = ns.create(w.tenant, {"all": True}, {"title": "b"}, send_at=timezone.now() + timedelta(days=1))[0]
        Notification.objects.filter(id=old_done.id).update(status=Notification.COMPLETED,
                                                           created_at=timezone.now() - timedelta(days=91))
        Notification.objects.filter(id=old_queued.id).update(created_at=timezone.now() - timedelta(days=200))
        ns.record_failures(old_queued.id, [(None, "fcm", 500, "x"), (None, "fcm", 500, "y")])
        f_old, f_new = DeliveryFailure.objects.order_by("id")
        DeliveryFailure.objects.filter(id=f_old.id).update(created_at=timezone.now() - timedelta(days=15))
        DeliveryFailure.objects.filter(id=f_new.id).update(created_at=timezone.now() - timedelta(days=13))
        result = dispatcher.purge_old_data()
        self.assertEqual((result["notifications"], result["failures"]), (1, 1))
        self.assertTrue(Notification.objects.filter(id=old_queued.id).exists())  # never purge unfinished


@override_settings(PUSH={**PUSH, "BATCH_SIZE": 5})
class TwoDispatchersTests(Base, TransactionTestCase):
    """Two dispatcher processes on one queue: every device exactly once, counters exact."""

    def test_parallel_dispatchers_never_double_send(self):
        w = World()
        self.setup_fakes()
        for i in range(60):
            w.register(f"install-{i:04}", platform="android" if i % 3 else "ios")
        n = ns.create(w.tenant, {"all": True}, {"title": "t"})[0]
        errors = []

        def worker():
            try:
                idle = 0
                while idle < 20:
                    idle = 0 if dispatcher.run_once(limit=2) else idle + 1
            except Exception as exc:  # pragma: no cover - surfaced below
                errors.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        tokens = [t for t, _ in self.log]
        self.assertEqual(len(tokens), 60)
        self.assertEqual(len(set(tokens)), 60)
        n = self.assert_settled(n)
        self.assertEqual(n.sent_count, 60)
