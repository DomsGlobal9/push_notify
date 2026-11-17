from django.urls import path

from .controller import (
    DeviceDetailView, DeviceLogoutView, DeviceRegisterView, DeviceTopicsView, UserDetailView, UserDevicesView,
)

urlpatterns = [
    path("devices", DeviceRegisterView.as_view()),
    path("devices/<str:installation_id>", DeviceDetailView.as_view()),
    path("devices/<str:installation_id>/logout", DeviceLogoutView.as_view()),
    path("devices/<str:installation_id>/topics", DeviceTopicsView.as_view()),
    path("users/<str:user_external_id>/devices", UserDevicesView.as_view()),
    path("users/<str:user_external_id>", UserDetailView.as_view()),
]
