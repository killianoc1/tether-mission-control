"""API + PostGIS integration tests (need the PostGIS test database)."""
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from missions import spatial
from missions.models import Scenario
from missions.simulation import persistence

User = get_user_model()
BASE_LNG, BASE_LAT = -6.3290, 53.0106


class ApiTestBase(APITestCase):
    """Shared fixture: two users and one wildfire scenario near Glendalough that already has its A* run."""

    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user("owner", password="pw-owner-1234")
        cls.other = User.objects.create_user("other", password="pw-other-1234")
        cls.scenario = persistence.create_scenario(
            name="Test fire", scenario_type="wildfire", seed=2, lng=BASE_LNG, lat=BASE_LAT,
            initial_battery=120.0, owner=cls.owner,
        )
        persistence.simulate_builtin(cls.scenario, user=cls.owner)

    def login(self, user):
        self.client.force_authenticate(user)


class ApiDocsTests(ApiTestBase):
    """The auto-generated OpenAPI schema and Swagger page (drf-spectacular)."""

    def test_schema_and_swagger_page_are_available(self):
        schema = self.client.get("/api/schema/")
        self.assertEqual(schema.status_code, 200)
        self.assertIn(b"/api/scenarios/", schema.content)
        self.assertIn(b"return-range", schema.content)
        self.assertEqual(self.client.get("/api/docs/").status_code, 200)


