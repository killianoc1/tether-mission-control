# Spatial queries

All queries live in [`project/missions/spatial.py`](../project/missions/spatial.py). Values are always passed as bound
parameters. Geometries are stored as WGS 84 (`SRID 4326`) `geometry`; wherever a real distance or length is needed
they are cast to `geography`, so results are in **metres**.

| # | Question | Technique | Endpoint |
|---|---|---|---|
| Q1 | Which hazards/obstacles are nearest to where I clicked? | Proximity: k-nearest-neighbour | `GET /api/scenarios/{id}/nearest/` |
| Q2 | How far did each agent travel *inside* hazard areas? | Intersection | `GET /api/scenarios/{id}/metrics/` |
| Q3 | How often was each agent within radio range of the base? | Containment (`ST_DWithin`) | `GET /api/scenarios/{id}/metrics/` |
| Q4 | When did a rover pass its point of no return? | Buffering | `GET /api/runs/{id}/return-range/` |
| Q5 | Which scenarios are within *r* km of a point? | Proximity / radius search | `GET /api/scenarios/nearby/` |

## Q1 - nearest hazards (proximity, KNN)

```sql
SELECT f.id, f.kind, f.cell_x, f.cell_y,
       ROUND(ST_Distance(f.geom::geography, pt.g, false)::numeric, 1) AS distance_m
FROM rover_hazards f
CROSS JOIN (SELECT ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography AS g) pt
WHERE f.scenario_id = :sid AND (:kind IS NULL OR f.kind = :kind)
ORDER BY f.geom::geography <-> pt.g, f.id
LIMIT :limit;
```

Design note: a first version used `ORDER BY geom <-> point` on plain `geometry`. A test caught that this ranks by
*degrees*, and at 53 degrees north a degree of longitude is about 40 % shorter than a degree of latitude, so the
order was wrong in metres. The fix orders on `geography` (metres) and uses a functional GiST index built on the same
`geom::geography` expression. The `<->` operator measures on a sphere, so the reported distance uses the sphere too
(`use_spheroid = false`) to stay consistent; it differs from the spheroid by under half a percent.

## Q2 - hazard exposure (intersection)

```sql
WITH hz AS (
  SELECT ST_Union(geom) AS g FROM rover_hazards
  WHERE scenario_id = :sid AND kind = 'hazard')
SELECT r.id,
       ST_Length(r.trajectory::geography)                         AS path_length_m,
       COALESCE(ST_Length(
         ST_CollectionExtract(ST_Intersection(r.trajectory, hz.g), 2)::geography), 0) AS hazard_exposure_m
FROM rover_route r LEFT JOIN hz ON TRUE
WHERE r.scenario_id = :sid;
```

The hazard cells are merged with `ST_Union`; the route (a `LineString`) is intersected with them, the line parts are
extracted (`ST_CollectionExtract(..., 2)`) and measured. This answers "how much of its journey did the agent spend in
the danger zone?", which is a direct safety metric for judging how safe a route is.

## Q3 - communication uptime (containment)

```sql
SELECT 100.0 * COUNT(*) FILTER (
         WHERE ST_DWithin(s.position::geography, sc.base::geography, sc.comm_radius_cells * sc.cell_size_m))
       / COUNT(*) AS comm_uptime_pct
FROM missions_runstep s WHERE s.run_id = :rid;
```

The share of steps at which the rover was within radio range of the base. It is computed in the same statement as Q2
(see `run_metrics`). The coverage polygon drawn on the map is `ST_Buffer(base::geography, radius)::geometry`
(`coverage_zone`).

## Q4 - point of no return (buffering)

```sql
SELECT s.step_index, s.battery,
       ST_Distance(s.position::geography, sc.base::geography)                AS dist_to_base_m,
       s.battery / :cost * sc.cell_size_m                                    AS return_range_m,
       ST_Covers(ST_Buffer(sc.base::geography, s.battery / :cost * sc.cell_size_m),
                 s.position::geography)                                      AS within_return_range
FROM missions_runstep s
JOIN rover_route r ON r.id = s.run_id
JOIN base_location sc ON sc.id = r.scenario_id
WHERE s.run_id = :rid ORDER BY s.step_index;
```

At each step the remaining battery can buy at most `battery / cost` cells of travel, so the *return range* is a circle
around the base of that radius. The first step where the rover is **outside** that circle is its point of no return:
from there it cannot afford the trip home at the assumed per-step cost (default 1.5, the hazard cost, i.e. a
conservative estimate; pass `cost_per_step=1.0` for the optimistic bound). This is the geometric form of the central
trade-off in the TETHER project: *is the target worth the energy needed to get back?*

## Q5 - scenarios near a point (radius search)

```sql
SELECT sc.id, sc.name, ST_Distance(sc.base::geography, pt.g) AS distance_m
FROM base_location sc
CROSS JOIN (SELECT ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography AS g) pt
WHERE ST_DWithin(sc.base::geography, pt.g, :radius_m)
ORDER BY distance_m LIMIT :limit;
```

## Indexes

Every geometry column has a GiST index (created automatically by GeoDjango). Two additional functional indexes are
created in migration `0002_geography_index`, because PostgreSQL can only use an index for an expression if the index
is built on the same expression:

```sql
CREATE INDEX scenario_base_geog_gist   ON base_location        USING GIST ((base::geography));
CREATE INDEX feature_geom_geog_gist    ON rover_hazards USING GIST ((geom::geography));
```

Plans from the running database (`SET enable_seqscan = off` so that the small demo tables still show the index being
chosen; with only a few hundred rows PostgreSQL would otherwise prefer a sequential scan, which is correct for tiny
tables):

```
-- Q1
Limit
  ->  Index Scan using feature_geom_geog_gist on rover_hazards f
        Order By: ((geom)::geography <-> '...'::geography)
        Filter: (scenario_id = 10)

-- Q5
Index Scan using scenario_base_geog_gist on base_location sc
  Index Cond: ((base)::geography && _st_expand('...'::geography, '25000'::double precision))
  Filter: st_dwithin((base)::geography, '...'::geography, '25000'::double precision, true)

-- bbox filter on the grid extent (list endpoint, ?bbox=)
Index Scan using missions_scenario_extent_..._id on base_location
  Index Cond: (extent && '...'::geometry)
```

Reproduce with `docker compose exec db psql -U tether -d tether` and `EXPLAIN` in front of any query above.

| Geometry column | Type | Index |
|---|---|---|
| `scenario.base`, `target`, `origin` | Point | GiST; `base` also has the geography index |
| `scenario.extent` | Polygon | GiST (used by the `?bbox=` filter) |
| `scenariofeature.geom` | Polygon | GiST + geography GiST |
| `run.trajectory` | LineString | GiST |
| `runstep.position` | Point | GiST |

Run `python manage.py test missions` to verify that all of these indexes exist (`test_spatial_indexes_exist`).
