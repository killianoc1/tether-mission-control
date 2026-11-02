"""Load demo scenarios (and baseline runs) so the app works out of the box."""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from missions.models import Scenario
from missions.simulation import persistence

# (name, type, seed, lng, lat, extra kwargs).  Locations are public places in
# Ireland; the grid is only a visual overlay, not a real hazard model.
DEMO_SCENARIOS = [
    ("Glendalough valley - earthquake drill", "earthquake", 11, -6.3290, 53.0106, {}),
    ("Powerscourt - wildfire front", "wildfire", 23, -6.1860, 53.1860, {}),
    ("Blessington Lakes - flood response", "flood", 37, -6.5410, 53.1560, {}),
    ("Dublin Port - chemical spill", "industrial", 41, -6.2110, 53.3470, {}),
    ("Maynooth - flood", "flood", 2, -6.5918, 53.3811,
     {"initial_battery": 50.0, "description": "A hazard-heavy map with a small battery. A* has to route "
      "around hazard cells (they cost extra battery) to get the rover home. "
      "This is the kind of energy trade-off a good route planner has to get right."}),
]


class Command(BaseCommand):
    help = "Create demo scenarios and run the A* rover on each."

    def add_arguments(self, parser):
        parser.add_argument("--username", default=None, help="Owner for the demo data (optional).")
        parser.add_argument("--force", action="store_true", help="Seed even if scenarios already exist.")

    def handle(self, *args, **options):
        if Scenario.objects.exists() and not options["force"]:
            self.stdout.write("Scenarios already exist - skipping (use --force to add anyway).")
            return
        owner = None
        if options["username"]:
            owner = get_user_model().objects.filter(username=options["username"]).first()

        for name, kind, seed, lng, lat, extra in DEMO_SCENARIOS:
            scenario = persistence.create_scenario(
                name=name, scenario_type=kind, seed=seed, lng=lng, lat=lat, owner=owner,
                description=extra.pop("description", f"Demo {kind} scenario (seed {seed})."), **extra,
            )
            runs = persistence.simulate_builtin(scenario, user=owner)
            summary = ", ".join(f"{r.agent}:{r.outcome}" for r in runs)
            self.stdout.write(self.style.SUCCESS(f"Created '{name}' -> {summary}"))
