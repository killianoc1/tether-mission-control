"""Unit tests for the pure-Python world generator and agents (no DB needed)."""
from django.test import SimpleTestCase

from missions.simulation.agents import AGENTS, run_episode
from missions.simulation.world import generate_world, manhattan, shortest_path


class GeneratorTests(SimpleTestCase):
    """generate_world: same seed gives the same map, and every map is feasible."""

    def test_same_seed_gives_same_world(self):
        a = generate_world(7, "wildfire")
        b = generate_world(7, "wildfire")
        self.assertEqual((a.base, a.target, a.obstacles, a.hazards), (b.base, b.target, b.obstacles, b.hazards))

    def test_different_seeds_differ(self):
        self.assertNotEqual(generate_world(1, "flood").obstacles, generate_world(2, "flood").obstacles)

    def test_world_is_feasible_for_every_type(self):
        for kind in ("earthquake", "wildfire", "flood", "industrial"):
            for seed in range(1, 15):
                w = generate_world(seed, kind, battery=120)
                out = shortest_path(w, w.base, w.target)
                back = shortest_path(w, w.target, w.base)
                self.assertIsNotNone(out)
                self.assertIsNotNone(back)
                self.assertLessEqual(out[1] + back[1], 0.85 * 120 + 1e-9)
                self.assertNotIn(w.base, w.obstacles | w.hazards)
                self.assertNotIn(w.target, w.obstacles | w.hazards)

    def test_infeasible_settings_raise(self):
        with self.assertRaises(ValueError):
            generate_world(1, "earthquake", size=15, battery=20)

    def test_target_is_far_from_base(self):
        w = generate_world(3, "earthquake")
        self.assertGreaterEqual(manhattan(w.base, w.target), int(0.55 * 2 * 14))


class AgentTests(SimpleTestCase):
    """The A* agent: legal moves only, correct outcomes, repeatable runs."""

    def setUp(self):
        self.world = generate_world(5, "earthquake")

    def test_all_agents_make_only_legal_moves(self):
        for agent in AGENTS:
            ep = run_episode(self.world, agent, 120, 250, seed=5)
            self.assertEqual((ep.steps[0].x, ep.steps[0].y), self.world.base)
            for prev, cur in zip(ep.steps, ep.steps[1:]):
                self.assertEqual(manhattan((prev.x, prev.y), (cur.x, cur.y)), 1)
                self.assertNotIn((cur.x, cur.y), self.world.obstacles)
                self.assertLessEqual(cur.battery, prev.battery)

    def test_astar_completes_the_mission(self):
        ep = run_episode(self.world, "astar", 120, 250, seed=5)
        self.assertEqual(ep.outcome, "success")
        self.assertTrue(ep.target_found and ep.returned_to_base)
        self.assertEqual((ep.steps[-1].x, ep.steps[-1].y), self.world.base)

    def test_episodes_are_reproducible(self):
        a = run_episode(self.world, "astar", 120, 250, seed=9)
        b = run_episode(self.world, "astar", 120, 250, seed=9)
        self.assertEqual([(s.x, s.y, s.battery) for s in a.steps], [(s.x, s.y, s.battery) for s in b.steps])

    def test_unknown_agent_rejected(self):
        with self.assertRaises(ValueError):
            run_episode(self.world, "magic", 120, 250, seed=1)

    def test_tiny_battery_ends_in_depletion(self):
        ep = run_episode(self.world, "astar", 5, 250, seed=5)
        self.assertEqual(ep.outcome, "battery_depleted")
        self.assertEqual(ep.steps[-1].battery, 0.0)
