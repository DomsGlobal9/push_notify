from django.core.management.base import BaseCommand, CommandError

from src.tenants_app import services
from src.tenants_app.models import App, Credential, Tenant


class Command(BaseCommand):
    help = "Register an Android package / iOS bundle id under a tenant: add_app 'Example Shop' android com.example.shop firebase-main"

    def add_arguments(self, parser):
        parser.add_argument("tenant")
        parser.add_argument("platform", choices=[App.ANDROID, App.IOS])
        parser.add_argument("bundle_id")
        parser.add_argument("credential")
        parser.add_argument("--channel", default="", help="Default Android notification channel id")

    def handle(self, *args, tenant, platform, bundle_id, credential, channel, **options):
        try:
            t = Tenant.objects.get(name=tenant)
            c = Credential.objects.get(name=credential)
            app = services.create_app(t, platform, bundle_id, c, channel)
        except Tenant.DoesNotExist:
            raise CommandError(f"No tenant named {tenant!r}")
        except Credential.DoesNotExist:
            raise CommandError(f"No credential named {credential!r}")
        except services.OnboardingError as exc:
            raise CommandError(str(exc))
        self.stdout.write(self.style.SUCCESS(f"Registered {app} for {t.name} via {c} (id={app.id})"))
