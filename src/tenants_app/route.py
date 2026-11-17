from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .controller import AppViewSet, CredentialViewSet, TenantViewSet

router = DefaultRouter(trailing_slash=False)
router.include_root_view = False
router.register(r"tenants", TenantViewSet, basename="admin-tenant")
router.register(r"credentials", CredentialViewSet, basename="admin-credential")
router.register(r"apps", AppViewSet, basename="admin-app")

urlpatterns = [path("", include(router.urls))]
