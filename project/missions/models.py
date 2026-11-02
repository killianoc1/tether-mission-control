"""Spatial data model (the "Model" in MVC).

Geometry types used (all SRID 4326 / WGS 84):

* ``Scenario.base`` / ``Scenario.target``  -> Point
* ``Scenario.extent``                       -> Polygon  (the georeferenced grid)
* ``ScenarioFeature.geom``                  -> Polygon  (one square per obstacle/hazard cell)
* ``Run.trajectory``                        -> LineString
* ``RunStep.position``                      -> Point

Every geometry column has a GiST spatial index (GeoDjango's default,
``spatial_index=True``).  ``Scenario.base`` additionally gets a *geography*
functional index in migration 0002, used by the proximity search.
"""
from django.conf import settings
from django.contrib.gis.db import models
from django.core.validators import MaxValueValidator, MinValueValidator


class Scenario(models.Model):
    """A georeferenced disaster-response scenario on a square grid of cells."""

    class Type(models.TextChoices):
        EARTHQUAKE = "earthquake", "Earthquake"
        WILDFIRE = "wildfire", "Wildfire"
        FLOOD = "flood", "Flood"
        INDUSTRIAL = "industrial", "Industrial accident"

    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    scenario_type = models.CharField(max_length=20, choices=Type.choices, default=Type.EARTHQUAKE)

    # Reproducibility: (seed, scenario_type, grid_size, initial_battery) always
    # regenerates the same grid.
    seed = models.PositiveIntegerField()
    grid_size = models.PositiveSmallIntegerField(
        default=15, validators=[MinValueValidator(5), MaxValueValidator(40)]
    )
    cell_size_m = models.FloatField(
        default=25.0, validators=[MinValueValidator(5.0), MaxValueValidator(200.0)]
    )
    initial_battery = models.FloatField(
        default=120.0, validators=[MinValueValidator(20.0), MaxValueValidator(1000.0)]
    )
    comm_radius_cells = models.FloatField(
        default=8.0, validators=[MinValueValidator(1.0), MaxValueValidator(60.0)]
    )
    max_steps = models.PositiveIntegerField(
        default=250, validators=[MinValueValidator(10), MaxValueValidator(2000)]
    )

    # Where the grid sits on the Earth.  The grid itself is just cell numbers (x, y); these geometry
    # fields place it on the map.  base_x/base_y/target_x/target_y repeat the base and target as
    # cell numbers so the simulation can use them without reading any geometry.
    origin = models.PointField(srid=4326, help_text="South-west corner of the grid")
    base = models.PointField(srid=4326)
    target = models.PointField(srid=4326)
    extent = models.PolygonField(srid=4326)
    base_x = models.PositiveSmallIntegerField()
    base_y = models.PositiveSmallIntegerField()
    target_x = models.PositiveSmallIntegerField()
    target_y = models.PositiveSmallIntegerField()

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="scenarios",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "base_location"   # table name in PostgreSQL (Django would default to missions_scenario)
        ordering = ["-created_at", "-id"]
        # Database-level rules: bad data is rejected even if it bypasses Django's own validators.
        constraints = [
            models.CheckConstraint(
                condition=models.Q(grid_size__gte=5, grid_size__lte=40),
                name="scenario_grid_size_range",
            ),
            models.CheckConstraint(
                condition=models.Q(cell_size_m__gt=0), name="scenario_cell_size_positive"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.get_scenario_type_display()}, seed {self.seed})"


class ScenarioFeature(models.Model):
    """One grid cell that is an obstacle (impassable) or hazard (passable, costly)."""

    class Kind(models.TextChoices):
        OBSTACLE = "obstacle", "Obstacle"
        HAZARD = "hazard", "Hazard"

    scenario = models.ForeignKey(Scenario, on_delete=models.CASCADE, related_name="features")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    cell_x = models.PositiveSmallIntegerField()
    cell_y = models.PositiveSmallIntegerField()
    # The cell drawn as a square polygon on the map; also what Q1 (nearest) and Q2 (intersection) query.
    geom = models.PolygonField(srid=4326)
    cost_multiplier = models.FloatField(default=1.0)

    class Meta:
        db_table = "rover_hazards"   # holds both hazard and obstacle cells (the "kind" column says which)
        ordering = ["scenario_id", "kind", "cell_y", "cell_x"]
        constraints = [
            models.UniqueConstraint(
                fields=["scenario", "cell_x", "cell_y"], name="feature_unique_cell"
            ),
        ]
        indexes = [models.Index(fields=["scenario", "kind"], name="feature_scenario_kind_idx")]

    def __str__(self) -> str:
        return f"{self.kind} ({self.cell_x},{self.cell_y})"


class Run(models.Model):
    """One episode of one agent on one scenario."""

    class Outcome(models.TextChoices):
        SUCCESS = "success", "Mission success"
        BATTERY_DEPLETED = "battery_depleted", "Battery depleted"
        TIMEOUT = "timeout", "Timed out"
        INCOMPLETE = "incomplete", "Incomplete"

    class Source(models.TextChoices):
        BUILTIN = "builtin", "Built-in baseline"
        IMPORTED = "imported", "Imported via API"  # reserved: the import endpoint was removed, only BUILTIN is used

    scenario = models.ForeignKey(Scenario, on_delete=models.CASCADE, related_name="runs")
    agent = models.CharField(max_length=40, help_text="e.g. random, greedy, astar, ppo")
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.BUILTIN)
    outcome = models.CharField(max_length=20, choices=Outcome.choices)
    # Convenience flags derived from the route; stored as columns so SQL can filter and count them.
    success = models.BooleanField(default=False)
    target_found = models.BooleanField(default=False)
    returned_to_base = models.BooleanField(default=False)
    steps = models.PositiveIntegerField()
    collisions = models.PositiveIntegerField(default=0)
    battery_remaining = models.FloatField()
    # The whole route as one LineString: used by ST_Length and ST_Intersection (Q2).
    # The individual points live in RunStep.
    trajectory = models.LineStringField(srid=4326)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="runs",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "rover_route"   # one row per route the rover drove (Django would default to missions_run)
        ordering = ["scenario_id", "id"]
        indexes = [models.Index(fields=["scenario", "agent"], name="run_scenario_agent_idx")]

    def __str__(self) -> str:
        return f"{self.agent} on scenario {self.scenario_id}: {self.outcome}"


class RunStep(models.Model):
    """The rover's position and battery after each step (step 0 = at base)."""

    run = models.ForeignKey(Run, on_delete=models.CASCADE, related_name="step_set")
    step_index = models.PositiveIntegerField()
    cell_x = models.PositiveSmallIntegerField()
    cell_y = models.PositiveSmallIntegerField()
    # One Point per step: used for the per-step queries, comm uptime (Q3) and return range (Q4).
    position = models.PointField(srid=4326)
    battery = models.FloatField()
    action = models.CharField(max_length=1, blank=True, help_text="N/S/E/W; blank for step 0")

    class Meta:
        ordering = ["run_id", "step_index"]
        constraints = [
            models.UniqueConstraint(fields=["run", "step_index"], name="runstep_unique_index"),
        ]

    def __str__(self) -> str:
        return f"run {self.run_id} step {self.step_index}"
