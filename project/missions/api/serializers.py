"""Serializers: validation on the way in, GeoJSON on the way out."""
from django.db import transaction
from rest_framework import serializers
from rest_framework_gis.serializers import GeoFeatureModelSerializer

from missions.models import Run, Scenario
from missions.simulation import persistence
from missions.simulation.agents import AGENTS


class ScenarioSerializer(GeoFeatureModelSerializer):
    """Read serializer: a GeoJSON Feature whose geometry is the base station."""

    run_count = serializers.IntegerField(read_only=True, default=0)
    obstacle_count = serializers.IntegerField(read_only=True, default=0)
    hazard_count = serializers.IntegerField(read_only=True, default=0)
    owner = serializers.StringRelatedField()
    scenario_type_label = serializers.CharField(source="get_scenario_type_display", read_only=True)

    class Meta:
        model = Scenario
        geo_field = "base"
        fields = (
            "id", "name", "description", "scenario_type", "scenario_type_label", "seed",
            "grid_size", "cell_size_m", "initial_battery", "comm_radius_cells", "max_steps",
            "base_x", "base_y", "target_x", "target_y", "owner", "created_at",
            "run_count", "obstacle_count", "hazard_count",
        )


class ScenarioCreateSerializer(serializers.Serializer):
    """Write serializer: generates a seeded world centred on (lng, lat)."""

    name = serializers.CharField(max_length=120)
    description = serializers.CharField(required=False, allow_blank=True, max_length=2000, default="")
    scenario_type = serializers.ChoiceField(choices=Scenario.Type.choices)
    seed = serializers.IntegerField(min_value=0, max_value=2**31 - 1, required=False)
    lng = serializers.FloatField(min_value=-180, max_value=180)
    lat = serializers.FloatField(min_value=-85, max_value=85)
    grid_size = serializers.IntegerField(min_value=5, max_value=40, default=15)
    cell_size_m = serializers.FloatField(min_value=5, max_value=200, default=25.0)
    initial_battery = serializers.FloatField(min_value=20, max_value=1000, default=120.0)
    comm_radius_cells = serializers.FloatField(min_value=1, max_value=60, default=8.0)
    max_steps = serializers.IntegerField(min_value=10, max_value=2000, default=250)
    run_baselines = serializers.BooleanField(default=True, write_only=True)

    def validate_name(self, value):
        """Field-level validation (runs automatically for the ``name`` field): reject blank names."""
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Name cannot be blank.")
        return value

    def create(self, validated_data):
        """Generate the scenario (and optionally run the baselines) in one database transaction.

        If the settings are infeasible, ``generate_world`` raises ``ValueError``; it is turned into
        a 400 response, and the transaction rolls back so no half-built scenario is saved.
        """
        run_baselines = validated_data.pop("run_baselines")
        validated_data.setdefault("seed", persistence.random_seed())
        user = self.context["request"].user
        try:
            with transaction.atomic():
                scenario = persistence.create_scenario(owner=user, **validated_data)
                if run_baselines:
                    persistence.simulate_builtin(scenario, user=user)
        except ValueError as exc:  # infeasible settings
            raise serializers.ValidationError({"non_field_errors": [str(exc)]})
        return scenario


class ScenarioUpdateSerializer(serializers.ModelSerializer):
    """Only descriptive fields may change - geometry is derived from the seed."""

    class Meta:
        model = Scenario
        fields = ("name", "description")


class RunSerializer(GeoFeatureModelSerializer):
    """Read serializer: a GeoJSON Feature whose geometry is the trajectory LineString."""

    agent_label = serializers.SerializerMethodField()

    class Meta:
        model = Run
        geo_field = "trajectory"
        fields = (
            "id", "scenario", "agent", "agent_label", "source", "outcome", "success",
            "target_found", "returned_to_base", "steps", "collisions", "battery_remaining",
            "created_at",
        )

    def get_agent_label(self, obj) -> str:
        """Display name: A* in capitals, others capitalised."""
        return obj.agent.upper() if obj.agent == "astar" else obj.agent.capitalize()


class SimulateSerializer(serializers.Serializer):
    """Options for POST /api/scenarios/{id}/simulate/: which agents to run and whether to replace old runs."""

    agents = serializers.ListField(
        child=serializers.ChoiceField(choices=AGENTS), allow_empty=False, default=list(AGENTS),
    )
    episode_seed = serializers.IntegerField(min_value=0, max_value=2**31 - 1, required=False)
    replace = serializers.BooleanField(default=False)
