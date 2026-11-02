# REST API

Base URL: `http://localhost:8080/api/`. All responses are JSON. Spatial results are GeoJSON where a geometry is involved.

## Interactive documentation

The API documents itself (drf-spectacular, as in the labs):

- **http://localhost:8080/api/docs/**: Swagger UI. Lists every endpoint, shows parameters and responses, and lets you try requests in the browser.
- **http://localhost:8080/api/schema/**: the raw OpenAPI 3 schema (download it to import into other tools).

The sections below describe the same endpoints in prose, with examples.

## Authentication and permissions

| Who | May do |
|---|---|
| Anyone | all `GET` requests |
| Signed-in user | create scenarios, run the rover |
| Owner of a scenario/run (or staff) | edit name/description, delete |

Two ways to authenticate:

```bash
# 1. Token (for scripts)
curl -X POST http://localhost:8080/api/auth/token/ -H 'Content-Type: application/json' \
     -d '{"username": "killian", "password": "..."}'            # -> {"token": "abc..."}
curl -H 'Authorization: Token abc...' http://localhost:8080/api/scenarios/

# 2. Session cookie (what the web UI uses; unsafe methods require the X-CSRFToken header)
```

## Error format

Every error uses the same envelope:

```json
{"error": {"status": 400, "message": "Validation failed.",
           "details": {"steps": ["Step 3: must move exactly one cell N/S/E/W from (2, 4); got (7, 4)."]}}}
```

Status codes: `400` validation, `401/403` authentication/permission, `404` not found, `429` throttled (600 anonymous / 6000 signed-in requests per hour).

## Scenarios

| Method | Path | Description |
|---|---|---|
| GET | `/scenarios/` | GeoJSON FeatureCollection (geometry = base station). Filters: `type`, `seed`, `q` (name contains), `bbox=minLng,minLat,maxLng,maxLat` (grid intersects box). Paginated: `page`, `page_size` (max 500) |
| POST | `/scenarios/` | Generate a scenario (auth) |
| GET | `/scenarios/{id}/` | One scenario as a GeoJSON Feature |
| PATCH | `/scenarios/{id}/` | Change `name` / `description` only (owner) |
| DELETE | `/scenarios/{id}/` | Delete scenario, features, runs and steps (owner) |
| GET | `/scenarios/nearby/` | **Q5** `lng`, `lat`, `radius_km` (default 25) -> scenarios by distance |
| GET | `/scenarios/{id}/layers/` | Grid extent, base, target, obstacles, hazards and coverage polygon as GeoJSON |
| GET | `/scenarios/{id}/nearest/` | **Q1** `lng`, `lat`, optional `kind` (`obstacle`/`hazard`), `limit` (1-25, default 3) |
| GET | `/scenarios/{id}/metrics/` | **Q2/Q3** per-run path length, hazard exposure, communication uptime |
| GET | `/scenarios/{id}/replay/` | Per-step `[lng, lat, battery, cell_x, cell_y]` for every run |
| POST | `/scenarios/{id}/simulate/` | Run the built-in A* rover (auth, owner). Body: `agents` (currently just `astar`), `replace` (bool), `episode_seed` |

### Create a scenario

```http
POST /api/scenarios/
{
  "name": "Glendalough valley - earthquake drill",
  "scenario_type": "earthquake",          // earthquake | wildfire | flood | industrial
  "lng": -6.329, "lat": 53.0106,           // where the base station sits
  "seed": 11,                              // optional; random if omitted. Same seed = same map
  "grid_size": 15, "cell_size_m": 25,      // optional (defaults shown): 5-40 cells, 5-200 m
  "initial_battery": 120,                  // optional: 20-1000
  "comm_radius_cells": 8,                  // optional: 1-60
  "max_steps": 250,                        // optional: 10-2000
  "description": "",
  "run_baselines": true                    // optional: also run the A* rover
}
```

`400` is returned for invalid values, and also if no feasible map exists for the settings (for example a battery that
cannot cover the shortest round trip).

### Nearest hazards (Q1)

```http
GET /api/scenarios/10/nearest/?lng=-6.186&lat=53.186&limit=2
[ {"id": 500, "kind": "hazard", "cell_x": 2, "cell_y": 12, "distance_m": 12.5, "lat": 53.186, "lng": -6.18562},
  {"id": 501, "kind": "hazard", "cell_x": 2, "cell_y": 13, "distance_m": 17.7, "lat": 53.18622, "lng": -6.18562} ]
```

### Spatial metrics (Q2, Q3)

```http
GET /api/scenarios/10/metrics/
[ {"run_id": 30, "agent": "astar", "outcome": "success", "success": true, "target_found": true,
   "returned_to_base": true, "steps": 38, "collisions": 0, "battery_remaining": 12.4,
   "path_length_m": 950.6, "hazard_exposure_m": 0.0, "comm_uptime_pct": 51.3},
  ... ]
```

## Runs

| Method | Path | Description |
|---|---|---|
| GET | `/runs/` | GeoJSON FeatureCollection (geometry = trajectory LineString). Filters: `scenario`, `agent` |
| GET | `/runs/{id}/` | One run as a GeoJSON Feature |
| DELETE | `/runs/{id}/` | Delete (run creator, scenario owner or staff) |
| GET | `/runs/{id}/return-range/` | **Q4** per-step return range and the first point of no return. Optional `cost_per_step` (0.5-5, default 1.5) |

### Return range (Q4)

```http
GET /api/runs/29/return-range/?cost_per_step=1.5
{ "run_id": 29, "cost_per_step": 1.5,
  "point_of_no_return": {"step_index": 27, "cell_x": 8, "cell_y": 6, "battery": 11.4,
                         "lat": 53.18465, "lng": -6.18338,
                         "dist_to_base_m": 230.7, "return_range_m": 190.1, "within_return_range": false},
  "steps": [ {"step_index": 0, "cell_x": 1, "cell_y": 12, "battery": 50.0, "lat": 53.186, "lng": -6.186,
              "dist_to_base_m": 0.0, "return_range_m": 833.3, "within_return_range": true}, ... ] }
```

(Illustrative values: at step 27 the rover is 230.7 m from base but 11.4 units of battery buy only 190.1 m of travel at
hazard cost, so it could no longer get home. A* plans a feasible round trip, so for built-in runs this is usually `null`;
raise `cost_per_step` to stress-test it.)

`point_of_no_return` is `null` if the rover was always within its return range.

## Health check

`GET /healthz` returns `ok` (used by Docker).
