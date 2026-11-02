"""REST API views (the "Controller" for JSON clients).

Thin by design: validation lives in serializers, business logic in
``missions.simulation`` and spatial SQL in ``missions.spatial``.
"""
import json

from django.db.models import Count, Q
from django.contrib.gis.geos import Polygon
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework_gis.pagination import GeoJsonPagination

from missions import spatial
from missions.models import Run, Scenario
from missions.simulation import persistence

from .serializers import (
    RunSerializer, ScenarioCreateSerializer, ScenarioSerializer,
    ScenarioUpdateSerializer, SimulateSerializer,
)


class Pagination(GeoJsonPagination):
    """Pages of up to 100 features (client can ask for up to 500); output stays valid GeoJSON."""

    page_size = 100
    page_size_query_param = "page_size"
    max_page_size = 500


# Query parameters shared by the spatial endpoints (only used to describe them in the generated API docs).
LNG = OpenApiParameter("lng", OpenApiTypes.FLOAT, required=True, description="Longitude, -180 to 180")
LAT = OpenApiParameter("lat", OpenApiTypes.FLOAT, required=True, description="Latitude, -90 to 90")


def _float_param(request, name, *, required=True, default=None, minimum=None, maximum=None):
    """Parse a numeric query parameter, raising a 400 with a clear message."""
    raw = request.query_params.get(name)
    if raw in (None, ""):
        if required and default is None:
            raise ValidationError({name: "This query parameter is required."})
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ValidationError({name: f"'{raw}' is not a number."})
    if minimum is not None and value < minimum or maximum is not None and value > maximum:
        raise ValidationError({name: f"Must be between {minimum} and {maximum}."})
    return value


def _lng_lat(request):
    """Read and range-check the ``lng`` and ``lat`` query parameters shared by the spatial endpoints."""
    return (
        _float_param(request, "lng", minimum=-180, maximum=180),
        _float_param(request, "lat", minimum=-90, maximum=90),
    )


class IsOwnerOrStaff(permissions.BasePermission):
    """Unsafe methods on an object are limited to its owner (or staff)."""

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
        owner = obj.owner if isinstance(obj, Scenario) else (obj.created_by or obj.scenario.owner)
        return bool(request.user.is_staff or (owner and owner == request.user))


