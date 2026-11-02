"""Pure-Python grid world: scenario generation and path planning.

Nothing in this module touches Django or the database, so it can be tested on its own.
Movement model: four directions, no "stay" action, hazards are passable but cost more battery.
"""
from __future__ import annotations

import heapq
import random
from dataclasses import dataclass, field

Cell = tuple[int, int]

# Action letter -> (dx, dy).  y increases north.
DIRECTIONS: dict[str, Cell] = {"N": (0, 1), "S": (0, -1), "E": (1, 0), "W": (-1, 0)}
ACTION_FOR_DELTA = {delta: action for action, delta in DIRECTIONS.items()}

BASE_STEP_COST = 1.0
HAZARD_STEP_COST = 1.5
COST_NOISE = 0.10  # +/-10 % stochastic battery variation per step

# Per-scenario-type generation profile: fraction of cells that become obstacles /
# hazards.  Hazards are grown as clusters so they look like fire fronts, flood
# pools or contamination plumes rather than salt-and-pepper noise.
PROFILES: dict[str, dict[str, float]] = {
    "earthquake": {"obstacle": 0.20, "hazard": 0.03, "obstacle_clusters": 9, "hazard_clusters": 2},
    "wildfire": {"obstacle": 0.06, "hazard": 0.18, "obstacle_clusters": 4, "hazard_clusters": 3},
    "flood": {"obstacle": 0.05, "hazard": 0.22, "obstacle_clusters": 3, "hazard_clusters": 2},
    "industrial": {"obstacle": 0.10, "hazard": 0.12, "obstacle_clusters": 5, "hazard_clusters": 3},
}


@dataclass
class GridWorld:
    size: int
    base: Cell
    target: Cell
    obstacles: set[Cell] = field(default_factory=set)
    hazards: set[Cell] = field(default_factory=set)

    def in_bounds(self, cell: Cell) -> bool:
        """True if the cell lies inside the square grid."""
        return 0 <= cell[0] < self.size and 0 <= cell[1] < self.size

    def passable(self, cell: Cell) -> bool:
        """True if a rover may enter the cell: inside the grid and not an obstacle."""
        return self.in_bounds(cell) and cell not in self.obstacles

    def step_cost(self, cell: Cell) -> float:
        """Nominal (noise-free) battery cost of *entering* ``cell``."""
        return HAZARD_STEP_COST if cell in self.hazards else BASE_STEP_COST

    def moves(self, cell: Cell) -> list[tuple[str, Cell]]:
        """All legal (action, next_cell) moves from ``cell``."""
        out = []
        for action, (dx, dy) in DIRECTIONS.items():
            nxt = (cell[0] + dx, cell[1] + dy)
            if self.passable(nxt):
                out.append((action, nxt))
        return out


def manhattan(a: Cell, b: Cell) -> int:
    """Grid distance counting only N/S/E/W steps; the A* heuristic."""
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def shortest_path(world: GridWorld, start: Cell, goal: Cell) -> tuple[list[Cell], float] | None:
    """A* over the grid with hazard-weighted costs.

    Returns ``(cells_from_start_exclusive_to_goal_inclusive, total_cost)`` or
    ``None`` if the goal is unreachable.  The Manhattan heuristic is admissible
    because the cheapest possible step costs 1.0.
    """
    if start == goal:
        return [], 0.0
    frontier: list[tuple[float, float, Cell]] = [(float(manhattan(start, goal)), 0.0, start)]
    best: dict[Cell, float] = {start: 0.0}
    parent: dict[Cell, Cell] = {}
    while frontier:
        _, g, current = heapq.heappop(frontier)
        if current == goal:
            path = []
            node = goal
            while node != start:
                path.append(node)
                node = parent[node]
            path.reverse()
            return path, g
        if g > best.get(current, float("inf")):
            continue
        for _, nxt in world.moves(current):
            new_g = g + world.step_cost(nxt)
            if new_g < best.get(nxt, float("inf")):
                best[nxt] = new_g
                parent[nxt] = current
                heapq.heappush(frontier, (new_g + manhattan(nxt, goal), new_g, nxt))
    return None


def _grow_clusters(rng: random.Random, size: int, count: int, clusters: int, forbidden: set[Cell]) -> set[Cell]:
    """Grow ``count`` cells as ``clusters`` random-walk blobs avoiding ``forbidden``."""
    cells: set[Cell] = set()
    free = [(x, y) for x in range(size) for y in range(size) if (x, y) not in forbidden]
    if not free or count <= 0:
        return cells
    seeds = rng.sample(free, min(max(clusters, 1), len(free)))
    cells.update(seeds)
    attempts = 0
    while len(cells) < count and attempts < count * 60:
        attempts += 1
        x, y = rng.choice(sorted(cells))
        dx, dy = rng.choice(list(DIRECTIONS.values()))
        nxt = (x + dx, y + dy)
        if 0 <= nxt[0] < size and 0 <= nxt[1] < size and nxt not in forbidden:
            cells.add(nxt)
    return cells


def generate_world(
    seed: int, scenario_type: str, size: int = 15, battery: float = 120.0
) -> GridWorld:
    """Deterministically generate a feasible world for ``seed``.

    "Feasible" means the target is reachable and an optimal round trip uses at
    most 85 % of ``battery`` (so a perfect planner can succeed, but sloppy ones
    often will not).  The same arguments always yield the same world.
    """
    profile = PROFILES.get(scenario_type, PROFILES["earthquake"])
    n_cells = size * size
    for attempt in range(400):
        rng = random.Random(f"tether:{seed}:{scenario_type}:{size}:{attempt}")

        # Base in one of the grid's corner regions, target in the opposite half.
        edge = max(3, size // 5)
        bx = rng.randrange(edge) if rng.random() < 0.5 else size - 1 - rng.randrange(edge)
        by = rng.randrange(edge) if rng.random() < 0.5 else size - 1 - rng.randrange(edge)
        base = (bx, by)
        min_dist = int(0.55 * 2 * (size - 1))  # target well away from the base
        far = [
            (x, y)
            for x in range(size)
            for y in range(size)
            if manhattan((x, y), base) >= min_dist
        ]
        if not far:
            continue
        target = rng.choice(far)

        reserved = {base, target}
        obstacles = _grow_clusters(
            rng, size, round(profile["obstacle"] * n_cells), int(profile["obstacle_clusters"]), reserved
        )
        hazards = _grow_clusters(
            rng, size, round(profile["hazard"] * n_cells), int(profile["hazard_clusters"]),
            reserved | obstacles,
        )
        world = GridWorld(size=size, base=base, target=target, obstacles=obstacles, hazards=hazards)

        outbound = shortest_path(world, base, target)
        if outbound is None:
            continue
        inbound = shortest_path(world, target, base)
        if inbound is None:
            continue
        if outbound[1] + inbound[1] <= 0.85 * battery:
            return world
    raise ValueError(
        "Could not generate a feasible scenario for these settings - try a larger "
        "battery, a smaller grid, or a different seed."
    )
