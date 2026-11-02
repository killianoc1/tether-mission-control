"""Glue between the pure-Python world/agents and the PostGIS models."""
from __future__ import annotations

import random

from django.contrib.gis.geos import LineString, Point
from django.db import transaction

from missions.models import Run, RunStep, Scenario, ScenarioFeature

from . import geo
from .agents import AGENTS, Episode, Step, run_episode
from .world import HAZARD_STEP_COST, GridWorld, generate_world


@transaction.atomic
def create_scenario(
    *, name: str, scenario_type: str, seed: int, lng: float, lat: float,
    grid_size: int = 15, cell_size_m: float = 25.0, initial_battery: float = 120.0,
    comm_radius_cells: float = 8.0, max_steps: int = 250, description: str = "", owner=None,
) -> Scenario:
    """Generate a world for ``seed`` and store it as georeferenced geometry.

    ``(lng, lat)`` is where the *base station* sits on the map.
    """
    # 1. Pure-Python step: decide where the base, target, obstacles and hazards go (no database yet).
    world = generate_world(seed, scenario_type, grid_size, initial_battery)
    # 2. Work out where the grid's south-west corner must be so the base cell sits at the chosen point.
    o_lng, o_lat = geo.origin_for_base(lng, lat, cell_size_m, *world.base)

    # 3. Save the scenario row; each geometry is built from grid cells by the helpers in geo.py.
    scenario = Scenario.objects.create(
        name=name, description=description, scenario_type=scenario_type, seed=seed,
        grid_size=grid_size, cell_size_m=cell_size_m, initial_battery=initial_battery,
        comm_radius_cells=comm_radius_cells, max_steps=max_steps,
        origin=Point(o_lng, o_lat, srid=geo.SRID),
        base=geo.cell_centre(o_lng, o_lat, cell_size_m, *world.base),
        target=geo.cell_centre(o_lng, o_lat, cell_size_m, *world.target),
        extent=geo.grid_extent(o_lng, o_lat, cell_size_m, grid_size),
        base_x=world.base[0], base_y=world.base[1],
        target_x=world.target[0], target_y=world.target[1],
        owner=owner,
    )

    # 4. One polygon row per obstacle/hazard cell, inserted in a single query with bulk_create.
    features = [
        ScenarioFeature(
            scenario=scenario, kind=kind, cell_x=x, cell_y=y,
            geom=geo.cell_polygon(o_lng, o_lat, cell_size_m, x, y),
            cost_multiplier=HAZARD_STEP_COST if kind == "hazard" else 1.0,
        )
        for kind, cells in (("obstacle", world.obstacles), ("hazard", world.hazards))
        for (x, y) in sorted(cells)
    ]
    ScenarioFeature.objects.bulk_create(features)
    return scenario


def world_from_scenario(scenario: Scenario) -> GridWorld:
    """Rebuild the in-memory grid for ``scenario`` from its stored features."""
    obstacles, hazards = set(), set()
    for kind, x, y in scenario.features.values_list("kind", "cell_x", "cell_y"):
        (obstacles if kind == "obstacle" else hazards).add((x, y))
    return GridWorld(
        size=scenario.grid_size, base=(scenario.base_x, scenario.base_y),
        target=(scenario.target_x, scenario.target_y), obstacles=obstacles, hazards=hazards,
    )


@transaction.atomic
def save_run(
    scenario: Scenario, agent: str, steps: list[Step], *, outcome: str, target_found: bool,
    returned_to_base: bool, collisions: int = 0, source: str = Run.Source.BUILTIN, user=None,
) -> Run:
    """Persist an episode: one Run (with LineString) plus one RunStep point per step."""
    # Convert each step's grid cell back to a real-world point, using the scenario's stored origin.
    o_lng, o_lat = scenario.origin.x, scenario.origin.y
    points = [geo.cell_centre(o_lng, o_lat, scenario.cell_size_m, s.x, s.y) for s in steps]
    # The Run row stores the whole route as one LineString built from those points.
    run = Run.objects.create(
        scenario=scenario, agent=agent, source=source, outcome=outcome,
        success=outcome == "success", target_found=target_found,
        returned_to_base=returned_to_base, steps=len(steps) - 1, collisions=collisions,
        battery_remaining=steps[-1].battery,
        trajectory=LineString([(p.x, p.y) for p in points], srid=geo.SRID),
        created_by=user,
    )
    # One RunStep row (a Point plus battery) per step, again in a single bulk insert.
    RunStep.objects.bulk_create(
        RunStep(
            run=run, step_index=i, cell_x=s.x, cell_y=s.y, position=points[i],
            battery=s.battery, action=s.action,
        )
        for i, s in enumerate(steps)
    )
    return run


def simulate_builtin(scenario: Scenario, agents=AGENTS, user=None, episode_seed: int | None = None) -> list[Run]:
    """Run the built-in A* agent on ``scenario`` and store the results."""
    world = world_from_scenario(scenario)
    if episode_seed is None:
        episode_seed = scenario.seed
    runs = []
    for agent in agents:
        ep: Episode = run_episode(world, agent, scenario.initial_battery, scenario.max_steps, episode_seed)
        runs.append(save_run(
            scenario, agent, ep.steps, outcome=ep.outcome, target_found=ep.target_found,
            returned_to_base=ep.returned_to_base, collisions=ep.collisions,
            source=Run.Source.BUILTIN, user=user,
        ))
    return runs


def random_seed() -> int:
    return random.SystemRandom().randrange(1, 1_000_000)
