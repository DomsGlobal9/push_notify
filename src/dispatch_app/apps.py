from django.apps import AppConfig
from django.db.models.signals import post_migrate


class DispatchAppConfig(AppConfig):
    name = "src.dispatch_app"
    label = "dispatch_app"
    verbose_name = "Queue"

    def ready(self):
        from src.core.supabase import on_post_migrate

        post_migrate.connect(on_post_migrate, sender=self, dispatch_uid="push_gateway_supabase_lockdown")
