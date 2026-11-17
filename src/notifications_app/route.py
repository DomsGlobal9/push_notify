from django.urls import path

from .controller import EventView, NotificationCancelView, NotificationDetailView, NotificationListView

urlpatterns = [
    path("notifications", NotificationListView.as_view()),
    path("notifications/<uuid:notification_id>", NotificationDetailView.as_view()),
    path("notifications/<uuid:notification_id>/cancel", NotificationCancelView.as_view()),
    path("events", EventView.as_view()),
]
