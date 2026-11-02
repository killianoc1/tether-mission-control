# Architecture

The diagrams below are Mermaid (rendered by GitHub). PNG copies for reports are in [`docs/diagrams/`](diagrams/): `architecture-components.png`, `sequence-replay.png`, `database-schema.png`.

## Components

```mermaid
flowchart LR
    subgraph Host["Developer machine (Docker)"]
        direction LR
        subgraph Net["Docker network tether_net - 172.28.0.0/24"]
            NGINX["nginx<br/>reverse proxy<br/>172.28.0.30"]
            WEB["web<br/>Django + GeoDjango + DRF<br/>172.28.0.20"]
            DB[("db<br/>PostgreSQL 16 + PostGIS<br/>172.28.0.10")]
            PGA["pgadmin 4<br/>172.28.0.40"]
        end
        VOL1[("volume: pgdata")]
        VOL2[("volume: pgadmin_data")]
    end
    BROWSER["Browser<br/>Bootstrap 5 + Leaflet"]
    OSM["OpenStreetMap<br/>tile servers"]

    BROWSER -- "HTTP :8080" --> NGINX
    NGINX -- "proxy_pass :8000" --> WEB
    WEB -- "SQL / PostGIS" --> DB
    PGA -- "SQL" --> DB
    DB --- VOL1
    PGA --- VOL2
    BROWSER -- "map tiles" --> OSM
    BROWSER -. "localhost:5050" .-> PGA
```

Only Nginx (port 8080) and pgAdmin (bound to `127.0.0.1:5050`) are published to the host. The database is reachable
only from inside `tether_net`.

## MVC mapping

| MVC role | Where | Responsibility |
|---|---|---|
| **Model** | `missions/models.py`, `missions/spatial.py`, migrations | Spatial schema, constraints, indexes, PostGIS queries |
| **View** (presentation) | `templates/`, `missions/templates/`, `static/js`, `static/css`, serializers | HTML pages, Leaflet map, GeoJSON representation |
| **Controller** | `missions/views.py` (pages), `missions/api/views.py` (REST), `missions/urls.py`, `missions/api/urls.py` | Routing, authentication/permissions, request parsing, calling services |
| Simulation | `missions/simulation/` | World generation, agents, persistence (framework-independent where possible) |

`missions/simulation/world.py` and `agents.py` import nothing from Django, so they can be tested without a database.

## Request flow: replaying a scenario

```mermaid
sequenceDiagram
    participant B as Browser (mission.js)
    participant N as Nginx
    participant D as Django (DRF)
    participant P as PostGIS

    B->>N: GET /scenarios/10/
    N->>D: proxy
    D-->>B: HTML shell (no data)
    par parallel API calls
        B->>D: GET /api/scenarios/10/layers/
        D->>P: features, ST_Buffer(base, comm radius)
        B->>D: GET /api/scenarios/10/metrics/
        D->>P: ST_Length, ST_Intersection, ST_DWithin
        B->>D: GET /api/scenarios/10/replay/
        D->>P: run steps
    end
    D-->>B: GeoJSON / JSON
    B->>D: GET /api/runs/{id}/return-range/ (per run)
    D->>P: ST_Buffer + ST_Covers
    B->>B: draw layers, routes, replay slider, results table
    B->>D: GET /api/scenarios/10/nearest/?lng=..&lat=.. (on map click)
    D->>P: KNN on geography index
```

The HTML pages are thin shells; all data comes from the REST API, so the API is the single source of truth and any other client
uses exactly the same endpoints as the browser.

## Database schema

```mermaid
erDiagram
    USER ||--o{ SCENARIO : owns
    USER ||--o{ RUN : created
    SCENARIO ||--o{ SCENARIO_FEATURE : contains
    SCENARIO ||--o{ RUN : has
    RUN ||--o{ RUN_STEP : records

    SCENARIO {
        bigint id PK
        varchar name
        varchar scenario_type "earthquake|wildfire|flood|industrial"
        int seed
        smallint grid_size "5..40"
        float cell_size_m
        float initial_battery
        float comm_radius_cells
        int max_steps
        point origin "SW grid corner, SRID 4326"
        point base "GiST + geography GiST"
        point target
        polygon extent "grid footprint, GiST"
        smallint base_x
        smallint base_y
        smallint target_x
        smallint target_y
        bigint owner_id FK
        timestamptz created_at
    }
    SCENARIO_FEATURE {
        bigint id PK
        bigint scenario_id FK
        varchar kind "obstacle|hazard"
        smallint cell_x
        smallint cell_y
        polygon geom "GiST + geography GiST"
        float cost_multiplier
    }
    RUN {
        bigint id PK
        bigint scenario_id FK
        varchar agent "astar"
        varchar source "builtin"
        varchar outcome
        bool success
        bool target_found
        bool returned_to_base
        int steps
        int collisions
        float battery_remaining
        linestring trajectory "GiST"
        bigint created_by_id FK
        timestamptz created_at
    }
    RUN_STEP {
        bigint id PK
        bigint run_id FK
        int step_index
        smallint cell_x
        smallint cell_y
        point position "GiST"
        float battery
        char action "N|S|E|W"
    }
```

Table names in PostgreSQL: `SCENARIO` is stored as `base_location` and `SCENARIO_FEATURE` as `rover_hazards` (set with `db_table` in `models.py`,
renamed in migration `0003_rename_tables`); `RUN` as `rover_route`; `RUN_STEP` keeps Django's default name `missions_runstep`.

Constraints worth noting: `scenario.grid_size` is limited to 5-40 and `cell_size_m > 0` (check constraints);
`(scenario, cell_x, cell_y)` is unique for features and `(run, step_index)` is unique for steps; deleting a scenario
cascades to its features, runs and steps; deleting a user sets owners to `NULL` rather than deleting data.
Indexes are listed in [SPATIAL_QUERIES.md](SPATIAL_QUERIES.md#indexes).

## Georeferencing

A scenario has a south-west `origin` and a `cell_size_m`. Cell `(x, y)` covers
`origin + (x, y) * cell_size_m` on a local flat-earth approximation
(`1 deg latitude = 111,320 m`, longitude scaled by `cos(latitude)`), where `x` runs east and `y` north. When a scenario is
created the origin is chosen so that the centre of the base cell lies exactly where the user clicked. The error of the
approximation over a few hundred metres is far below one cell. See `missions/simulation/geo.py`.

## Scenario generation and reproducibility

`generate_world(seed, type, size, battery)` seeds a private random-number generator from the arguments, grows obstacle and
hazard clusters according to a per-type profile, places the base in a corner region and the target in the far half, and
accepts the map only if the target is reachable and an optimal round trip costs at most 85 % of the battery. The same
arguments always give the same map; episodes are seeded separately so the A* run is reproducible too.

## Deployment configuration

| Concern | Mechanism |
|---|---|
| Configuration | environment variables (`.env`, template in `.env.example`) |
| Start-up order | `db` health check -> `web` (migrate, collectstatic, bootstrap) -> `nginx` waits for `web` health |
| Persistence | named volumes `pgdata`, `pgadmin_data` |
| Isolation | user-defined bridge network `tether_net`, subnet `172.28.0.0/24`, fixed addresses |
| Least exposure | DB port unpublished; pgAdmin on localhost only; container runs as non-root |
| Static files | collected at start-up and served by WhiteNoise |
