import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from src.tenants_app import services
from src.tenants_app.models import Credential


class Command(BaseCommand):
    help = (
        "Store an FCM service account or an APNs .p8 key, encrypted.\n"
        "  add_credential firebase-main --fcm-json service-account.json\n"
        "  add_credential apple-team --apns-p8 AuthKey_ABC123.p8 --team-id TEAMID --key-id ABC123"
    )

    def add_arguments(self, parser):
        parser.add_argument("name")
        parser.add_argument("--fcm-json", help="Path to the Firebase service account JSON")
        parser.add_argument("--apns-p8", help="Path to AuthKey_XXXX.p8")
        parser.add_argument("--team-id")
        parser.add_argument("--key-id")
        parser.add_argument("--replace", action="store_true", help="Replace the secret of an existing credential")

    def handle(self, *args, name, fcm_json, apns_p8, team_id, key_id, replace, **options):
        if bool(fcm_json) == bool(apns_p8):
            raise CommandError("Pass exactly one of --fcm-json or --apns-p8.")
        if fcm_json:
            provider, secret = Credential.FCM, json.loads(Path(fcm_json).read_text())
        else:
            provider = Credential.APNS
            secret = {"team_id": team_id or "", "key_id": key_id or "", "p8": Path(apns_p8).read_text()}
        existing = Credential.objects.filter(name=name).first()
        if existing and not replace:
            raise CommandError(f"Credential {name!r} exists; pass --replace to change its secret.")
        try:
            cred = services.save_credential(name, provider, secret, existing)
        except services.OnboardingError as exc:
            raise CommandError(str(exc))
        self.stdout.write(self.style.SUCCESS(f"Saved {cred} {cred.public_summary()} id={cred.id}"))
