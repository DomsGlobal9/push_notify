"""The job queue, in Postgres.

Why Postgres and not Redis: enqueueing happens in the same transaction as the
row it is about (a Notification, a batch's counters), so a job can never exist
for a notification that rolled back, and a batch's results and its follow-up
retry commit together or not at all. Redis would need an outbox to get that.

Claiming uses `FOR UPDATE SKIP LOCKED`, so any number of dispatcher processes
can run side by side without stepping on each other: that is how this scales
horizontally on Render (add worker instances).

A worker that dies mid-job leaves the job `running`; `recover_stale` returns it
to the queue after PUSH["STALE_JOB_MINUTES"]. Delivery is therefore
at-least-once: a crash between "sent" and "recorded" can repeat a batch.

Swap point: everything else calls only enqueue/claim/finish/fail/recover_stale/
drop_pending_for. A Redis-backed queue can replace this module alone.
"""

from datetime import timedelta

from django.db import connection, models, transaction
from django.utils import timezone

from .models import Job

EXPAND = "expand"
SEND_BATCH = "send_batch"


class DbClock(models.Func):
    """The database's wall clock. All run_at times are written and compared in
    database time, so clock skew between web/worker hosts and Postgres cannot
    delay or early-fire a job. (`now()` would be frozen at transaction start.)"""

    template = "clock_timestamp()"
    output_field = models.DateTimeField()


def _db_time(delay_seconds: float = 0):
    if not delay_seconds:
        return DbClock()
    return models.ExpressionWrapper(DbClock() + models.Value(timedelta(seconds=delay_seconds)),
                                    output_field=models.DateTimeField())


def enqueue(kind: str, payload: dict, run_at=None, delay_seconds: float = 0) -> Job:
    """run_at: an explicit time (a scheduled send). Otherwise now + delay_seconds, in DB time."""
    return Job.objects.create(kind=kind, payload=payload, run_at=run_at or _db_time(delay_seconds))


def claim(limit: int) -> list[Job]:
    with transaction.atomic():
        with connection.cursor() as cur:
            cur.execute(
                """UPDATE dispatch_app_job
                   SET status = 'running', locked_at = clock_timestamp(), attempts = attempts + 1
                   WHERE id IN (
                       SELECT id FROM dispatch_app_job
                       WHERE status = 'pending' AND run_at <= clock_timestamp()
                       ORDER BY run_at
                       LIMIT %s
                       FOR UPDATE SKIP LOCKED)
                   RETURNING id""",
                [limit],
            )
            ids = [row[0] for row in cur.fetchall()]
    return list(Job.objects.filter(id__in=ids).order_by("run_at"))


def finish(job: Job) -> None:
    Job.objects.filter(id=job.id).delete()


def fail(job: Job, error: str, retry: bool) -> None:
    if retry:
        Job.objects.filter(id=job.id).update(
            status=Job.PENDING, locked_at=None, last_error=error[:4000],
            run_at=_db_time(min(300, 5 * (2 ** job.attempts))),
        )
    else:
        Job.objects.filter(id=job.id).update(status=Job.FAILED, last_error=error[:4000])


def recover_stale(minutes: int) -> int:
    cutoff = models.ExpressionWrapper(DbClock() - models.Value(timedelta(minutes=minutes)),
                                      output_field=models.DateTimeField())
    return Job.objects.filter(status=Job.RUNNING, locked_at__lt=cutoff).update(status=Job.PENDING, locked_at=None)


def drop_pending_for(notification_id: str) -> int:
    deleted, _ = Job.objects.filter(status=Job.PENDING, payload__notification_id=notification_id).delete()
    return deleted


def depth() -> dict:
    """Queue health for /healthz-style checks and the admin."""
    now = timezone.now()
    pending = Job.objects.filter(status=Job.PENDING, run_at__lte=now)
    oldest = pending.order_by("run_at").values_list("run_at", flat=True).first()
    return {
        "ready": pending.count(),
        "running": Job.objects.filter(status=Job.RUNNING).count(),
        "failed": Job.objects.filter(status=Job.FAILED).count(),
        "oldest_ready_seconds": int((now - oldest).total_seconds()) if oldest else 0,
    }
