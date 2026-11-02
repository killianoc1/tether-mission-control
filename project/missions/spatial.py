"""PostGIS spatial queries.

Five queries, each one answering a real question about a mission.  They use raw
SQL because the PostGIS expressions (``geography`` casts, ``ST_Buffer``,
``ST_Intersection`` ...) are clearer to read and to document that way.  All
user-supplied values are passed as bound parameters - never string-formatted -
so they are safe from SQL injection.

Distances are in metres: geometries are stored as WGS 84 ``geometry`` and cast
to ``geography`` wherever a real-world distance or length is needed.

Index usage (see docs/SPATIAL_QUERIES.md for EXPLAIN output):
  Q1  KNN operator ``<->`` on the functional geography index ``feature_geom_geog_gist``
  Q2  ``ST_Intersection`` of a trajectory with the union of hazard polygons
  Q3  ``ST_DWithin`` against a buffered base point
  Q4  ``ST_Buffer`` / ``ST_DWithin`` for the "return range" of each step
  Q5  ``ST_DWithin`` on the functional geography index ``scenario_base_geog_gist``
"""
from __future__ import annotations

import json
from typing import Any

from django.db import connection


def _rows(cursor) -> list[dict[str, Any]]:
    cols = [c[0] for c in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def _q(sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return _rows(cursor)


# --------------------------------------------------------------------------- Q1
def nearest_features(scenario_id: int, lng: float, lat: float, kind: str | None = None, limit: int = 3):
    """Q1 - PROXIMITY: the ``limit`` obstacle/hazard cells closest to a clicked point.

    Ordered with the KNN operator ``<->`` on the *geography* type, so "nearest"
    is measured in metres.  (On plain ``geometry`` in WGS 84 the operator ranks by
    *degrees*, and at 53 degrees north a degree of longitude is ~40 % shorter than
    a degree of latitude - the ranking would be wrong.)  The functional GiST
    index ``feature_geom_geog_gist`` (migration 0002) serves this ordering.
    """
    return _q(
        """
        SELECT f.id, f.kind, f.cell_x, f.cell_y,
               -- spherical distance (use_spheroid = false) so the reported value matches the
               -- order produced by the KNN operator, which is also spherical (under half a percent from spheroid)
               ROUND(ST_Distance(f.geom::geography, pt.g, false)::numeric, 1) AS distance_m,
               ST_Y(ST_Centroid(f.geom)) AS lat, ST_X(ST_Centroid(f.geom)) AS lng
        FROM rover_hazards f
        CROSS JOIN (SELECT ST_SetSRID(ST_MakePoint(%(lng)s, %(lat)s), 4326)::geography AS g) pt
        WHERE f.scenario_id = %(sid)s
          AND (%(kind)s::text IS NULL OR f.kind = %(kind)s::text)
        ORDER BY f.geom::geography <-> pt.g, f.id
        LIMIT %(limit)s
        """,
        {"sid": scenario_id, "lng": lng, "lat": lat, "kind": kind, "limit": limit},
    )


# --------------------------------------------------------------- Q2 + Q3 (+ length)
def run_metrics(scenario_id: int):
    """Q2 + Q3 - INTERSECTION and CONTAINMENT metrics for every run of a scenario.

    * ``path_length_m``       - ``ST_Length`` of the trajectory (geography).
    * ``hazard_exposure_m``   - length of the trajectory that lies *inside* the union
                                of hazard polygons: ``ST_Intersection`` of a LineString
                                with a MultiPolygon, reduced to its line parts by
                                ``ST_CollectionExtract(..., 2)``.
    * ``comm_uptime_pct``     - share of steps whose position is within the
                                communication radius of the base (``ST_DWithin``).
    """
    return _q(
        """
        WITH sc AS (
            SELECT id, base, comm_radius_cells * cell_size_m AS comm_m
            FROM base_location WHERE id = %(sid)s
        ),
        hz AS (
            SELECT ST_Union(geom) AS g
            FROM rover_hazards
            WHERE scenario_id = %(sid)s AND kind = 'hazard'
        )
        SELECT r.id AS run_id, r.agent, r.outcome, r.success, r.target_found,
               r.returned_to_base, r.steps, r.collisions,
               ROUND(r.battery_remaining::numeric, 1) AS battery_remaining,
               ROUND(ST_Length(r.trajectory::geography)::numeric, 1) AS path_length_m,
               ROUND(COALESCE(
                   ST_Length(ST_CollectionExtract(ST_Intersection(r.trajectory, hz.g), 2)::geography), 0
               )::numeric, 1) AS hazard_exposure_m,
               ROUND((
                   SELECT 100.0 * COUNT(*) FILTER (
                              WHERE ST_DWithin(s.position::geography, sc.base::geography, sc.comm_m))
                          / NULLIF(COUNT(*), 0)
                   FROM missions_runstep s WHERE s.run_id = r.id
               )::numeric, 1) AS comm_uptime_pct
        FROM rover_route r
        CROSS JOIN sc
        LEFT JOIN hz ON TRUE
        WHERE r.scenario_id = %(sid)s
        ORDER BY r.id
        """,
        {"sid": scenario_id},
    )


# --------------------------------------------------------------------------- Q4
def return_range(run_id: int, cost_per_step: float = 1.5):
    """Q4 - BUFFERING: the "point of no return" for a run.

    For every step we ask: *could the rover still get home?*  The battery left
    buys at most ``battery / cost_per_step`` cells of travel, so we buffer the
    base by that many metres (``ST_Buffer`` on geography) and test whether the
    rover is inside it (``ST_Covers``).  The first step that falls outside is
    the point of no return - after that, a return trip at ``cost_per_step`` is
    no longer affordable.

    ``cost_per_step`` defaults to the hazard cost (1.5), i.e. a conservative
    worst-case estimate; pass 1.0 for the optimistic bound.
    """
    return _q(
        """
        SELECT s.step_index, s.cell_x, s.cell_y, ROUND(s.battery::numeric, 1) AS battery,
               ST_Y(s.position) AS lat, ST_X(s.position) AS lng,
               ROUND(ST_Distance(s.position::geography, sc.base::geography)::numeric, 1) AS dist_to_base_m,
               ROUND((s.battery / %(cost)s * sc.cell_size_m)::numeric, 1) AS return_range_m,
               ST_Covers(
                   ST_Buffer(sc.base::geography, s.battery / %(cost)s * sc.cell_size_m),
                   s.position::geography
               ) AS within_return_range
        FROM missions_runstep s
        JOIN rover_route r ON r.id = s.run_id
        JOIN base_location sc ON sc.id = r.scenario_id
        WHERE s.run_id = %(rid)s
        ORDER BY s.step_index
        """,
        {"rid": run_id, "cost": cost_per_step},
    )


def first_point_of_no_return(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """First step where the rover is already outside its return range (None if never)."""
    for row in rows:
        if not row["within_return_range"]:
            return row
    return None


# --------------------------------------------------------------------------- Q5
def scenarios_near(lng: float, lat: float, radius_m: float, limit: int = 50):
    """Q5 - PROXIMITY ("within radius"): scenarios whose base is within ``radius_m`` of a point.

    ``ST_DWithin`` on ``base::geography`` is served by the functional GiST index
    ``scenario_base_geog_gist`` (migration 0002).
    """
    return _q(
        """
        SELECT sc.id, sc.name, sc.scenario_type,
               ROUND(ST_Distance(sc.base::geography, pt.g)::numeric, 0) AS distance_m
        FROM base_location sc
        CROSS JOIN (SELECT ST_SetSRID(ST_MakePoint(%(lng)s, %(lat)s), 4326)::geography AS g) pt
        WHERE ST_DWithin(sc.base::geography, pt.g, %(radius)s)
        ORDER BY distance_m, sc.id
        LIMIT %(limit)s
        """,
        {"lng": lng, "lat": lat, "radius": radius_m, "limit": limit},
    )


# ------------------------------------------------------------- map layer helpers
def coverage_zone(scenario_id: int) -> dict | None:
    """Communication-coverage polygon: ``ST_Buffer`` of the base by the comm radius (GeoJSON)."""
    rows = _q(
        """
        SELECT ST_AsGeoJSON(
                   ST_Buffer(base::geography, comm_radius_cells * cell_size_m)::geometry, 6
               ) AS geojson
        FROM base_location WHERE id = %(sid)s
        """,
        {"sid": scenario_id},
    )
    return json.loads(rows[0]["geojson"]) if rows else None
