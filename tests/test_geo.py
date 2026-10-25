"""Offline regression tests for the standalone CLI (no package install needed)."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import socket
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "geo.py"
SPEC = importlib.util.spec_from_file_location("geo_cli_under_test", SCRIPT)
geo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(geo)


def place(address="Example", lat=42.0, lng=-71.0, **raw):
    return SimpleNamespace(address=address, latitude=lat, longitude=lng,
                           raw={"type": "city", "class": "place", "importance": 0.8, **raw})


def result():
    return {"latitude": 42.0, "longitude": -71.0, "address": "Example",
            "boundary": None, "alternatives": [],
            "raw": {"type": "city", "class": "place", "addresstype": "city", "importance": 0.8}}


class OfflineCase(unittest.TestCase):
    def setUp(self):
        # Fail closed if a future test accidentally reaches an external service.
        self.start_patch(socket.socket, "connect", side_effect=AssertionError("Network forbidden"))
        self.locator = self.start_patch(geo, "Nominatim").return_value
        self.session = self.start_patch(geo.requests, "Session").return_value
        self.locator.geocode.return_value = None
        self.locator.reverse.return_value = None
        self.wrappers = []

        def pacing_wrapper(fn, **kwargs):
            wrapper = Mock(wraps=fn)
            self.wrappers.append(wrapper)
            return wrapper

        self.pacing = self.start_patch(
            geo, "RateLimiter", create=True, side_effect=pacing_wrapper)
        self.client = geo.GeoClient()

    def start_patch(self, target, name, **kwargs):
        p = patch.object(target, name, **kwargs)
        self.addCleanup(p.stop)
        return p.start()

    def cli(self, *argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(geo.sys, "argv", [str(SCRIPT), *argv]), \
                patch.object(geo, "GeoClient", return_value=self.client), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                code = geo.main()
            except SystemExit as exc:
                code = exc.code
        return code, stdout.getvalue(), stderr.getvalue()

    def assert_failure(self, *argv):
        code, out, err = self.cli(*argv)
        self.assertNotEqual(code, 0)
        self.assertEqual(out, "")
        self.assertTrue(err.strip())


class ClientTests(OfflineCase):
    def test_rate_limit_configuration_and_wrapped_calls(self):
        # Geocode and reverse must share one clock, not independent limiters.
        self.assertEqual(self.pacing.call_count, 1)
        self.assertTrue(callable(self.pacing.call_args.args[0]))
        for call in self.pacing.call_args_list:
            self.assertEqual(call.kwargs["min_delay_seconds"], 1.1)
            self.assertEqual(call.kwargs["max_retries"], 0)
            self.assertIs(call.kwargs["swallow_exceptions"], False)
        self.client.geocode("missing")
        self.client.reverse_geocode(42, -71)
        self.locator.geocode.assert_called_once()
        self.locator.reverse.assert_called_once()
        self.assertEqual(self.wrappers[0].call_count, 2)
        self.assertEqual([call.args[0] for call in self.wrappers[0].call_args_list],
                         ["geocode", "reverse"])

    def test_geocode_candidates_boundary_and_bounded_search(self):
        boundary = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [0, 1], [0, 0]]]}
        self.locator.geocode.return_value = [place(geojson=boundary), place("Other", importance=0.4)]
        box = [(41, -72), (43, -70)]
        found = self.client.geocode("Example", polygon=True, limit=9, viewbox=box)
        self.assertEqual(found["boundary"], boundary)
        self.assertEqual(found["alternatives"][0]["address"], "Other")
        kwargs = self.locator.geocode.call_args.kwargs
        self.assertEqual(kwargs["limit"], 9)
        self.assertFalse(kwargs["exactly_one"])
        self.assertTrue(kwargs["bounded"])
        self.assertEqual(kwargs["viewbox"], box)
        self.assertEqual(kwargs["geometry"], "geojson")

    def test_service_failures_are_geo_errors(self):
        self.locator.geocode.side_effect = RuntimeError("unavailable")
        self.locator.reverse.side_effect = RuntimeError("unavailable")
        for function, args in [(self.client.geocode, ("Example",)),
                               (self.client.reverse_geocode, (42, -71))]:
            with self.subTest(function=function.__name__), self.assertRaises(geo.GeoError):
                function(*args)

    def test_coordinate_parsing_and_validation(self):
        for text in ["-33.87,151.21", " -33.87 151.21 ", "-33.87, 151.21"]:
            self.assertEqual(self.client.parse_location(text), (-33.87, 151.21, None))
        self.locator.geocode.assert_not_called()
        self.assertEqual(len(self.client.coordinate_errors(91, 181)), 2)
        self.assertEqual(self.client.coordinate_errors(0, 0), [])
        self.assertTrue(self.client.coordinate_errors(float("nan"), 0))
        with self.assertRaises(geo.GeoError):
            self.client.parse_location("91,181")

    def test_geodesic_distance_and_bearings(self):
        distance = self.client.calculate_distance(0, 0, 0, 1)
        self.assertAlmostEqual(distance["distance"]["kilometers"], 111.319, places=2)
        for lat, lng, bearing in [(1, 0, 0), (0, 1, 90), (-1, 0, 180), (0, -1, 270)]:
            self.assertEqual(self.client.calculate_bearing(0, 0, lat, lng), bearing)
        self.assertEqual(self.client.calculate_distance(42, -71, 42, -71)["distance"]["meters"], 0)

    def test_bounds(self):
        bounds = self.client.calculate_bounds(42, -71, 10)
        self.assertEqual(set(bounds), {"north", "south", "east", "west"})
        self.assertLess(bounds["south"], 42)
        self.assertGreater(bounds["north"], 42)
        self.assertLess(bounds["west"], -71)
        self.assertGreater(bounds["east"], -71)

    def test_route_summary_steps_and_http_errors(self):
        response = self.session.post.return_value
        response.json.return_value = {
            "trip": {
                "summary": {"length": 2, "time": 120, "has_toll": True},
                "legs": [{
                    "summary": {"length": 2, "time": 120},
                    "maneuvers": [
                        {"instruction": "Head east", "length": 1, "time": 60},
                        {"instruction": "Arrive", "length": 1, "time": 60},
                    ],
                }],
            },
        }
        result = self.client.route([(0, 0), (0, 1)], mode="cycling")
        self.assertEqual(result["distance"]["kilometers"], 2)
        self.assertEqual(result["duration_seconds"], 120)
        self.assertTrue(result["has_toll"])
        self.assertEqual(result["legs"][0]["steps"][-1]["cumulative_distance"]["kilometers"], 2)
        self.assertEqual(self.session.post.call_args.kwargs["json"]["costing"], "bicycle")
        response.raise_for_status.assert_called_once()
        summary = self.client.route([(0, 0), (0, 1)], steps=False)
        self.assertEqual(summary["legs"][0]["steps"], [])
        response.raise_for_status.side_effect = geo.requests.HTTPError("503")
        with self.assertRaises(geo.GeoError):
            self.client.route([(0, 0), (0, 1)])

    def test_route_geojson_marks_each_maneuver(self):
        self.session.post.return_value.json.return_value = {
            "trip": {
                "summary": {"length": 2, "time": 120, "has_highway": True},
                "legs": [{
                    "summary": {"length": 2, "time": 120},
                    "shape": "encoded",
                    "maneuvers": [
                        {"instruction": "Head east", "length": 1, "time": 60,
                         "begin_shape_index": 0},
                        {"instruction": "Take exit 2D", "length": 1, "time": 60,
                         "begin_shape_index": 1, "toll": True,
                         "sign": {"exit_number_elements": [{"text": "2D"}]}},
                        {"instruction": "Arrive", "length": 0, "time": 0,
                         "begin_shape_index": 9},
                    ],
                }],
            },
        }
        with patch.object(geo, "_decode_polyline", return_value=[(0, 0), (0, 0.5), (0, 1)]):
            code, out, err = self.cli("route", "--from", "0,0", "--to", "0,1", "--geojson")
        self.assertEqual(code, 0, err)
        features = json.loads(out)["features"]
        self.assertEqual(features[0]["properties"]["uses"], "highways")
        turns = [f for f in features if f["properties"].get("marker-size") == "small"]
        # The out-of-range shape index is skipped rather than guessed
        self.assertEqual([f["geometry"]["coordinates"] for f in turns], [[0, 0], [0.5, 0]])
        self.assertEqual(turns[1]["properties"]["title"], "Take exit 2D")
        self.assertEqual(turns[1]["properties"]["toll"], "yes")

    def test_invalid_calculation_numbers(self):
        for radius in [0, -1, float("nan"), float("inf")]:
            with self.subTest(radius=radius), self.assertRaises(geo.GeoError):
                self.client.calculate_bounds(0, 0, radius)

    def test_polyline_precision_and_coordinate_order(self):
        encoded = "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
        expected = [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]
        self.assertEqual(geo._decode_polyline(encoded, precision=5), expected)
        self.assertEqual(geo._decode_polyline(encoded), [(3.85, -12.02), (4.07, -12.095), (4.3252, -12.6453)])
        self.assertEqual(geo._decode_polyline(""), [])
        self.assertEqual(geo._geojson_line(expected)["geometry"]["coordinates"][0], [-120.2, 38.5])

    def test_unit_aliases_and_round_trip(self):
        aliases = [("mi", "miles"), ("km", "kilometers"), ("m", "meters"),
                   ("ft", "feet"), ("nm", "nautical")]
        units = geo._distance_units(10)
        for short, long in aliases:
            with self.subTest(unit=short):
                self.assertEqual(geo._to_kilometers(2, short), geo._to_kilometers(2, long))
                self.assertEqual(geo._format_distance(units, short), geo._format_distance(units, long))
        self.assertAlmostEqual(geo._to_kilometers(1, "miles"), 1.609344, places=5)
        self.assertEqual(geo._to_kilometers(1000, "m"), 1)
        self.assertAlmostEqual(geo._to_kilometers(1, "nm"), 1.852)
        self.assertEqual(units["meters"], 10000)


class CommandTests(OfflineCase):
    def test_removed_commands_are_not_public_cli(self):
        self.assert_failure("bbox", "--center", "42,-71", "--radius", "1")
        self.assert_failure("ip", "8.8.8.8")
        self.assert_failure("validate", "40.7128,-74.0060")
        self.assert_failure("destination", "--start", "0,0", "--bearing", "90", "--distance", "1")

    def test_map_loads_valid_data_without_network(self):
        data = {"type": "Point", "coordinates": [1, 2]}
        with patch.object(geo.sys, "stdin", io.StringIO(json.dumps(data))), \
                patch.object(geo, "_serve_map", return_value=0) as serve:
            code, _, err = self.cli("interact", "--geojson", "-", "--no-open")
        self.assertEqual(code, 0, err)
        page, collection, port, open_browser = serve.call_args.args
        self.assertIn("L.map", page)
        self.assertNotIn("__CENTER__", page)
        self.assertEqual(collection["features"][0]["geometry"], data)
        self.assertNotIn("script.js", page)
        self.assertEqual(port, 8765)
        self.assertFalse(open_browser)

    def test_geocode_limit_fetches_enough_candidates(self):
        for limit in [1, 3, 12]:
            with self.subTest(limit=limit), patch.object(self.client, "geocode", return_value=result()) as fetch:
                code, out, err = self.cli("geocode", "Example", "--limit", str(limit), "--json")
                self.assertEqual(code, 0, err)
                self.assertEqual(json.loads(out)["address"], "Example")
                self.assertEqual(fetch.call_args.kwargs["limit"], max(geo.CANDIDATE_LIMIT, limit))

    def test_nonpositive_limit_rejected_before_network(self):
        with patch.object(self.client, "geocode") as fetch:
            for limit in ["0", "-1"]:
                with self.subTest(limit=limit):
                    self.assert_failure("geocode", "Example", "--limit", limit)
            fetch.assert_not_called()

    def test_invalid_simplify_rejected_before_network(self):
        with patch.object(self.client, "geocode") as fetch:
            for tolerance in ["-1", "nan", "inf"]:
                with self.subTest(tolerance=tolerance):
                    self.assert_failure("geocode", "Example", "--simplify", tolerance)
            fetch.assert_not_called()

    def test_near_preserves_bounded_viewbox(self):
        with patch.object(self.client, "geocode", return_value=result()) as fetch:
            code, _, err = self.cli("geocode", "Main St", "--near", "-33.87,151.21",
                                    "--within", "10", "--unit", "km", "--json")
            self.assertEqual(code, 0, err)
            box = fetch.call_args.kwargs["viewbox"]
            self.assertEqual(len(box), 2)
            self.assertLess(box[0][0], -33.87)
            self.assertGreater(box[1][0], -33.87)
            self.assertLess(box[0][1], 151.21)
            self.assertGreater(box[1][1], 151.21)

    def test_expected_type_matches_any_classification(self):
        for expected in ["CITY", "building,place", "peak, city"]:
            with self.subTest(expected=expected), patch.object(self.client, "geocode", return_value=result()):
                code, _, err = self.cli("geocode", "Example", "--expect-type", expected, "--json")
                self.assertEqual(code, 0, err)
        with patch.object(self.client, "geocode", return_value=result()):
            self.assert_failure("geocode", "Example", "--expect-type", "administrative")

    def test_ambiguity_warning_keeps_json_clean(self):
        found = result()
        found["alternatives"] = [{"address": "Other", "latitude": 43, "longitude": -72,
                                  "type": "town", "class": "place", "importance": 0.75}]
        with patch.object(self.client, "geocode", return_value=found):
            code, out, err = self.cli("geocode", "Example", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["address"], "Example")
        self.assertIn("ambiguous", err)

    def test_geojson_precedes_json(self):
        with patch.object(self.client, "geocode", return_value=result()):
            code, out, err = self.cli("geocode", "Example", "--json", "--geojson")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["type"], "Feature")
        self.assertEqual(json.loads(out)["geometry"]["coordinates"], [-71, 42])
        code, out, err = self.cli("distance", "--from", "0,0", "--to", "0,1", "--json", "--geojson")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["type"], "FeatureCollection")

    def test_southern_positional_and_option_coordinates(self):
        self.locator.reverse.return_value = place("Sydney", -33.87, 151.21)
        code, out, err = self.cli("reverse", "-33.87,151.21", "--json")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["latitude"], -33.87)
        code, out, err = self.cli("distance", "--from", "-33.87,151.21", "--to", "-34,151", "--json")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["from"]["latitude"], -33.87)

    def test_failures_only_write_stderr(self):
        cases = [("geocode", "Missing"), ("reverse", "91,181"), ("reverse", "42,-71"),
                 ("distance", "--from", "91,181", "--to", "0,0"),
                 ("route", "--from", "91,181", "--to", "0,0"),
                 ("reverse", "not coordinates")]
        for argv in cases:
            with self.subTest(argv=argv):
                self.assert_failure(*argv)
        with patch.object(geo.sys, "stdin", io.StringIO('{"type":"Bogus"}')), \
                patch.object(geo, "_serve_map") as serve:
            self.assert_failure("interact", "--geojson", "-", "--no-open")
            serve.assert_not_called()


class GeometryTests(OfflineCase):
    def load(self, value):
        with patch.object(geo.sys, "stdin", io.StringIO(json.dumps(value))):
            return geo._load_geojson_inputs(["-"])

    def test_simplification_never_degenerates_polygon_rings(self):
        outer = [[0, 0], [1, 0], [2, 0], [2, 2], [0, 2], [0, 0]]
        hole = [[0.2, 0.2], [0.3, 0.2], [0.3, 0.3], [0.2, 0.2]]
        for kind, coordinates in [("Polygon", [outer, hole]), ("MultiPolygon", [[outer, hole]])]:
            original = {"type": kind, "coordinates": coordinates}
            before = copy.deepcopy(original)
            for tolerance in [0.01, 100]:
                with self.subTest(kind=kind, tolerance=tolerance):
                    simplified = geo._simplify_geometry(original, tolerance)
                    rings = simplified["coordinates"] if kind == "Polygon" else simplified["coordinates"][0]
                    for ring in rings:
                        self.assertEqual(ring[0], ring[-1])
                        self.assertGreaterEqual(len(ring), 4)
                        self.assertGreaterEqual(len(set(map(tuple, ring))), 3)
                    if tolerance == 100:
                        self.assertEqual(simplified, original)
            self.assertEqual(original, before)

    def test_simplification_retains_other_geometry_and_zero_tolerance(self):
        point = {"type": "Point", "coordinates": [1, 2]}
        self.assertEqual(geo._simplify_geometry(point, 1), point)
        polygon = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [0, 1], [0, 0]]]}
        self.assertEqual(geo._simplify_geometry(polygon, 0), polygon)

    def test_all_geometry_types_and_feature_wrappers(self):
        ring = [[0, 0], [1, 0], [0, 1], [0, 0]]
        geometries = [
            {"type": "Point", "coordinates": [1, 2, 3]},
            {"type": "MultiPoint", "coordinates": [[1, 2], [3, 4]]},
            {"type": "LineString", "coordinates": [[1, 2], [3, 4]]},
            {"type": "MultiLineString", "coordinates": [[[1, 2], [3, 4]]]},
            {"type": "Polygon", "coordinates": [ring]},
            {"type": "MultiPolygon", "coordinates": [[ring]]},
            {"type": "GeometryCollection", "geometries": [{"type": "Point", "coordinates": [1, 2]}]},
        ]
        for geometry in geometries:
            with self.subTest(kind=geometry["type"]):
                features = self.load(geometry)
                self.assertEqual(features[0]["geometry"], geometry)
                feature = {"type": "Feature", "geometry": geometry, "properties": {"title": "Example"}}
                self.assertEqual(self.load(feature), [feature])
                self.assertEqual(self.load({"type": "FeatureCollection", "features": [feature]}), [feature])

    def test_malformed_geojson_rejected(self):
        invalid = [
            None, [], {}, {"type": "Bogus", "coordinates": [1, 2]},
            {"type": ["Point"], "coordinates": [1, 2]},
            {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [0, 1]]]},
            {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 0], [0, 0]]]},
            {"type": "Feature", "geometry": None},
            {"type": "Feature", "geometry": None, "properties": []},
            {"type": "FeatureCollection", "features": [123]},
            {"type": "FeatureCollection", "features": {}},
            {"type": "Feature", "geometry": {"type": "Bogus"}, "properties": {}},
            {"type": "Point", "coordinates": [1]},
            {"type": "Point", "coordinates": ["1", 2]},
            {"type": "Point", "coordinates": [True, 2]},
            {"type": "Point", "coordinates": [float("nan"), 2]},
            {"type": "Point", "coordinates": [10 ** 400, 2]},
            {"type": "Point", "coordinates": [1, float("inf")]},
            {"type": "Point", "coordinates": [[1, 2], [3, 4]]},
            {"type": "LineString", "coordinates": [1, 2]},
            {"type": "Polygon", "coordinates": [[1, 2]]},
            {"type": "GeometryCollection", "geometries": [123]},
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(geo.GeoError):
                self.load(value)

    def test_invalid_json_and_missing_file_rejected(self):
        with patch.object(geo.sys, "stdin", io.StringIO("not json")), self.assertRaises(geo.GeoError):
            geo._load_geojson_inputs(["-"])
        with patch("builtins.open", side_effect=FileNotFoundError("missing")), self.assertRaises(geo.GeoError):
            geo._load_geojson_inputs(["missing.geojson"])


if __name__ == "__main__":
    unittest.main()
