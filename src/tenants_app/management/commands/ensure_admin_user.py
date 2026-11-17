"""Create or reset the /admin/ superuser from env on every deploy (CRM-B's create_superuser.py idea).

Reads ADMIN_USERNAME and ADMIN_PASSWORD. Does nothing when either is unset, so a
deploy never fails for want of them.
"""

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create or update the admin superuser from ADMIN_USERNAME / ADMIN_PASSWORD."

    def handle(self, *args, **options):
        username = os.environ.get("ADMIN_USERNAME")
        password = os.environ.get("ADMIN_PASSWORD")
        if not (username and password):
            self.stdout.write("ADMIN_USERNAME/ADMIN_PASSWORD not set; skipping.")
            return
        user, created = get_user_model().objects.get_or_create(username=username)
        user.is_staff = user.is_superuser = True
        user.set_password(password)
        user.save()
        self.stdout.write(self.style.SUCCESS(f"{'Created' if created else 'Updated'} admin user {username}"))