@extend_schema_view(
    list=extend_schema(parameters=[
        OpenApiParameter("type", OpenApiTypes.STR, description="earthquake, wildfire, flood or industrial"),
        OpenApiParameter("seed", OpenApiTypes.INT, description="Exact seed"),
        OpenApiParameter("q", OpenApiTypes.STR, description="Name contains (case-insensitive)"),
        OpenApiParameter("bbox", OpenApiTypes.STR, description="minLng,minLat,maxLng,maxLat (ST_Intersects with the grid)"),
    ]),
    create=extend_schema(request=ScenarioCreateSerializer, responses={201: ScenarioSerializer}),
    partial_update=extend_schema(request=ScenarioUpdateSerializer, responses=ScenarioSerializer),
)
class ScenarioViewSet(viewsets.ModelViewSet):
    """CRUD for scenarios plus spatial analysis actions."""

    permission_classes = [permissions.IsAuthenticatedOrReadOnly, IsOwnerOrStaff]
    pagination_class = Pagination
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        """Scenarios with run/obstacle/hazard counts added, narrowed by the optional URL filters.

        Filters: ``type``, ``seed``, ``q`` (name contains) and ``bbox`` (a PostGIS intersects test).
        """
        qs = Scenario.objects.select_related("owner").annotate(
            run_count=Count("runs", distinct=True),
            obstacle_count=Count("features", filter=Q(features__kind="obstacle"), distinct=True),
            hazard_count=Count("features", filter=Q(features__kind="hazard"), distinct=True),
        ).order_by("-created_at", "-id")  # explicit: aggregation drops Meta.ordering
        params = self.request.query_params
        if params.get("type"):
            qs = qs.filter(scenario_type=params["type"])
        if params.get("seed", "").isdigit():
            qs = qs.filter(seed=int(params["seed"]))
        if params.get("q"):
            qs = qs.filter(name__icontains=params["q"])
        if params.get("bbox"):  # bbox=minLng,minLat,maxLng,maxLat -> ST_Intersects with the grid extent
            try:
                min_x, min_y, max_x, max_y = (float(v) for v in params["bbox"].split(","))
            except ValueError:
                raise ValidationError({"bbox": "Use bbox=minLng,minLat,maxLng,maxLat."})
            qs = qs.filter(extent__intersects=Polygon.from_bbox((min_x, min_y, max_x, max_y)))
        return qs

    def get_serializer_class(self):
        """Pick the serializer per action: write models for create and update, the GeoJSON one for reads."""
        if self.action == "create":
            return ScenarioCreateSerializer
        if self.action == "partial_update":
            return ScenarioUpdateSerializer
        return ScenarioSerializer

    def create(self, request, *args, **kwargs):
        serializer = ScenarioCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        scenario = serializer.save()
        read = ScenarioSerializer(self.get_queryset().get(pk=scenario.pk), context={"request": request})
        return Response(read.data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = ScenarioUpdateSerializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        read = ScenarioSerializer(self.get_queryset().get(pk=instance.pk), context={"request": request})
        return Response(read.data)

    # ---- Q5: scenarios near a point ------------------------------------------
    @extend_schema(
        parameters=[LNG, LAT, OpenApiParameter("radius_km", OpenApiTypes.FLOAT, description="Search radius in km (default 25)")],
        responses=OpenApiTypes.OBJECT,
    )
    @action(detail=False, methods=["get"])
    def nearby(self, request):
        """``GET /api/scenarios/nearby/?lng=&lat=&radius_km=25`` - scenarios whose base is within the radius."""
        lng, lat = _lng_lat(request)
        radius_km = _float_param(request, "radius_km", required=False, default=25.0, minimum=0.01, maximum=20000)
        return Response(spatial.scenarios_near(lng, lat, radius_km * 1000))

    # ---- map layers ------------------------------------------------------------
    @extend_schema(responses=OpenApiTypes.OBJECT)
    @action(detail=True, methods=["get"])
    def layers(self, request, pk=None):
        """All geometry needed to draw a scenario: grid, base, target, obstacles, hazards, coverage zone."""
        scenario = self.get_object()
        collections = {"obstacle": [], "hazard": []}
        for f in scenario.features.all():
            collections[f.kind].append({
                "type": "Feature", "geometry": json.loads(f.geom.geojson),
                "properties": {"id": f.id, "kind": f.kind, "cell_x": f.cell_x, "cell_y": f.cell_y,
                               "cost_multiplier": f.cost_multiplier},
            })
        return Response({
            "scenario": {
                "id": scenario.id, "name": scenario.name, "scenario_type": scenario.scenario_type,
                "seed": scenario.seed, "grid_size": scenario.grid_size, "cell_size_m": scenario.cell_size_m,
                "initial_battery": scenario.initial_battery, "comm_radius_cells": scenario.comm_radius_cells,
                "max_steps": scenario.max_steps, "description": scenario.description,
            },
            "extent": json.loads(scenario.extent.geojson),
            "base": json.loads(scenario.base.geojson),
            "target": json.loads(scenario.target.geojson),
            "obstacles": {"type": "FeatureCollection", "features": collections["obstacle"]},
            "hazards": {"type": "FeatureCollection", "features": collections["hazard"]},
            "coverage": spatial.coverage_zone(scenario.id),
        })

    # ---- Q1: nearest features ----------------------------------------------------
    @extend_schema(
        parameters=[LNG, LAT,
                    OpenApiParameter("kind", OpenApiTypes.STR, enum=["obstacle", "hazard"], description="Only this kind"),
                    OpenApiParameter("limit", OpenApiTypes.INT, description="How many to return (1-25, default 3)")],
        responses=OpenApiTypes.OBJECT,
    )
    @action(detail=True, methods=["get"])
    def nearest(self, request, pk=None):
        """``GET /api/scenarios/{id}/nearest/?lng=&lat=&kind=hazard&limit=3``"""
        scenario = self.get_object()
        lng, lat = _lng_lat(request)
        kind = request.query_params.get("kind") or None
        if kind not in (None, "obstacle", "hazard"):
            raise ValidationError({"kind": "Must be 'obstacle' or 'hazard'."})
        limit = int(_float_param(request, "limit", required=False, default=3, minimum=1, maximum=25))
        return Response(spatial.nearest_features(scenario.id, lng, lat, kind, limit))

    # ---- Q2 + Q3: per-run spatial metrics ----------------------------------------------
    @extend_schema(responses=OpenApiTypes.OBJECT)
    @action(detail=True, methods=["get"])
    def metrics(self, request, pk=None):
        """Path length, hazard exposure and communication uptime for every run (computed in PostGIS)."""
        scenario = self.get_object()
        return Response(spatial.run_metrics(scenario.id))

    # ---- replay data ------------------------------------------------------------------
    @extend_schema(responses=OpenApiTypes.OBJECT)
    @action(detail=True, methods=["get"])
    def replay(self, request, pk=None):
        """Compact per-step positions for every run, for the map's replay slider."""
        scenario = self.get_object()
        out = []
        for run in scenario.runs.all():
            positions = [
                [round(s.position.x, 7), round(s.position.y, 7), s.battery, s.cell_x, s.cell_y]
                for s in run.step_set.all()
            ]
            out.append({"run_id": run.id, "agent": run.agent, "success": run.success,
                        "outcome": run.outcome, "positions": positions})
        return Response(out)

    # ---- run built-in baselines -----------------------------------------------------------
    @extend_schema(request=SimulateSerializer, responses={201: RunSerializer(many=True)})
    @action(detail=True, methods=["post"], permission_classes=[permissions.IsAuthenticated, IsOwnerOrStaff])
    def simulate(self, request, pk=None):
        """``POST /api/scenarios/{id}/simulate/`` - run the A* rover on this scenario."""
        scenario = self.get_object()
        serializer = SimulateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if data["replace"]:
            scenario.runs.filter(source=Run.Source.BUILTIN, agent__in=data["agents"]).delete()
        runs = persistence.simulate_builtin(
            scenario, agents=data["agents"], user=request.user, episode_seed=data.get("episode_seed"),
        )
        return Response(RunSerializer(runs, many=True).data, status=status.HTTP_201_CREATED)


@extend_schema_view(
    list=extend_schema(parameters=[
        OpenApiParameter("scenario", OpenApiTypes.INT, description="Only runs of this scenario id"),
        OpenApiParameter("agent", OpenApiTypes.STR, description="Only runs by this agent (astar)"),
    ]),
)
class RunViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin, viewsets.GenericViewSet,
):
    """List, retrieve and delete runs.  Runs are created by the built-in agents (POST .../simulate/), not directly."""

    permission_classes = [permissions.IsAuthenticatedOrReadOnly, IsOwnerOrStaff]
    pagination_class = Pagination
    serializer_class = RunSerializer

    def get_queryset(self):
        """Runs, optionally narrowed with ``?scenario=<id>`` and ``?agent=<name>``."""
        qs = Run.objects.select_related("scenario", "scenario__owner", "created_by")
        params = self.request.query_params
        if params.get("scenario", "").isdigit():
            qs = qs.filter(scenario_id=int(params["scenario"]))
        if params.get("agent"):
            qs = qs.filter(agent=params["agent"].lower())
        return qs

    # ---- Q4: return range ------------------------------------------------------------------------
    @extend_schema(
        parameters=[OpenApiParameter("cost_per_step", OpenApiTypes.FLOAT, description="Battery cost per cell, 0.5-5 (default 1.5)")],
        responses=OpenApiTypes.OBJECT,
    )
    @action(detail=True, methods=["get"], url_path="return-range")
    def return_range(self, request, pk=None):
        """Per-step return-range analysis plus the first point of no return."""
        run = self.get_object()
        cost = _float_param(request, "cost_per_step", required=False, default=1.5, minimum=0.5, maximum=5)
        rows = spatial.return_range(run.id, cost)
        return Response({
            "run_id": run.id, "cost_per_step": cost,
            "point_of_no_return": spatial.first_point_of_no_return(rows),
            "steps": rows,
        })
