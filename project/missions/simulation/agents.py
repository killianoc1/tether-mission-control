"""The A* rover agent and the episode loop.

The agent plans its route with A* (see ``world.shortest_path``) and the loop plays it
out step by step on a seeded scenario.  Mission rules:

1. Start at the base with a full battery.
2. Reach the target cell (target found).
3. Return to the base while battery is still above zero.

Each step costs 1.0 (1.5 on a hazard cell) +/-10 % random variation.  Agents
only ever make legal moves (no walking into obstacles).
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from .world import (
    ACTION_FOR_DELTA, COST_NOISE, Cell, GridWorld, shortest_path,
)

AGENTS = ("astar",)


@dataclass
class Step:
    x: int
    y: int
    battery: float
    action: str = ""


@dataclass
class Episode:
    steps: list[Step] = field(default_factory=list)
    outcome: str = "incomplete"
    target_found: bool = False
    returned_to_base: bool = False
    collisions: int = 0

    @property
    def success(self) -> bool:
        return self.outcome == "success"


def _move_astar(world: GridWorld, pos: Cell, goal: Cell, visits: dict, rng: random.Random, plan: list):
    """Follow a precomputed optimal base -> target -> base route (full map knowledge)."""
    nxt = plan.pop(0)
    return ACTION_FOR_DELTA[(nxt[0] - pos[0], nxt[1] - pos[1])], nxt


# A policy returns (action, next_cell).  Kept as a lookup so another agent could be added later.
_POLICIES = {"astar": _move_astar}


def run_episode(world: GridWorld, agent: str, battery: float, max_steps: int, seed: int) -> Episode:
    """Simulate one episode and return the full trajectory."""
    if agent not in _POLICIES:
        raise ValueError(f"Unknown agent '{agent}'. Choose from {', '.join(AGENTS)}.")
    policy = _POLICIES[agent]
    # Seeded per (seed, agent): the same scenario + seed always replays the same battery noise.
    rng = random.Random(f"episode:{seed}:{agent}")

    # A* has full knowledge of the map, so it plans the whole base -> target -> base route up front.
    plan: list[Cell] = []
    if agent == "astar":
        out = shortest_path(world, world.base, world.target)
        back = shortest_path(world, world.target, world.base)
        if out is None or back is None:
            raise ValueError("Target is not reachable from the base.")
        plan = out[0] + back[0]

    pos = world.base
    episode = Episode(steps=[Step(pos[0], pos[1], round(battery, 3))])
    visits: dict[Cell, int] = {pos: 1}

    # One loop iteration = one move.  Stops on success, empty battery, or max_steps (timeout).
    for _ in range(max_steps):
        goal = world.base if episode.target_found else world.target
        action, nxt = policy(world, pos, goal, visits, rng, plan)
        # Hazard cells cost more battery; +/-10% noise makes each run slightly unpredictable.
        cost = world.step_cost(nxt) * (1.0 + rng.uniform(-COST_NOISE, COST_NOISE))
        battery -= cost
        pos = nxt
        visits[pos] = visits.get(pos, 0) + 1
        episode.steps.append(Step(pos[0], pos[1], round(max(battery, 0.0), 3), action))

        if pos == world.target:
            episode.target_found = True
        if battery <= 0:
            episode.outcome = "battery_depleted"
            return episode
        if episode.target_found and pos == world.base:
            episode.returned_to_base = True
            episode.outcome = "success"
            return episode

    episode.outcome = "timeout"
    return episode