class ScenarioApiTests(ApiTestBase):
    """Scenario endpoints: GeoJSON output, create/update/delete, permissions, validation and filters."""

    def test_list_is_public_and_geojson(self):
        r = self.client.get("/api/scenarios/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["type"], "FeatureCollection")
        feature = r.json()["features"][0]
        self.assertEqual(feature["geometry"]["type"], "Point")
        self.assertEqual(feature["properties"]["run_count"], 1)

    def test_create_requires_authentication(self):
        r = self.client.post("/api/scenarios/", {"name": "x", "scenario_type": "flood", "lng": 0, "lat": 53}, format="json")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["error"]["status"], 403)

    def test_create_generates_geometry_and_baselines(self):
        self.login(self.owner)
        r = self.client.post("/api/scenarios/", {
            "name": "  New flood ", "scenario_type": "flood", "seed": 5, "lng": -6.5, "lat": 53.1,
        }, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        sc = Scenario.objects.get(pk=r.json()["id"])
        self.assertEqual(sc.name, "New flood")
        self.assertEqual(sc.owner, self.owner)
        self.assertEqual(sc.runs.count(), 1)
        self.assertGreater(sc.features.count(), 0)
        # The base cell centre sits exactly where the user clicked.
        self.assertAlmostEqual(sc.base.x, -6.5, places=6)
        self.assertAlmostEqual(sc.base.y, 53.1, places=6)

    def test_same_seed_is_reproducible_through_api(self):
        self.login(self.owner)
        payload = {"name": "A", "scenario_type": "industrial", "seed": 9, "lng": -6.2, "lat": 53.3, "run_baselines": False}
        a = self.client.post("/api/scenarios/", payload, format="json").json()
        b = self.client.post("/api/scenarios/", {**payload, "name": "B"}, format="json").json()
        self.assertEqual((a["properties"]["base_x"], a["properties"]["target_x"], a["properties"]["hazard_count"]),
                         (b["properties"]["base_x"], b["properties"]["target_x"], b["properties"]["hazard_count"]))

    def test_validation_errors_use_envelope(self):
        self.login(self.owner)
        r = self.client.post("/api/scenarios/", {"name": "", "scenario_type": "zombies", "lng": 500, "lat": 53}, format="json")
        self.assertEqual(r.status_code, 400)
        body = r.json()["error"]
        self.assertEqual(body["status"], 400)
        for field in ("name", "scenario_type", "lng"):
            self.assertIn(field, body["details"])

    def test_infeasible_settings_return_400(self):
        self.login(self.owner)
        r = self.client.post("/api/scenarios/", {"name": "Tiny", "scenario_type": "earthquake", "lng": 0, "lat": 53,
                                                   "initial_battery": 20}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_only_owner_can_delete(self):
        self.login(self.other)
        self.assertEqual(self.client.delete(f"/api/scenarios/{self.scenario.id}/").status_code, 403)
        self.login(self.owner)
        self.assertEqual(self.client.delete(f"/api/scenarios/{self.scenario.id}/").status_code, 204)
        self.assertFalse(Scenario.objects.filter(pk=self.scenario.id).exists())

    def test_patch_only_changes_descriptive_fields(self):
        self.login(self.owner)
        r = self.client.patch(f"/api/scenarios/{self.scenario.id}/", {"name": "Renamed", "seed": 999}, format="json")
        self.assertEqual(r.status_code, 200)
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.name, "Renamed")
        self.assertEqual(self.scenario.seed, 2)

    def test_patch_updates_name_and_description(self):
        self.login(self.owner)
        r = self.client.patch(f"/api/scenarios/{self.scenario.id}/", {"name": "Edited", "description": "New text"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.scenario.refresh_from_db()
        self.assertEqual((self.scenario.name, self.scenario.description), ("Edited", "New text"))

    def test_patch_rejects_blank_name(self):
        self.login(self.owner)
        r = self.client.patch(f"/api/scenarios/{self.scenario.id}/", {"name": ""}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"]["status"], 400)

    def test_patch_requires_owner(self):
        url = f"/api/scenarios/{self.scenario.id}/"
        self.assertEqual(self.client.patch(url, {"name": "x"}, format="json").status_code, 403)  # anonymous
        self.login(self.other)
        self.assertEqual(self.client.patch(url, {"name": "x"}, format="json").status_code, 403)

    def test_edit_controls_only_rendered_for_owner(self):
        url = f"/scenarios/{self.scenario.id}/"
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(url), "editScenarioModal")
        self.client.force_login(self.other)
        self.assertNotContains(self.client.get(url), "editScenarioModal")

    def test_filters(self):
        self.assertEqual(len(self.client.get("/api/scenarios/?type=flood").json()["features"]), 0)
        self.assertEqual(len(self.client.get("/api/scenarios/?type=wildfire").json()["features"]), 1)
        self.assertEqual(len(self.client.get("/api/scenarios/?q=fire").json()["features"]), 1)
        near = "bbox=-6.34,53.00,-6.32,53.02"
        far = "bbox=0,0,1,1"
        self.assertEqual(len(self.client.get(f"/api/scenarios/?{near}").json()["features"]), 1)
        self.assertEqual(len(self.client.get(f"/api/scenarios/?{far}").json()["features"]), 0)
        self.assertEqual(self.client.get("/api/scenarios/?bbox=oops").status_code, 400)

    def test_layers_endpoint(self):
        d = self.client.get(f"/api/scenarios/{self.scenario.id}/layers/").json()
        self.assertEqual(d["extent"]["type"], "Polygon")
        self.assertEqual(d["coverage"]["type"], "Polygon")
        self.assertEqual(len(d["hazards"]["features"]), self.scenario.features.filter(kind="hazard").count())

    def test_simulate_requires_auth_and_replace_works(self):
        url = f"/api/scenarios/{self.scenario.id}/simulate/"
        self.assertEqual(self.client.post(url, {}, format="json").status_code, 403)
        self.login(self.owner)
        r = self.client.post(url, {"agents": ["astar"], "replace": True}, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(self.scenario.runs.filter(agent="astar").count(), 1)
        self.assertEqual(self.client.post(url, {"agents": ["wizard"]}, format="json").status_code, 400)


class RunApiTests(ApiTestBase):
    """Run endpoints: read as GeoJSON, delete permissions, and no direct way to create runs."""

    def test_runs_cannot_be_created_directly(self):
        self.login(self.owner)
        self.assertEqual(self.client.post("/api/runs/", {}, format="json").status_code, 405)

    def test_run_geojson_and_delete_permissions(self):
        run = self.scenario.runs.first()
        r = self.client.get(f"/api/runs/{run.id}/")
        self.assertEqual(r.json()["geometry"]["type"], "LineString")
        self.login(self.other)
        self.assertEqual(self.client.delete(f"/api/runs/{run.id}/").status_code, 403)
        self.login(self.owner)
        self.assertEqual(self.client.delete(f"/api/runs/{run.id}/").status_code, 204)


class SpatialQueryTests(ApiTestBase):
    """The five PostGIS queries in spatial.py, plus a check that the spatial indexes exist."""

    def test_q1_nearest_is_ordered_and_in_metres(self):
        rows = spatial.nearest_features(self.scenario.id, BASE_LNG, BASE_LAT, limit=5)
        self.assertEqual(len(rows), 5)
        distances = [float(r["distance_m"]) for r in rows]
        self.assertEqual(distances, sorted(distances))
        self.assertLess(distances[-1], 400)   # a 15x15 grid of 25 m cells is ~375 m across
        only_haz = spatial.nearest_features(self.scenario.id, BASE_LNG, BASE_LAT, kind="hazard", limit=3)
        self.assertTrue(all(r["kind"] == "hazard" for r in only_haz))

    def test_q1_endpoint_validates_input(self):
        base = f"/api/scenarios/{self.scenario.id}/nearest/"
        self.assertEqual(self.client.get(base).status_code, 400)
        self.assertEqual(self.client.get(base + "?lng=abc&lat=1").status_code, 400)
        self.assertEqual(self.client.get(base + "?lng=0&lat=95").status_code, 400)
        self.assertEqual(self.client.get(base + f"?lng={BASE_LNG}&lat={BASE_LAT}&kind=lava").status_code, 400)
        self.assertEqual(self.client.get(base + f"?lng={BASE_LNG}&lat={BASE_LAT}&limit=2").status_code, 200)

    def test_q2_q3_metrics(self):
        rows = {r["agent"]: r for r in spatial.run_metrics(self.scenario.id)}
        self.assertEqual(set(rows), {"astar"})
        for r in rows.values():
            self.assertGreater(float(r["path_length_m"]), 0)
            self.assertGreaterEqual(float(r["hazard_exposure_m"]), 0)
            self.assertLessEqual(float(r["hazard_exposure_m"]), float(r["path_length_m"]) + 0.1)
            self.assertTrue(0 <= float(r["comm_uptime_pct"]) <= 100)
        # Path length should be ~ steps * cell size (each step moves one 25 m cell).
        astar = rows["astar"]
        self.assertAlmostEqual(float(astar["path_length_m"]), astar["steps"] * 25, delta=astar["steps"] * 0.5)

    def test_q2_hazard_exposure_is_zero_without_hazards(self):
        sc = persistence.create_scenario(name="Dry", scenario_type="earthquake", seed=1, lng=-6.3, lat=53.0)
        sc.features.filter(kind="hazard").delete()
        persistence.simulate_builtin(sc, agents=["astar"])
        self.assertEqual(float(spatial.run_metrics(sc.id)[0]["hazard_exposure_m"]), 0.0)

    def test_q4_return_range_finds_point_of_no_return(self):
        run = self.scenario.runs.get(agent="astar")         # seed 2 / battery 120 -> still has slack
        rows = spatial.return_range(run.id)
        self.assertEqual(rows[0]["step_index"], 0)
        self.assertTrue(rows[0]["within_return_range"])      # at base with full battery
        # A* plans an affordable round trip, so at the normal cost it never passes the point of no return.
        # With an absurdly high per-step cost (20) the return range is only a few cells, so it must.
        pessimistic = spatial.first_point_of_no_return(spatial.return_range(run.id, cost_per_step=20))
        self.assertIsNotNone(pessimistic)
        self.assertGreater(pessimistic["step_index"], 0)

    def test_q4_endpoint(self):
        run = self.scenario.runs.get(agent="astar")
        d = self.client.get(f"/api/runs/{run.id}/return-range/").json()
        self.assertEqual(d["run_id"], run.id)
        self.assertEqual(len(d["steps"]), run.steps + 1)
        self.assertEqual(self.client.get(f"/api/runs/{run.id}/return-range/?cost_per_step=99").status_code, 400)

    def test_q5_nearby_scenarios(self):
        near = spatial.scenarios_near(BASE_LNG, BASE_LAT, 1000)
        self.assertEqual([r["id"] for r in near], [self.scenario.id])
        self.assertLess(float(near[0]["distance_m"]), 5)
        self.assertEqual(spatial.scenarios_near(-6.0, 53.5, 1000), [])
        r = self.client.get(f"/api/scenarios/nearby/?lng={BASE_LNG}&lat={BASE_LAT}&radius_km=5")
        self.assertEqual(len(r.json()), 1)
        self.assertEqual(self.client.get("/api/scenarios/nearby/?lng=1").status_code, 400)

    def test_sql_injection_is_inert(self):
        r = self.client.get(f"/api/scenarios/{self.scenario.id}/nearest/?lng=1;DROP TABLE base_location;--&lat=2")
        self.assertEqual(r.status_code, 400)
        self.assertTrue(Scenario.objects.exists())

    def test_spatial_indexes_exist(self):
        from django.db import connection
        with connection.cursor() as c:
            c.execute(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename IN ('base_location', 'rover_hazards', 'rover_route', 'missions_runstep')"
            )
            names = " ".join(r[0] for r in c.fetchall())
        for needle in ("scenario_extent", "scenario_base", "scenariofeature_geom", "run_trajectory",
                       "runstep_position", "scenario_base_geog_gist", "feature_geom_geog_gist"):
            self.assertIn(needle, names)


class PageTests(ApiTestBase):
    """HTML pages render, 404 for unknown scenarios, and edit controls appear only for the owner."""

    def test_pages_render(self):
        self.assertContains(self.client.get("/"), "Disaster-response scenarios")
        self.assertContains(self.client.get(f"/scenarios/{self.scenario.id}/"), "Test fire")
        self.assertContains(self.client.get("/guide/"), "User guide")
        self.assertEqual(self.client.get("/scenarios/99999/").status_code, 404)
        self.assertEqual(self.client.get("/healthz").status_code, 200)

    def test_edit_buttons_only_for_owner_in_markup(self):
        self.client.force_login(self.other)
        self.assertContains(self.client.get(f"/scenarios/{self.scenario.id}/"), 'data-can-edit=""')
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(f"/scenarios/{self.scenario.id}/"), 'data-can-edit="1"')
