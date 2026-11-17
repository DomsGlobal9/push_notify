import signal

from django.core.management.base import BaseCommand

from src.dispatch_app import dispatcher


class Command(BaseCommand):
    help = "Run the push dispatcher: claims queued jobs and sends them to FCM/APNs."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Drain the ready jobs once and exit.")

    def handle(self, *args, once=False, **options):
        if once:
            total = 0
            while n := dispatcher.run_once():
                total += n
            self.stdout.write(self.style.SUCCESS(f"Processed {total} jobs."))
            return
        loop = dispatcher.Dispatcher()
        signal.signal(signal.SIGTERM, loop.stop)
        signal.signal(signal.SIGINT, loop.stop)
        loop.run()
