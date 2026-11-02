"""Django admin registration: the built-in /admin/ site for inspecting and editing data.

Not part of the public app.  ``GISModelAdmin`` shows a small map widget for
geometry fields, which is handy for checking a scenario's base point or a
run's trajectory.  Needs a superuser (``manage.py createsuperuser``).
"""
from django.contrib import admin
from django.contrib.gis.admin import GISModelAdmin

from .models import Run, RunStep, Scenario, ScenarioFeature


@admin.register(Scenario)
class ScenarioAdmin(GISModelAdmin):
    list_display = ("name", "scenario_type", "seed", "grid_size", "owner", "created_at")
    list_filter = ("scenario_type",)
    search_fields = ("name",)


@admin.register(ScenarioFeature)
class ScenarioFeatureAdmin(GISModelAdmin):
    list_display = ("scenario", "kind", "cell_x", "cell_y")
    list_filter = ("kind",)


@admin.register(Run)
class RunAdmin(GISModelAdmin):
    list_display = ("id", "scenario", "agent", "outcome", "steps", "battery_remaining", "source")
    list_filter = ("agent", "outcome", "source")


@admin.register(RunStep)
class RunStepAdmin(admin.ModelAdmin):
    list_display = ("run", "step_index", "cell_x", "cell_y", "battery")
