from django.core.management.base import BaseCommand, CommandError

from src.tenants_app import services
from src.tenants_app.models import Tenant


class Command(BaseCommand):
    help = "Create a tenant (one of your products) and print its keys once."

    def add_arguments(self, parser):
        parser.add_argument("name")

    def handle(self, *args, name, **options):
        if Tenant.objects.filter(name=name).exists():
            raise CommandError(f"Tenant {name!r} already exists.")
        tenant, secrets = services.create_tenant(name)
        self.stdout.write(self.style.SUCCESS(f"Created tenant {tenant.name} ({tenant.id})"))
        self.stdout.write(f"  secret_key      (servers only): {secrets['secret_key']}")
        self.stdout.write(f"  publishable_key (ship in apps): {secrets['publishable_key']}")
        self.stdout.write(f"  identity_secret (servers only): {secrets['identity_secret']}")
        self.stdout.write(self.style.WARNING("Store these now. The keys are not shown again."))
