from django.contrib import admin
from django.db import connection
from django.http import JsonResponse
from django.urls import include, path
from django.views.generic import RedirectView


def healthz(_request):
    """Render's health check. Fails if the database is unreachable."""
    with connection.cursor() as cur:
        cur.execute("SELECT 1")
    return JsonResponse({"ok": True})


urlpatterns = [
    path("", RedirectView.as_view(url="/admin/", permanent=False)),
    path("healthz", healthz),
    path("admin/", admin.site.urls),
    path("admin-api/", include("src.tenants_app.route")),
    path("v1/", include("src.devices_app.route")),
    path("v1/", include("src.notifications_app.route")),
]
