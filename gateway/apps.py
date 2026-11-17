from django.contrib.admin.apps import AdminConfig


class GatewayAdminConfig(AdminConfig):
    default_site = "gateway.admin_site.GatewayAdminSite"
