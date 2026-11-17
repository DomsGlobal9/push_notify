"""The operator dashboard: Django admin with a real home page.

The index shows what an operator checks first: are pushes going out, how many
land, is the queue healthy, which product is sending what. Everything else
(tenants, credentials, devices, jobs) stays on the standard admin pages.
"""

from datetime import timedelta

from django.contrib import admin
from django.db.models import Count, Max, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone


class GatewayAdminSite(admin.AdminSite):
    site_header = "Push Gateway"
    site_title = "Push Gateway"
    index_title = "Overview"
    index_template = "admin/gateway_index.html"

    def index(self, request, extra_context=None):
        return super().index(request, {**self._stats(), **(extra_context or {})})

    @staticmethod
    def _stats():
        from src.devices_app.models import Device
        from src.dispatch_app import queue
        from src.notifications_app.models import Notification
        from src.tenants_app.models import Tenant

        now = timezone.now()
        day_ago = now - timedelta(hours=24)

        devices = Device.objects.filter(is_active=True).aggregate(
            total=Count("id"),
            android=Count("id", filter=Q(app__platform="android")),
            ios=Count("id", filter=Q(app__platform="ios")),
            users=Count("user_external_id", distinct=True),
        )

        last_day = Notification.objects.filter(created_at__gte=day_ago).aggregate(
            count=Count("id"), sent=Sum("sent_count"), failed=Sum("failed_count"),
            invalid=Sum("invalid_token_count"), opened=Sum("opened_count"),
        )
        sent = last_day["sent"] or 0
        attempted = sent + (last_day["failed"] or 0) + (last_day["invalid"] or 0)
        delivery_rate = round(100 * sent / attempted, 1) if attempted else None
        open_rate = round(100 * (last_day["opened"] or 0) / sent, 1) if sent else None

        # 7-day chart, oldest first, every day present even when empty.
        rows = {
            r["day"]: r for r in Notification.objects.filter(created_at__gte=now - timedelta(days=7))
            .annotate(day=TruncDate("created_at")).values("day")
            .annotate(sent=Sum("sent_count"), failed=Sum("failed_count"), invalid=Sum("invalid_token_count"))
        }
        days = []
        for i in range(6, -1, -1):
            d = (now - timedelta(days=i)).date()
            r = rows.get(d, {})
            days.append({"label": d.strftime("%a"), "date": d.isoformat(), "sent": r.get("sent") or 0,
                         "failed": (r.get("failed") or 0) + (r.get("invalid") or 0)})
        peak = max([d["sent"] + d["failed"] for d in days] + [1])
        for d in days:
            d["sent_pct"] = round(100 * d["sent"] / peak, 1)
            d["failed_pct"] = round(100 * d["failed"] / peak, 1)

        recent = list(Notification.objects.select_related("tenant").order_by("-created_at")[:8])
        for n in recent:
            n.progress = round(100 * (n.sent_count + n.failed_count + n.invalid_token_count + n.skipped_count)
                               / n.targeted_count) if n.targeted_count else (100 if n.status == "completed" else 0)
            n.title_text = (n.message or {}).get("title") or (n.message or {}).get("body") or "(data only)"

        tenants = Tenant.objects.annotate(
            app_count=Count("apps", distinct=True),
            active_devices=Count("devices", filter=Q(devices__is_active=True), distinct=True),
            last_send=Max("notifications__created_at"),
        ).order_by("name")

        q = queue.depth()
        queue_state = "ok"
        if q["failed"] or q["oldest_ready_seconds"] > 300:
            queue_state = "bad"
        elif q["oldest_ready_seconds"] > 30:
            queue_state = "warn"

        return {
            "devices": devices, "last_day": last_day, "sent_24h": sent, "delivery_rate": delivery_rate,
            "open_rate": open_rate, "days": days, "recent": recent, "tenants": tenants,
            "queue": q, "queue_state": queue_state,
        }
