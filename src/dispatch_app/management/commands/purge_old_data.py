from django.core.management.base import BaseCommand

from src.dispatch_app.dispatcher import purge_old_data


class Command(BaseCommand):
    help = "Delete delivery failures, finished notifications and dead jobs past their retention."

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS(f"Deleted {purge_old_data()}"))
