"""App configuration for ``missions``, the single Django app in this project.

Registered in ``INSTALLED_APPS`` (config/settings.py) so Django finds its models,
migrations, templates and management commands.
"""
from django.apps import AppConfig


class MissionsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "missions"
    verbose_name = "TETHER missions"
