"""Create the /admin/ superuser from env on deploy, without ever clobbering an existing one.

Reads ADMIN_USERNAME and ADMIN_PASSWORD.
  * Either unset          -> nothing happens (the normal state once an admin exists).
  * User does not exist   -> created as a superuser with that password.
  * User already exists   -> left alone, so a password changed in /admin/ survives
                             every deploy. Set ADMIN_RESET_PASSWORD=true for one deploy
                             to force the env password (e.g. a forgotten password).
"""

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create the admin superuser from ADMIN_USERNAME / ADMIN_PASSWORD if it does not exist."

    def handle(self, *args, **options):
        username = os.environ.get("ADMIN_USERNAME")
        password = os.environ.get("ADMIN_PASSWORD")
        if not (username and password):
            self.stdout.write("ADMIN_USERNAME/ADMIN_PASSWORD not set; skipping.")
            return
        User = get_user_model()
        user = User.objects.filter(username=username).first()
        if user is None:
            User.objects.create_superuser(username=username, email="", password=password)
            self.stdout.write(self.style.SUCCESS(f"Created admin user {username}"))
            return
        if os.environ.get("ADMIN_RESET_PASSWORD", "").lower() in ("1", "true", "yes"):
            user.is_staff = user.is_superuser = True
            user.set_password(password)
            user.save()
            self.stdout.write(self.style.WARNING(f"Reset password for {username} (ADMIN_RESET_PASSWORD)"))
            return
        self.stdout.write(f"Admin user {username} exists; leaving it unchanged.")
