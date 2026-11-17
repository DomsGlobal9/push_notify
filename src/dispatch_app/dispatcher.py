"""The dispatcher: turns queued notifications into provider requests.

Two job kinds:

  expand      page through a notification's audience (500 device ids at a time,
              keyset on device id) and enqueue one send_batch per page. Re-enqueues
              itself for the next page, so even a million-device broadcast is a
              chain of small jobs and never holds the queue.
  send_batch  send one page. HTTP happens in a thread pool (PUSH["SEND_THREADS"])
              over long-lived HTTP/2 senders; the database work happens after, in
              one transaction: counters, dead-token deactivation, failure rows, the
              retry batch for transient errors, and the job's own removal.

Retries: a device that gets 429/5xx/network goes into a new send_batch with
`attempt + 1`, delayed by max(Retry-After, exponential backoff), up to
PUSH["MAX_SEND_ATTEMPTS"]; after that it is counted failed.

Scaling: run more dispatcher processes (Render background worker instances).
They share the queue through SKIP LOCKED and need no coordination.
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from django.conf import settings
from django.db import close_old_connections, transaction
from django.db.models import F
from django.utils import timezone

from src.devices_app import services as device_services
from src.devices_app.models import Device
from src.notifications_app import services as notification_services
from src.notifications_app.models import DeliveryFailure, Notification

from . import queue
from .models import Job
from .providers import SendResult, Target
from .providers import registry

logger = logging.getLogger(__name__)

_executor: ThreadPoolExecutor | None = None


def _pool() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=settings.PUSH["SEND_THREADS"], thread_name_prefix="push-send")
    return _executor


# ---------------------------------------------------------------- expand
def handle_expand(job: Job) -> None:
    nid = job.payload["notification_id"]
    n = Notification.objects.filter(id=nid).first()
    if n is None or n.status in (Notification.CANCELLED, Notification.COMPLETED):
        queue.finish(job)
        return
    batch_size = settings.PUSH["BATCH_SIZE"]
    ids = notification_services.target_page(n, int(job.payload.get("after", 0)), batch_size)

    with transaction.atomic():
        updates = {"status": Notification.PROCESSING}
        if ids:
            queue.enqueue(queue.SEND_BATCH, {"notification_id": nid, "device_ids": ids, "attempt": 1})
            updates.update(pending_batches=F("pending_batches") + 1, targeted_count=F("targeted_count") + len(ids))
        if len(ids) == batch_size:
            queue.enqueue(queue.EXPAND, {"notification_id": nid, "after": ids[-1]})
        else:
            updates["expand_done"] = True
        Notification.objects.filter(id=nid).exclude(status=Notification.CANCELLED).update(**updates)
        queue.finish(job)
        notification_services.settle_if_done(nid)


# ---------------------------------------------------------------- send
def _send_one(sender, target: Target, message: dict, nid: str) -> SendResult:
    try:
        return sender.send(target, message, nid)
    except Exception as exc:  # a provider bug must cost one device, not the batch
        logger.exception("Provider %s crashed on device %s", getattr(sender, "provider", "?"), target.device_id)
        return SendResult("retry", None, f"internal: {type(exc).__name__}: {exc}"[:300])


def handle_send_batch(job: Job) -> None:
    nid = job.payload["notification_id"]
    attempt = int(job.payload.get("attempt", 1))
    n = Notification.objects.filter(id=nid).first()
    if n is None:
        queue.finish(job)
        return
    if n.status == Notification.CANCELLED:
        with transaction.atomic():
            Notification.objects.filter(id=nid).update(pending_batches=F("pending_batches") - 1)
            queue.finish(job)
        return

    devices = list(
        Device.objects.select_related("app__credential")
        .filter(id__in=job.payload["device_ids"], is_active=True)
    )
    # Unregistered or deactivated since the audience was paged.
    skipped = len(set(job.payload["device_ids"])) - len(devices)
    work = []
    for d in devices:
        sender = registry.sender_for(d.app.credential)
        target = Target(d.id, d.token, d.app.platform, d.app.bundle_id, d.apns_environment,
                        d.app.android_channel_id)
        work.append((d, sender, target))

    futures = [(d, s.provider, _pool().submit(_send_one, s, t, n.message, nid)) for d, s, t in work]
    results = [(d, provider, f.result()) for d, provider, f in futures]

    sent = 0
    invalid_ids, failures, retry_ids = [], [], []
    retry_after = 0.0
    for d, provider, r in results:
        if r.outcome == "sent":
            sent += 1
        elif r.outcome == "invalid_token":
            invalid_ids.append(d.id)
            failures.append((d.id, provider, r.status_code, f"invalid_token: {r.reason}"))
        elif r.outcome == "retry":
            retry_ids.append((d.id, provider, r))
            retry_after = max(retry_after, r.retry_after or 0)
        else:
            failures.append((d.id, provider, r.status_code, r.reason))

    max_attempts = settings.PUSH["MAX_SEND_ATTEMPTS"]
    will_retry = bool(retry_ids) and attempt < max_attempts
    failed = len([f for f in failures if not f[3].startswith("invalid_token")])
    if retry_ids and not will_retry:
        failed += len(retry_ids)
        failures += [(i, p, r.status_code, f"gave up after {attempt} attempts: {r.reason}") for i, p, r in retry_ids]

    with transaction.atomic():
        if will_retry:
            delay = max(retry_after, min(300, 5 * (2 ** attempt)))
            queue.enqueue(queue.SEND_BATCH,
                          {"notification_id": nid, "device_ids": [i for i, _, _ in retry_ids], "attempt": attempt + 1},
                          delay_seconds=delay)
        Notification.objects.filter(id=nid).update(
            sent_count=F("sent_count") + sent,
            failed_count=F("failed_count") + failed,
            invalid_token_count=F("invalid_token_count") + len(invalid_ids),
            skipped_count=F("skipped_count") + skipped,
            pending_batches=F("pending_batches") - (0 if will_retry else 1),
        )
        device_services.deactivate(invalid_ids, "provider_rejected_token")
        if failures:
            notification_services.record_failures(nid, failures)
        queue.finish(job)
        notification_services.settle_if_done(nid)

    if retry_ids or failures:
        logger.info("notification %s batch: sent=%s invalid=%s failed=%s retry=%s (attempt %s)",
                    nid, sent, len(invalid_ids), failed, len(retry_ids) if will_retry else 0, attempt)


HANDLERS = {queue.EXPAND: handle_expand, queue.SEND_BATCH: handle_send_batch}
MAX_JOB_ATTEMPTS = 5


def give_up(job: Job, error: str) -> None:
    """A job crashed MAX_JOB_ATTEMPTS times. Account for it so its notification still
    completes instead of sitting in 'processing' forever."""
    nid = (job.payload or {}).get("notification_id")
    with transaction.atomic():
        queue.fail(job, error, retry=False)
        if not nid or not Notification.objects.filter(id=nid).exists():
            return
        if job.kind == queue.SEND_BATCH:
            ids = job.payload.get("device_ids") or []
            Notification.objects.filter(id=nid).update(
                failed_count=F("failed_count") + len(ids), pending_batches=F("pending_batches") - 1)
            notification_services.record_failures(nid, [(None, "-", None, f"batch of {len(ids)} abandoned: {error}")])
        elif job.kind == queue.EXPAND:
            # Devices after this page were never targeted; finish with what was sent.
            Notification.objects.filter(id=nid).exclude(status=Notification.CANCELLED).update(
                status=Notification.PROCESSING, expand_done=True)
            notification_services.record_failures(nid, [(None, "-", None, f"audience paging abandoned: {error}")])
        notification_services.settle_if_done(nid)


# ---------------------------------------------------------------- loop
def process(job: Job) -> None:
    handler = HANDLERS.get(job.kind)
    if handler is None:
        queue.fail(job, f"unknown job kind {job.kind}", retry=False)
        return
    try:
        handler(job)
    except Exception as exc:
        logger.exception("Job %s failed (attempt %s)", job, job.attempts)
        error = f"{type(exc).__name__}: {exc}"
        if job.attempts < MAX_JOB_ATTEMPTS:
            queue.fail(job, error, retry=True)
        else:
            give_up(job, error)


def run_once(limit: int = 4) -> int:
    jobs = queue.claim(limit)
    for job in jobs:
        process(job)
    return len(jobs)


def purge_old_data() -> dict:
    p = settings.PUSH
    now = timezone.now()
    failures, _ = DeliveryFailure.objects.filter(
        created_at__lt=now - timedelta(days=p["FAILURE_RETENTION_DAYS"])).delete()
    notifications, _ = Notification.objects.filter(
        status__in=[Notification.COMPLETED, Notification.CANCELLED],
        created_at__lt=now - timedelta(days=p["NOTIFICATION_RETENTION_DAYS"])).delete()
    jobs, _ = Job.objects.filter(status=Job.FAILED, created_at__lt=now - timedelta(days=7)).delete()
    return {"failures": failures, "notifications": notifications, "failed_jobs": jobs}


class Dispatcher:
    """The long-running loop behind `manage.py run_dispatcher`."""

    def __init__(self):
        self.stopping = False
        self._last_recover = 0.0
        self._last_purge = 0.0

    def stop(self, *_):
        logger.info("Dispatcher stopping after the current job")
        self.stopping = True

    def _housekeeping(self):
        now = time.monotonic()
        if now - self._last_recover > 60:
            recovered = queue.recover_stale(settings.PUSH["STALE_JOB_MINUTES"])
            if recovered:
                logger.warning("Recovered %s stale jobs", recovered)
            self._last_recover = now
        if now - self._last_purge > 3600:
            logger.info("Purged old data: %s", purge_old_data())
            self._last_purge = now

    def run(self):
        logger.info("Dispatcher started (threads=%s, batch=%s)", settings.PUSH["SEND_THREADS"],
                    settings.PUSH["BATCH_SIZE"])
        while not self.stopping:
            close_old_connections()
            try:
                self._housekeeping()
                processed = run_once()
            except Exception:
                logger.exception("Dispatcher loop error; backing off")
                processed = 0
                time.sleep(5)
            if not processed:
                time.sleep(settings.PUSH["POLL_SECONDS"])
        registry.reset()
        if _executor:
            _executor.shutdown(wait=True)
