"""First-run bootstrap used by the container entrypoint (safe to run repeatedly)."""
import os

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create the demo user from DEMO_USERNAME/DEMO_PASSWORD and optionally seed demo data."

    def handle(self, *args, **options):
        username = os.environ.get("DEMO_USERNAME", "demo")
        password = os.environ.get("DEMO_PASSWORD", "")
        User = get_user_model()
        user = None
        # Only create the demo account if a real password was set (never ship the placeholder).
        if password and password != "change-me-demo":
            user, created = User.objects.get_or_create(username=username, defaults={"is_staff": False})
            if created:
                user.set_password(password)
                user.save()
                self.stdout.write(self.style.SUCCESS(f"Created demo user '{username}'."))
        else:
            self.stdout.write("DEMO_PASSWORD not set (or still the placeholder) - no demo user created.")

        # Seed the five demo scenarios (skipped by seed_demo if scenarios already exist),
        # owned by the demo user so they can edit, re-run and delete them.
        if os.environ.get("SEED_DEMO", "0") == "1":
            call_command("seed_demo", username=user.username if user else None)
