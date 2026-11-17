from django.db import models


class Job(models.Model):
    """A unit of dispatcher work, queued in Postgres. See src/dispatch_app/queue.py."""

    PENDING = "pending"
    RUNNING = "running"
    FAILED = "failed"
    STATUSES = [(PENDING, "Pending"), (RUNNING, "Running"), (FAILED, "Failed")]

    id = models.BigAutoField(primary_key=True)
    kind = models.CharField(max_length=20)
    payload = models.JSONField()
    run_at = models.DateTimeField()
    status = models.CharField(max_length=8, choices=STATUSES, default=PENDING)
    attempts = models.IntegerField(default=0)
    locked_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["run_at"], name="job_pending_idx", condition=models.Q(status="pending")),
            models.Index(fields=["locked_at"], name="job_running_idx", condition=models.Q(status="running")),
        ]

    def __str__(self):
        return f"{self.kind}#{self.id} [{self.status}]"
