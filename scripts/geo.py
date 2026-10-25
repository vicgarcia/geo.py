#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "geopy>=2.4.0",
#     "requests>=2.31.0",
# ]
# ///
"""
geo.py - A CLI tool for working with latitude/longitude and geography.

Supports geocoding, reverse geocoding, distance calculations, and more.
Designed for agents and humans who need to work with geographic data.
"""

import argparse
import http.server
import json
import math
import re
import sys
import webbrowser
from typing import Optional

import requests
from geopy.extra.rate_limiter import RateLimiter
from geopy.geocoders import Nominatim
from geopy.distance import geodesic
from geopy.point import Point

# User agent for Nominatim (required)
USER_AGENT = "geo.py-cli/1.0"

# How many candidates to pull back so ambiguity is detectable. Same single
# request either way, so this costs nothing against the rate limit.
CANDIDATE_LIMIT = 5

# Nominatim scores every match with an "importance". When the runner-up scores
# this close to the winner the query is a genuine toss-up, not a clear hit.
AMBIGUITY_RATIO = 0.85

# "lat,lng" or "lat lng". Range checks happen after parsing, in coordinate_errors().
COORDINATE_PATTERN = re.compile(r"^([-+]?\d+(?:\.\d*)?)\s*[,\s]\s*([-+]?\d+(?:\.\d*)?)$")

CLI_EPILOG = """\
Examples:
  geo.py geocode "1600 Pennsylvania Ave, Washington DC"
  geo.py geocode "Tokyo, Japan" --json
  geo.py geocode "Washington, USA" --limit 5
  geo.py geocode "134 Angell St" --near "Providence, RI" --within 10
  geo.py geocode "Washington State, USA" --expect-type administrative
  geo.py geocode "Colorado" --geojson --polygon
  geo.py geocode "Switzerland" --geojson --polygon --simplify 0.01

  geo.py reverse 40.7128,-74.0060
  geo.py reverse "51.5074, -0.1278"

  geo.py distance --from "New York" --to "Los Angeles"
  geo.py distance --from "40.7128,-74.0060" --to "34.0522,-118.2437" --unit km

  geo.py route --from "Seattle" --to "Portland, OR"
  geo.py route --from "Times Square" --to "Central Park" --mode walking
  geo.py route --from "Boston" --to "NYC" --via "Hartford, CT" --json

  geo.py interact --center "Seattle" --zoom 12
  geo.py interact --marker "Space Needle:Start here" --marker "Pike Place Market"
  geo.py route --from "Seattle" --to "Portland, OR" --geojson | geo.py interact --geojson -
  geo.py interact --center "Zermatt" --tiles satellite --zoom 14
  geo.py interact --center "Manhattan" --tiles satellite

Smart Location Parsing:
  Most commands accept either raw coordinates or addresses:
    "40.7128,-74.0060"     Raw lat,lng (comma or space separated)
    "40.7128 -74.0060"     Raw lat lng (space separated)
    "New York City"        Address (auto-geocoded)
    "Tokyo, Japan"         Address with region

Basemap Tiles (interact):
  osm           OpenStreetMap standard (default)
  satellite     Esri World Imagery
  light         Esri Light Gray Canvas
  dark          Esri Dark Gray Canvas

Travel Modes (route):
  driving       Car (default)
  walking       Pedestrian
  cycling       Bicycle
  motorcycle    Motorcycle
  truck         Truck

Distance Units:
  mi, miles     Miles (default)
  km            Kilometers
  m, meters     Meters
  ft, feet      Feet
  nm            Nautical miles
"""


# Keyless raster tile providers, each under its own fair-use policy - keep traffic
# light. Private use only, so the map shows no attribution bar.
TILE_LAYERS = {
    "osm": {
        "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        "max_zoom": 19,
    },
    "satellite": {
        "url": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "max_zoom": 19,
    },
    "light": {
        "url": "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}",
        "max_zoom": 16,
    },
    "dark": {
        "url": "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}",
        "max_zoom": 16,
    },
}

MAP_PAGE = """\
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>geo.py</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  html, body, #map { height: 100%; margin: 0; }
  .leaflet-popup-content { font: 13px/1.45 system-ui, sans-serif; }
  .leaflet-popup-content dt { font-weight: 600; margin-top: 4px; }
  .leaflet-popup-content dd { margin: 0; }
</style>
</head>
<body>
<div id="map"></div>
<script>
const CENTER = __CENTER__;
const ZOOM = __ZOOM__;

const map = L.map('map', { attributionControl: false });
L.tileLayer(__TILE_URL__, {
  maxZoom: __TILE_MAX_ZOOM__
}).addTo(map);

let dataLayer = null;

function esc(value) {
  return String(value).replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

// simplestyle-spec: https://github.com/mapbox/simplestyle-spec
function styleFor(feature) {
  const p = feature.properties || {};
  const geometry = feature.geometry || {};
  // Leaflet applies style() to pointToLayer results too, so honour marker-color
  // here or it gets overwritten with the line defaults below.
  if (geometry.type && geometry.type.indexOf('Point') !== -1 && p['marker-color']) {
    return { color: '#fff', weight: 2, fillColor: p['marker-color'], fillOpacity: 1 };
  }
  return {
    color: p['stroke'] || '#3388ff',
    weight: p['stroke-width'] || 4,
    opacity: p['stroke-opacity'] !== undefined ? p['stroke-opacity'] : 0.9,
    fillColor: p['fill'] || p['stroke'] || '#3388ff',
    fillOpacity: p['fill-opacity'] !== undefined ? p['fill-opacity'] : 0.2
  };
}

function markerFor(feature, latlng) {
  const p = feature.properties || {};
  const color = p['marker-color'];
  const sizes = { small: 6, medium: 8, large: 11 };
  if (!color) return L.marker(latlng);
  return L.circleMarker(latlng, {
    radius: sizes[p['marker-size']] || 8,
    color: '#fff', weight: 2, fillColor: color, fillOpacity: 1
  });
}

const STYLE_KEYS = /^(stroke|fill|marker-)/;

function popupFor(feature, layer) {
  const p = feature.properties || {};
  const parts = [];
  if (p.title) parts.push('<strong>' + esc(p.title) + '</strong>');
  if (p.description) parts.push(esc(p.description));

  const extra = Object.keys(p)
    .filter(k => k !== 'title' && k !== 'description' && !STYLE_KEYS.test(k));
  if (extra.length) {
    parts.push('<dl>' + extra
      .map(k => '<dt>' + esc(k) + '</dt><dd>' + esc(p[k]) + '</dd>').join('') + '</dl>');
  }
  if (parts.length) layer.bindPopup(parts.join('<br>'));
}

function frame() {
  const bounds = dataLayer && dataLayer.getBounds();
  if (CENTER) {
    map.setView(CENTER, ZOOM === null ? 13 : ZOOM);
  } else if (bounds && bounds.isValid()) {
    map.fitBounds(bounds, { padding: [40, 40] });
    if (ZOOM !== null) map.setZoom(ZOOM);
  } else {
    map.setView([0, 0], ZOOM === null ? 2 : ZOOM);
  }
}

fetch('data.json')
  .then(r => r.json())
  .then(data => {
    if (data.features && data.features.length) {
      dataLayer = L.geoJSON(data, {
        style: styleFor, pointToLayer: markerFor, onEachFeature: popupFor
      }).addTo(map);
    }
    frame();
  })
  .catch(err => console.error('geo.py: failed to load data.json', err));
</script>
</body>
</html>
"""


class GeoError(Exception):
    """Base exception for geo.py errors."""
    pass


class GeoClient:
    """Client for geographic operations using geopy and external services."""

    # Public Valhalla instance run by FOSSGIS - no API key or account needed
    ROUTING_URL = "https://valhalla1.openstreetmap.de/route"

    # Travel mode -> Valhalla costing model
    COSTING_MODES = {
        "driving": "auto",
        "walking": "pedestrian",
        "cycling": "bicycle",
        "motorcycle": "motorcycle",
        "truck": "truck",
    }

    def __init__(self):
        self.geolocator = Nominatim(user_agent=USER_AGENT, timeout=10)
        # Share one pacing clock between forward and reverse lookups.
        self._lookup = RateLimiter(
            self._nominatim_lookup, min_delay_seconds=1.1,
            max_retries=0, swallow_exceptions=False,
        )
        self._session = requests.Session()

    def _nominatim_lookup(self, operation: str, query: str, **kwargs):
        return getattr(self.geolocator, operation)(query, **kwargs)

    def _looks_like_coordinates(self, location: str) -> bool:
        """Check if a string looks like lat,lng coordinates."""
        return bool(COORDINATE_PATTERN.match(location.strip()))

    def _parse_coordinates(self, location: str) -> tuple[float, float]:
        """Parse a coordinate string into (lat, lng) tuple."""
        match = COORDINATE_PATTERN.match(location.strip())
        if not match:
            raise GeoError(f"Could not parse coordinates: {location}")

        lat, lng = float(match.group(1)), float(match.group(2))
        return lat, lng

    def coordinate_errors(self, lat: float, lng: float) -> list[str]:
        """List the reasons a lat/lng pair is out of range; empty when valid."""
        errors = []
        if not math.isfinite(lat) or lat < -90 or lat > 90:
            errors.append(f"Latitude {lat} out of range [-90, 90]")
        if not math.isfinite(lng) or lng < -180 or lng > 180:
            errors.append(f"Longitude {lng} out of range [-180, 180]")
        return errors

    def parse_location(self, location: str) -> tuple[float, float, Optional[str]]:
        """
        Smart location parsing - accepts coordinates or addresses.

        Returns: (lat, lng, resolved_address or None)
        """
        location = location.strip()

        # Try to parse as coordinates first
        if self._looks_like_coordinates(location):
            lat, lng = self._parse_coordinates(location)
            errors = self.coordinate_errors(lat, lng)
            if errors:
                raise GeoError(f"Invalid coordinates: {', '.join(errors)}")
            return lat, lng, None

        # Otherwise, geocode as address
        result = self.geocode(location)
        if not result:
            raise GeoError(f"Could not geocode address: {location}")

        _warn_if_ambiguous(result, location)

        return result["latitude"], result["longitude"], result["address"]

    def geocode(
        self,
        address: str,
        polygon: bool = False,
        limit: int = CANDIDATE_LIMIT,
        viewbox: Optional[list] = None,
    ) -> Optional[dict]:
        """
        Geocode an address to coordinates.

        With polygon=True, also ask Nominatim for the administrative boundary
        geometry, returned under "boundary" when the place has one.

        Runners-up come back under "alternatives" so callers can see whether the
        query was ambiguous. viewbox restricts the search to [(lat, lng), (lat, lng)].

        Returns dict with lat, lng, address, alternatives, and raw response.
        """
        try:
            kwargs = {"addressdetails": True}
            if polygon:
                kwargs["geometry"] = "geojson"
            if viewbox:
                kwargs["viewbox"] = viewbox
                kwargs["bounded"] = True
            if limit > 1:
                kwargs["exactly_one"] = False
                kwargs["limit"] = limit

            found = self._lookup("geocode", address, **kwargs)
            if not found:
                return None

            if limit > 1:
                found = list(found)
                if not found:
                    return None
                location, runners_up = found[0], found[1:]
            else:
                location, runners_up = found, []

            return {
                "latitude": location.latitude,
                "longitude": location.longitude,
                "address": location.address,
                "boundary": location.raw.get("geojson") if polygon else None,
                "alternatives": [
                    {
                        "latitude": alt.latitude,
                        "longitude": alt.longitude,
                        "address": alt.address,
                        "type": alt.raw.get("type"),
                        "class": alt.raw.get("class"),
                        "importance": float(alt.raw.get("importance") or 0),
                    }
                    for alt in runners_up
                ],
                "raw": location.raw,
            }
        except Exception as e:
            raise GeoError(f"Geocoding failed: {e}")

    def reverse_geocode(self, lat: float, lng: float) -> Optional[dict]:
        """
        Reverse geocode coordinates to an address.

        Returns dict with address and location details.
        """
        try:
            location = self._lookup("reverse", f"{lat}, {lng}", addressdetails=True)
            if not location:
                return None

            raw = location.raw
            address_parts = raw.get("address", {})

            return {
                "latitude": lat,
                "longitude": lng,
                "address": location.address,
                "components": {
                    "house_number": address_parts.get("house_number"),
                    "road": address_parts.get("road"),
                    "neighbourhood": address_parts.get("neighbourhood"),
                    "suburb": address_parts.get("suburb"),
                    "city": address_parts.get("city") or address_parts.get("town") or address_parts.get("village"),
                    "county": address_parts.get("county"),
                    "state": address_parts.get("state"),
                    "postcode": address_parts.get("postcode"),
                    "country": address_parts.get("country"),
                    "country_code": address_parts.get("country_code"),
                },
                "raw": raw,
            }
        except Exception as e:
            raise GeoError(f"Reverse geocoding failed: {e}")

    def calculate_distance(
        self,
        from_lat: float,
        from_lng: float,
        to_lat: float,
        to_lng: float,
    ) -> dict:
        """Calculate the geodesic (WGS-84 ellipsoid) distance between two points."""
        dist = geodesic((from_lat, from_lng), (to_lat, to_lng))

        return {
            "from": {"latitude": from_lat, "longitude": from_lng},
            "to": {"latitude": to_lat, "longitude": to_lng},
            "distance": {
                "miles": round(dist.miles, 3),
                "kilometers": round(dist.km, 3),
                "meters": round(dist.m, 3),
                "feet": round(dist.feet, 3),
                "nautical_miles": round(dist.nm, 3),
            },
        }

    def calculate_bearing(self, from_lat: float, from_lng: float, to_lat: float, to_lng: float) -> float:
        """Calculate initial bearing from point1 to point2 in degrees."""
        import math

        lat1 = math.radians(from_lat)
        lat2 = math.radians(to_lat)
        diff_lng = math.radians(to_lng - from_lng)

        x = math.sin(diff_lng) * math.cos(lat2)
        y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(diff_lng)

        initial_bearing = math.atan2(x, y)
        initial_bearing = math.degrees(initial_bearing)
        bearing = (initial_bearing + 360) % 360

        return round(bearing, 2)

    def calculate_bounds(
        self, center_lat: float, center_lng: float, radius_km: float
    ) -> dict:
        """Calculate cardinal bounds for a bounded geocoding search."""
        if not math.isfinite(radius_km) or radius_km <= 0:
            raise GeoError("Search radius must be a positive finite distance")
        center = Point(center_lat, center_lng)
        distance = geodesic(kilometers=radius_km)
        return {
            "north": distance.destination(center, 0).latitude,
            "south": distance.destination(center, 180).latitude,
            "east": distance.destination(center, 90).longitude,
            "west": distance.destination(center, 270).longitude,
        }

    def route(
        self,
        waypoints: list[tuple[float, float]],
        mode: str = "driving",
        steps: bool = True
    ) -> dict:
        """
        Get turn-by-turn directions along a list of waypoints.

        waypoints: [(lat, lng), ...] in travel order, at least two.
        mode: one of COSTING_MODES.
        """
        costing = self.COSTING_MODES.get(mode)
        if not costing:
            raise GeoError(
                f"Unknown travel mode: {mode} "
                f"(choose from {', '.join(sorted(self.COSTING_MODES))})"
            )
        if len(waypoints) < 2:
            raise GeoError("Routing needs at least a start and an end point")

        payload = {
            "locations": [{"lat": lat, "lon": lng} for lat, lng in waypoints],
            "costing": costing,
            "units": "kilometers",
        }

        try:
            response = self._session.post(
                self.ROUTING_URL,
                json=payload,
                headers={"User-Agent": USER_AGENT},
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as e:
            raise GeoError(f"Routing request failed: {e}")
        except ValueError:
            raise GeoError("Routing service returned an invalid response")

        if "error" in data:
            raise GeoError(f"Routing failed: {data['error']}")

        trip = data.get("trip") or {}
        if not trip.get("legs"):
            raise GeoError("No route found between these locations")

        legs = []
        traveled = 0.0
        for leg in trip["legs"]:
            maneuvers = []
            shape = _decode_polyline(leg["shape"]) if leg.get("shape") else []
            raw_maneuvers = leg.get("maneuvers", [])
            for index, m in enumerate(raw_maneuvers):
                traveled += m.get("length", 0.0)
                if not steps:
                    continue
                following = raw_maneuvers[index + 1] if index + 1 < len(raw_maneuvers) else None
                # Where the maneuver begins, as an index into the leg's shape
                position = m.get("begin_shape_index")
                location = shape[position] if position is not None and position < len(shape) else None
                maneuvers.append({
                    "instruction": m.get("instruction", ""),
                    "location": list(location) if location else None,
                    "street": ", ".join(m.get("street_names") or []) or None,
                    "toward": _step_toward(m, following),
                    "exit": _step_exit(m),
                    "toll": bool(m.get("toll")),
                    "distance": _distance_units(m.get("length", 0.0)),
                    "cumulative_distance": _distance_units(traveled),
                    "duration_seconds": round(m.get("time", 0.0)),
                })
            legs.append({
                "distance": _distance_units(leg["summary"]["length"]),
                "duration_seconds": round(leg["summary"]["time"]),
                "shape": shape,
                "steps": maneuvers,
            })

        summary = trip["summary"]
        return {
            "mode": mode,
            "distance": _distance_units(summary["length"]),
            "duration_seconds": round(summary["time"]),
            "has_toll": summary.get("has_toll", False),
            "has_ferry": summary.get("has_ferry", False),
            "has_highway": summary.get("has_highway", False),
            "legs": legs,
        }


def _bearing_to_cardinal(bearing: float) -> str:
    """Convert bearing in degrees to cardinal direction."""
    directions = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
                  "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    index = round(bearing / 22.5) % 16
    return directions[index]


def _format_distance(distance: dict, unit: str) -> str:
    """Format distance dict to string with specified unit."""
    unit_map = {
        "mi": ("miles", "mi"),
        "miles": ("miles", "mi"),
        "km": ("kilometers", "km"),
        "kilometers": ("kilometers", "km"),
        "m": ("meters", "m"),
        "meters": ("meters", "m"),
        "ft": ("feet", "ft"),
        "feet": ("feet", "ft"),
        "nm": ("nautical_miles", "nm"),
        "nautical": ("nautical_miles", "nm"),
    }

    key, abbrev = unit_map.get(unit.lower(), ("miles", "mi"))
    value = distance[key]
    return f"{value:,.3f} {abbrev}"


def _distance_units(kilometers: float) -> dict:
    """Expand a distance in kilometers into the unit dict used everywhere else."""
    meters = kilometers * 1000
    return {
        "miles": round(kilometers * 0.621371, 3),
        "kilometers": round(kilometers, 3),
        "meters": round(meters, 3),
        "feet": round(meters * 3.280839895, 3),
        "nautical_miles": round(kilometers * 0.539957, 3),
    }


def _mentions_street(instruction: str, street: str) -> bool:
    """
    Check whether an instruction already names a street.

    Compares against the name minus any trailing direction suffix, so
    "onto SR 520" counts as already naming "SR 520 East".
    """
    if street in instruction:
        return True

    base = re.sub(r"\s+(North|South|East|West|N|S|E|W)$", "", street)
    return bool(base) and base in instruction


def _step_toward(maneuver: dict, following: Optional[dict]) -> Optional[str]:
    """
    Name the street a step puts you on, when the instruction text doesn't already.

    Valhalla omits the street for unnamed segments (driveways, service roads), which
    leaves bare instructions like "Drive northeast." Naming the road the next maneuver
    happens on gives the step something to aim at.
    """
    instruction = maneuver.get("instruction", "")
    streets = maneuver.get("street_names") or maneuver.get("begin_street_names") or []

    for street in streets:
        if not _mentions_street(instruction, street):
            return street
    if streets:
        return None

    # No street of its own - borrow the next maneuver's, as a "toward" hint
    for street in (following or {}).get("street_names") or []:
        if not _mentions_street(instruction, street):
            return street
    return None


def _step_exit(maneuver: dict) -> Optional[str]:
    """Extract the exit number from a maneuver's signage, if it has one."""
    elements = (maneuver.get("sign") or {}).get("exit_number_elements") or []
    instruction = maneuver.get("instruction", "")
    numbers = [
        e["text"] for e in elements
        if e.get("text") and e["text"] not in instruction
    ]
    return ", ".join(numbers) or None


def _format_duration(seconds: float) -> str:
    """Format a duration in seconds as a human-readable string."""
    seconds = int(round(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)

    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m"
    return f"{secs}s"


def _decode_polyline(encoded: str, precision: int = 6) -> list[tuple[float, float]]:
    """
    Decode an encoded polyline into [(lat, lng), ...].

    Valhalla encodes route shapes at precision 6, unlike the Google/OSRM
    default of 5.
    """
    factor = 10 ** precision
    coordinates = []
    index = lat = lng = 0

    while index < len(encoded):
        for is_latitude in (True, False):
            shift = result = 0
            while True:
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1f) << shift
                shift += 5
                if byte < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if is_latitude:
                lat += delta
            else:
                lng += delta
        coordinates.append((lat / factor, lng / factor))

    return coordinates


def _viewbox_around(client: "GeoClient", near: str, within: float, unit: str) -> list:
    """
    Build a Nominatim viewbox around a reference location.

    Free-form search will happily match a same-named street in the wrong town;
    constraining the search area is the reliable fix.
    """
    lat, lng, _ = client.parse_location(near)
    radius_km = _to_kilometers(within, unit)
    bounds = client.calculate_bounds(lat, lng, radius_km)

    return [(bounds["south"], bounds["west"]), (bounds["north"], bounds["east"])]


def _to_kilometers(value: float, unit: str) -> float:
    """Convert a distance in the given unit to kilometres."""
    unit = unit.lower()
    if unit in ("km", "kilometers"):
        return value
    if unit in ("m", "meters"):
        return value / 1000
    if unit in ("ft", "feet"):
        return value * 0.0003048
    if unit in ("nm", "nautical"):
        return value * 1.852
    return value * 1.60934  # miles


def _ambiguous_alternative(result: dict) -> Optional[dict]:
    """
    Return the runner-up when it is close enough to be a real toss-up.

    Nominatim happily returns the same place twice, so identical addresses are
    skipped rather than reported as competing answers.
    """
    top = float((result.get("raw") or {}).get("importance") or 0)
    if not top:
        return None

    for alt in result.get("alternatives") or []:
        if alt["address"] == result["address"]:
            continue
        return alt if alt["importance"] / top >= AMBIGUITY_RATIO else None

    return None


def _place_kinds(result: dict) -> set:
    """Nominatim's own classification of what kind of place matched."""
    raw = result.get("raw") or {}
    return {str(raw.get(key)).lower() for key in ("type", "class", "addresstype")
            if raw.get(key)}


def _warn_if_ambiguous(result: dict, query: str) -> None:
    """Warn on stderr when a query had a near-equal runner-up. Keeps stdout pipeable."""
    alt = _ambiguous_alternative(result)
    if not alt:
        return

    print(f"Warning: '{query}' is ambiguous - two close matches", file=sys.stderr)
    print(f"  using:   {result['address']}", file=sys.stderr)
    print(f"  also:    {alt['address']} ({alt['type']})", file=sys.stderr)
    print("  narrow the query, or use --near, or --limit to list candidates",
          file=sys.stderr)


def _simplify_ring(points: list, tolerance: float) -> list:
    """Douglas-Peucker simplification of a coordinate ring."""
    if len(points) < 3:
        return points

    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]

    while stack:
        start, end = stack.pop()
        ax, ay = points[start]
        bx, by = points[end]
        dx, dy = bx - ax, by - ay
        span = dx * dx + dy * dy

        worst, index = tolerance, None
        for i in range(start + 1, end):
            px, py = points[i]
            if span == 0:
                dist = ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
            else:
                t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / span))
                dist = ((px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2) ** 0.5
            if dist > worst:
                worst, index = dist, i

        if index is not None:
            keep[index] = True
            stack.append((start, index))
            stack.append((index, end))

    simplified = [pt for pt, k in zip(points, keep) if k]
    # A linear ring needs closure and at least three distinct vertices.
    if (len(simplified) < 4 or simplified[0] != simplified[-1]
            or len({tuple(pt) for pt in simplified[:-1]}) < 3):
        return points
    return simplified


def _simplify_geometry(geometry: dict, tolerance: float) -> dict:
    """Simplify Polygon/MultiPolygon coordinates, leaving other types alone."""
    if not geometry or tolerance <= 0:
        return geometry

    kind = geometry.get("type")
    if kind == "Polygon":
        rings = [_simplify_ring(r, tolerance) for r in geometry["coordinates"]]
    elif kind == "MultiPolygon":
        rings = [[_simplify_ring(r, tolerance) for r in poly]
                 for poly in geometry["coordinates"]]
    else:
        return geometry

    return {"type": kind, "coordinates": rings}


def _geojson_feature(geometry: dict, properties: Optional[dict] = None) -> dict:
    """Wrap a geometry in a GeoJSON Feature."""
    return {
        "type": "Feature",
        "geometry": geometry,
        "properties": {k: v for k, v in (properties or {}).items() if v is not None},
    }


def _geojson_point(lat: float, lng: float, properties: Optional[dict] = None) -> dict:
    """Build a GeoJSON Point feature. Note GeoJSON orders coordinates lng, lat."""
    return _geojson_feature({"type": "Point", "coordinates": [lng, lat]}, properties)


def _geojson_line(points: list, properties: Optional[dict] = None) -> dict:
    """Build a GeoJSON LineString feature from [(lat, lng), ...]."""
    return _geojson_feature(
        {"type": "LineString", "coordinates": [[lng, lat] for lat, lng in points]},
        properties,
    )


def _geojson_collection(features: list) -> dict:
    """Wrap features in a GeoJSON FeatureCollection."""
    return {"type": "FeatureCollection", "features": features}


def _print_geojson(obj: dict) -> None:
    """Print GeoJSON to stdout, ready to pipe into 'geo.py interact --geojson -'."""
    print(json.dumps(obj, indent=2))


def _maps_url(lat: float, lng: float) -> str:
    """Generate Google Maps URL for coordinates."""
    return f"https://www.google.com/maps?q={lat},{lng}"


def _directions_url(waypoints: list[tuple[float, float]], mode: str = "driving") -> str:
    """Generate a Google Maps directions URL for a list of waypoints."""
    travel_modes = {
        "driving": "driving",
        "walking": "walking",
        "cycling": "bicycling",
        "motorcycle": "driving",
        "truck": "driving",
    }

    origin = f"{waypoints[0][0]},{waypoints[0][1]}"
    destination = f"{waypoints[-1][0]},{waypoints[-1][1]}"
    url = (
        "https://www.google.com/maps/dir/?api=1"
        f"&origin={origin}&destination={destination}"
        f"&travelmode={travel_modes.get(mode, 'driving')}"
    )

    via = waypoints[1:-1]
    if via:
        url += "&waypoints=" + "|".join(f"{lat},{lng}" for lat, lng in via)

    return url


# ============================================================================
# Command handlers
# ============================================================================

def _read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as stream:
            return stream.read()
    except (OSError, UnicodeError) as e:
        raise GeoError(f"Could not read {path}: {e}") from e


def _validate_geometry(geometry: dict) -> None:
    """Validate GeoJSON structure before handing it to Leaflet."""
    if not isinstance(geometry, dict):
        raise GeoError("GeoJSON geometry must be an object")
    kind = geometry.get("type")
    if kind == "GeometryCollection":
        geometries = geometry.get("geometries")
        if not isinstance(geometries, list):
            raise GeoError("GeometryCollection needs a geometries array")
        for child in geometries:
            _validate_geometry(child)
        return
    depths = {
        "Point": 0, "MultiPoint": 1, "LineString": 1,
        "MultiLineString": 2, "Polygon": 2, "MultiPolygon": 3,
    }
    if not isinstance(kind, str) or kind not in depths:
        raise GeoError(f"Unknown GeoJSON geometry type: {kind}")

    def positions(value, depth):
        if not isinstance(value, list):
            raise GeoError("GeoJSON coordinates must be arrays")
        if depth == 0:
            try:
                valid = len(value) >= 2 and all(
                    not isinstance(n, bool) and isinstance(n, (int, float))
                    and math.isfinite(n) for n in value
                )
            except OverflowError:
                valid = False
            if not valid:
                raise GeoError("GeoJSON positions need at least two finite numbers")
        else:
            for child in value:
                positions(child, depth - 1)

    coordinates = geometry.get("coordinates")
    positions(coordinates, depths[kind])

    def line(points):
        if len(points) < 2:
            raise GeoError("GeoJSON LineString needs at least two positions")

    def polygon(rings):
        if not rings:
            raise GeoError("GeoJSON Polygon needs an exterior ring")
        for ring in rings:
            if (len(ring) < 4 or ring[0] != ring[-1]
                    or len({tuple(p) for p in ring[:-1]}) < 3):
                raise GeoError("GeoJSON polygon rings need closure and three distinct vertices")

    if kind == "LineString":
        line(coordinates)
    elif kind == "MultiLineString":
        for points in coordinates:
            line(points)
    elif kind == "Polygon":
        polygon(coordinates)
    elif kind == "MultiPolygon":
        for rings in coordinates:
            polygon(rings)


def _validate_feature(feature: dict) -> None:
    if not isinstance(feature, dict) or feature.get("type") != "Feature":
        raise GeoError("GeoJSON features must be Feature objects")
    if "geometry" not in feature or "properties" not in feature:
        raise GeoError("GeoJSON Feature needs geometry and properties members")
    if feature["properties"] is not None and not isinstance(feature["properties"], dict):
        raise GeoError("GeoJSON Feature properties must be an object or null")
    if feature["geometry"] is not None:
        _validate_geometry(feature["geometry"])


def _load_geojson_inputs(sources: list) -> list:
    """
    Read GeoJSON from files or stdin ('-') and flatten into a list of features.

    Accepts a FeatureCollection, a bare Feature, or a raw geometry.
    """
    features = []

    for source in sources:
        raw = sys.stdin.read() if source == "-" else _read_text(source)

        try:
            data = json.loads(raw)
        except ValueError as e:
            label = "stdin" if source == "-" else source
            raise GeoError(f"{label} is not valid JSON: {e}")

        kind = data.get("type") if isinstance(data, dict) else None
        if kind == "FeatureCollection":
            incoming = data.get("features")
            if not isinstance(incoming, list):
                raise GeoError(f"{source}: FeatureCollection needs a features array")
            for feature in incoming:
                _validate_feature(feature)
            features.extend(incoming)
        elif kind == "Feature":
            _validate_feature(data)
            features.append(data)
        elif kind:
            _validate_geometry(data)
            features.append(_geojson_feature(data))
        else:
            label = "stdin" if source == "-" else source
            raise GeoError(f"{label} is not GeoJSON (no 'type' member)")

    return features


def _serve_map(page: str, data: dict, port: int, open_browser: bool) -> int:
    """Serve the Leaflet page on localhost until interrupted."""
    routes = {
        "/": ("text/html; charset=utf-8", page.encode()),
        "/data.json": ("application/json", json.dumps(data).encode()),
    }

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?")[0]
            if path not in routes:
                self.send_error(404)
                return
            content_type, body = routes[path]
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass  # keep the terminal readable

    try:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as e:
        if port == 0:
            raise GeoError(f"Could not start server: {e}")
        print(f"  Port {port} unavailable ({e.strerror}), using a free port instead")
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)

    url = f"http://127.0.0.1:{server.server_address[1]}/"
    feature_count = len(data.get("features") or [])
    print(f"\n  Serving map at {url}")
    print(f"  {feature_count} feature{'s' if feature_count != 1 else ''} loaded")
    print("  Press Ctrl+C to stop\n", flush=True)

    if open_browser:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("  Stopped")
    finally:
        server.server_close()

    return 0


def _fail(message: str) -> int:
    """Keep diagnostics off stdout, especially for JSON/GeoJSON pipelines."""
    print(f"Error: {message}", file=sys.stderr)
    return 1


def cmd_interact(client: GeoClient, args: argparse.Namespace) -> int:
    """Handle the interact command."""
    try:
        features = _load_geojson_inputs(args.geojson or [])

        # --marker "LOCATION:Popup text", location resolved the usual way
        for marker in args.marker or []:
            location, _, popup = marker.partition(":")
            lat, lng, address = client.parse_location(location.strip())
            features.append(_geojson_point(lat, lng, {
                "title": popup.strip() or location.strip(),
                "description": address,
                "marker-color": "#e6550d",
            }))

        center = None
        if args.center:
            lat, lng, _ = client.parse_location(args.center)
            center = [lat, lng]

        tiles = TILE_LAYERS[args.tiles]
        if args.zoom is not None and args.zoom > tiles["max_zoom"]:
            print(f"  Note: {args.tiles} tiles stop at zoom {tiles['max_zoom']}")

        page = (MAP_PAGE
                .replace("__CENTER__", json.dumps(center))
                .replace("__ZOOM__", json.dumps(args.zoom))
                .replace("__TILE_URL__", json.dumps(tiles["url"]))
                .replace("__TILE_MAX_ZOOM__", str(tiles["max_zoom"])))

        return _serve_map(page, _geojson_collection(features),
                          args.port, not args.no_open)
    except GeoError as e:
        return _fail(str(e))


def cmd_geocode(client: GeoClient, args: argparse.Namespace) -> int:
    """Handle the geocode command."""
    try:
        if args.limit < 1:
            raise GeoError("--limit must be at least 1")
        if not math.isfinite(args.simplify) or args.simplify < 0:
            raise GeoError("--simplify must be a non-negative finite tolerance")
        viewbox = None
        if args.near:
            viewbox = _viewbox_around(client, args.near, args.within, args.unit)
        result = client.geocode(
            args.address, polygon=args.polygon, viewbox=viewbox,
            limit=max(CANDIDATE_LIMIT, args.limit),
        )

        if not result:
            if args.near:
                return _fail(f"No results for '{args.address}' within "
                             f"{args.within} {args.unit} of {args.near}")
            return _fail(f"No results found for: {args.address}")

        _warn_if_ambiguous(result, args.address)

        if args.expect_type:
            expected = {kind.strip().lower() for kind in args.expect_type.split(",")}
            found = _place_kinds(result)
            if not expected & found:
                print(f"Error: expected {'/'.join(sorted(expected))} but matched "
                      f"{'/'.join(sorted(found)) or 'nothing'}", file=sys.stderr)
                print(f"  {result['address']}", file=sys.stderr)
                return 1

        maps_url = _maps_url(result['latitude'], result['longitude'])

        if args.geojson:
            properties = {"title": args.address, "description": result["address"],
                          "marker-color": "#e6550d"}
            boundary = result.get("boundary")
            if boundary:
                properties["latitude"] = result["latitude"]
                properties["longitude"] = result["longitude"]
                _print_geojson(_geojson_feature(
                    _simplify_geometry(boundary, args.simplify), properties))
            else:
                if args.polygon:
                    print(f"No boundary geometry for: {args.address}", file=sys.stderr)
                _print_geojson(_geojson_point(
                    result["latitude"], result["longitude"], properties))
        elif args.json:
            result["maps_url"] = maps_url
            result["alternatives"] = result["alternatives"][:max(0, args.limit - 1)]
            print(json.dumps(result, indent=2))
        else:
            print(f"\n  Geocode: {args.address}")
            print("  " + "=" * 58)
            print(f"  Latitude:   {result['latitude']}")
            print(f"  Longitude:  {result['longitude']}")
            print(f"  Address:    {result['address']}")
            print(f"  Map:        {maps_url}")

            others = result["alternatives"][:max(0, args.limit - 1)]
            if others:
                print()
                print(f"  Other matches for '{args.address}':")
                for number, alt in enumerate(others, start=2):
                    print(f"    {number}. {alt['address']}")
                    print(f"       {alt['type'] or '?'}, importance {alt['importance']:.3f}"
                          f"  ({alt['latitude']}, {alt['longitude']})")
            print()

        return 0
    except GeoError as e:
        return _fail(str(e))


def cmd_reverse(client: GeoClient, args: argparse.Namespace) -> int:
    """Handle the reverse geocode command."""
    try:
        # Parse coordinates
        lat, lng = client._parse_coordinates(args.coordinates)

        errors = client.coordinate_errors(lat, lng)
        if errors:
            return _fail(f"Invalid coordinates - {', '.join(errors)}")

        result = client.reverse_geocode(lat, lng)

        if not result:
            return _fail(f"No address found for coordinates: {lat}, {lng}")

        maps_url = _maps_url(lat, lng)

        if args.geojson:
            _print_geojson(_geojson_point(
                lat, lng,
                {"title": f"{lat}, {lng}", "description": result["address"],
                 "marker-color": "#e6550d"},
            ))
        elif args.json:
            result["maps_url"] = maps_url
            print(json.dumps(result, indent=2))
        else:
            print(f"\n  Reverse Geocode: {lat}, {lng}")
            print("  " + "=" * 58)
            print(f"  Address:  {result['address']}")
            print(f"  Map:      {maps_url}")
            print()

            # Show components if available
            c = result["components"]
            if any(c.values()):
                print("  Components:")
                if c.get("road"):
                    house_num = c.get("house_number") or ""
                    street = f"{house_num} {c['road']}".strip()
                    print(f"    Street:       {street}")
                if c.get("city"):
                    print(f"    City:         {c['city']}")
                if c.get("state"):
                    print(f"    State:        {c['state']}")
                if c.get("postcode"):
                    print(f"    Postal Code:  {c['postcode']}")
                if c.get("country"):
                    print(f"    Country:      {c['country']}")
                print()

        return 0
    except GeoError as e:
        return _fail(str(e))


def cmd_distance(client: GeoClient, args: argparse.Namespace) -> int:
    """Handle the distance command."""
    try:
        # Parse both locations (smart parsing)
        from_lat, from_lng, from_addr = client.parse_location(args.from_loc)
        to_lat, to_lng, to_addr = client.parse_location(args.to_loc)

        # Calculate distance
        result = client.calculate_distance(from_lat, from_lng, to_lat, to_lng)

        # Calculate bearing too
        bearing = client.calculate_bearing(from_lat, from_lng, to_lat, to_lng)
        cardinal = _bearing_to_cardinal(bearing)

        from_maps = _maps_url(from_lat, from_lng)
        to_maps = _maps_url(to_lat, to_lng)

        if args.geojson:
            from_display = from_addr or f"{from_lat}, {from_lng}"
            to_display = to_addr or f"{to_lat}, {to_lng}"
            label = f"{_format_distance(result['distance'], args.unit)} ({cardinal})"
            _print_geojson(_geojson_collection([
                _geojson_point(from_lat, from_lng,
                               {"title": "From", "description": from_display,
                                "marker-color": "#31a354"}),
                _geojson_point(to_lat, to_lng,
                               {"title": "To", "description": to_display,
                                "marker-color": "#e6550d"}),
                _geojson_line([(from_lat, from_lng), (to_lat, to_lng)],
                              {"title": label, "stroke": "#3182bd",
                               "stroke-width": 3, "stroke-opacity": 0.8}),
            ]))
        elif args.json:
            result["bearing"] = {"degrees": bearing, "cardinal": cardinal}
            result["from"]["maps_url"] = from_maps
            result["to"]["maps_url"] = to_maps
            if from_addr:
                result["from"]["address"] = from_addr
            if to_addr:
                result["to"]["address"] = to_addr
            print(json.dumps(result, indent=2))
        else:
            from_display = from_addr or f"{from_lat}, {from_lng}"
            to_display = to_addr or f"{to_lat}, {to_lng}"

            print(f"\n  Distance Calculation")
            print("  " + "=" * 58)
            print(f"  From:       {from_display}")
            if from_addr:
                print(f"              ({from_lat}, {from_lng})")
            print(f"              {from_maps}")
            print(f"  To:         {to_display}")
            if to_addr:
                print(f"              ({to_lat}, {to_lng})")
            print(f"              {to_maps}")
            print()
            print(f"  Distance:   {_format_distance(result['distance'], args.unit)}")
            print(f"  Bearing:    {bearing}° ({cardinal})")
            print()

        return 0
    except GeoError as e:
        return _fail(str(e))


def cmd_route(client: GeoClient, args: argparse.Namespace) -> int:
    """Handle the route command."""
    try:
        # Parse every stop (smart parsing), in travel order
        stops = []
        for location in [args.from_loc] + (args.via or []) + [args.to_loc]:
            lat, lng, addr = client.parse_location(location)
            stops.append({
                "input": location,
                "latitude": lat,
                "longitude": lng,
                "address": addr,
                "maps_url": _maps_url(lat, lng),
            })

        waypoints = [(s["latitude"], s["longitude"]) for s in stops]
        result = client.route(waypoints, mode=args.mode, steps=not args.no_steps)
        directions_url = _directions_url(waypoints, args.mode)

        if args.geojson:
            features = []
            uses = ", ".join(name for name, present in [
                ("tolls", result["has_toll"]),
                ("ferry", result["has_ferry"]),
                ("highways", result["has_highway"]),
            ] if present)
            for index, leg in enumerate(result["legs"], start=1):
                if not leg["shape"]:
                    continue
                features.append(_geojson_line(leg["shape"], {
                    "title": f"Leg {index}" if len(result["legs"]) > 1 else result["mode"],
                    "description": (
                        f"{_format_distance(leg['distance'], args.unit)}"
                        f", {_format_duration(leg['duration_seconds'])}"
                    ),
                    "mode": result["mode"],
                    "uses": uses or None,
                    "steps": len(leg["steps"]) or None,
                    "stroke": "#3182bd",
                    "stroke-width": 5,
                    "stroke-opacity": 0.8,
                }))
            # One small marker per turn; the first and last sit under the stop markers
            for leg in result["legs"]:
                for step in leg["steps"]:
                    if not step["location"]:
                        continue
                    lat, lng = step["location"]
                    features.append(_geojson_point(lat, lng, {
                        "title": step["instruction"],
                        "toward": step["toward"],
                        "exit": step["exit"],
                        "toll": "yes" if step["toll"] else None,
                        "next": (
                            f"{_format_distance(step['distance'], args.unit)}"
                            f" ({_format_duration(step['duration_seconds'])})"
                        ) if step["distance"]["meters"] >= 1 else None,
                        "total after": _format_distance(step["cumulative_distance"], args.unit),
                        "marker-color": "#3182bd",
                        "marker-size": "small",
                    }))
            for position, stop in enumerate(stops):
                if position == 0:
                    title, color = "Start", "#31a354"
                elif position == len(stops) - 1:
                    title, color = "Destination", "#e6550d"
                else:
                    title, color = f"Stop {position}", "#756bb1"
                features.append(_geojson_point(
                    stop["latitude"], stop["longitude"],
                    {"title": title, "description": stop["address"] or stop["input"],
                     "marker-color": color},
                ))
            _print_geojson(_geojson_collection(features))
        elif args.json:
            result["stops"] = stops
            result["directions_url"] = directions_url
            print(json.dumps(result, indent=2))
        else:
            print(f"\n  Directions ({result['mode']})")
            print("  " + "=" * 58)
            for label, stop in zip(
                ["From:"] + ["Via:"] * len(args.via or []) + ["To:"], stops
            ):
                display = stop["address"] or f"{stop['latitude']}, {stop['longitude']}"
                print(f"  {label:<11}{display}")
            print()
            print(f"  Distance:   {_format_distance(result['distance'], args.unit)}")
            print(f"  Duration:   {_format_duration(result['duration_seconds'])}")

            flags = [
                name for name, present in [
                    ("tolls", result["has_toll"]),
                    ("ferry", result["has_ferry"]),
                    ("highways", result["has_highway"]),
                ] if present
            ]
            if flags:
                print(f"  Route uses: {', '.join(flags)}")
            print(f"  Map:        {directions_url}")

            for index, leg in enumerate(result["legs"], start=1):
                if not leg["steps"]:
                    continue
                print()
                if len(result["legs"]) > 1:
                    print(f"  Leg {index} - {_format_distance(leg['distance'], args.unit)}"
                          f", {_format_duration(leg['duration_seconds'])}")
                    print("  " + "-" * 58)
                for number, step in enumerate(leg["steps"], start=1):
                    print(f"  {number:>3}. {step['instruction']}")

                    details = []
                    if step["toward"]:
                        details.append(f"toward {step['toward']}")
                    if step["exit"]:
                        details.append(f"exit {step['exit']}")
                    if step["toll"]:
                        details.append("toll")
                    if step["distance"]["meters"] >= 1:
                        details.append(
                            f"{_format_distance(step['distance'], args.unit)}"
                            f" ({_format_duration(step['duration_seconds'])})"
                        )
                        details.append(
                            f"{_format_distance(step['cumulative_distance'], args.unit)} total"
                        )
                    if details:
                        print(f"       {' · '.join(details)}")
            print()

        return 0
    except GeoError as e:
        return _fail(str(e))


# ============================================================================
# Main CLI
# ============================================================================


def _value_option_strings(parser: argparse.ArgumentParser) -> set:
    """Collect every option string that takes a value, subcommands included."""
    names = set()

    for action in parser._actions:
        if action.option_strings and action.nargs != 0:
            names.update(action.option_strings)
        if isinstance(action, argparse._SubParsersAction):
            for subparser in action.choices.values():
                names |= _value_option_strings(subparser)

    return names


def _normalize_coordinate_args(argv: list, value_options: set) -> list:
    """
    Let southern hemisphere coordinates survive argparse.

    argparse reads any token starting with '-' as an option, so '-33.87,151.21'
    is rejected wherever a location is expected. Attach such a value to the flag
    it belongs to with '=', and push a bare positional behind a '--' guard.
    """
    normalized = []
    positionals = []

    for token in argv:
        if token.startswith("-") and COORDINATE_PATTERN.match(token):
            previous = normalized[-1] if normalized else ""
            if previous in value_options:
                normalized[-1] = f"{previous}={token}"
            else:
                positionals.append(token)
            continue
        normalized.append(token)

    if positionals:
        normalized.append("--")
        normalized.extend(positionals)

    return normalized


def _add_output_options(parser: argparse.ArgumentParser, geojson: bool = True) -> None:
    parser.add_argument("--json", "-j", action="store_true", help="Output as JSON")
    if geojson:
        parser.add_argument(
            "--geojson", "-g", action="store_true",
            help="Output as GeoJSON (pipe into 'geo.py interact --geojson -')",
        )


def _use_utf8_streams() -> None:
    """
    Read and write UTF-8 even when stdio is a pipe on Windows.

    Windows hands pipes the legacy code page (cp1252), so a degree sign or '·'
    came out as a lone byte that UTF-8 terminals render as a replacement glyph.
    """
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        encoding = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
        if encoding != "utf8" and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def main() -> int:
    _use_utf8_streams()
    parser = argparse.ArgumentParser(
        description="geo.py - CLI tool for working with lat/lng and geography",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=CLI_EPILOG,
    )

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # geocode command
    geocode_parser = subparsers.add_parser(
        "geocode",
        help="Convert address to coordinates",
        description="Geocode an address to latitude/longitude coordinates."
    )
    geocode_parser.add_argument(
        "address",
        help="Address to geocode (e.g., '1600 Pennsylvania Ave, Washington DC')"
    )
    geocode_parser.add_argument(
        "--expect-type",
        metavar="TYPE",
        help="Fail unless the match is one of these OSM types "
             "(comma separated, e.g. 'administrative,city')"
    )
    geocode_parser.add_argument(
        "--near", "-n",
        help="Restrict the search to the area around this location"
    )
    geocode_parser.add_argument(
        "--within", "-w",
        type=float,
        default=25.0,
        help="Radius used by --near (default: 25)"
    )
    geocode_parser.add_argument(
        "--unit", "-u",
        default="miles",
        choices=["mi", "miles", "km", "kilometers", "m", "meters", "ft", "feet"],
        help="Unit for --within (default: miles)"
    )
    geocode_parser.add_argument(
        "--limit", "-l",
        type=int,
        default=1,
        help="Show this many candidate matches (default: 1)"
    )
    geocode_parser.add_argument(
        "--polygon", "-p",
        action="store_true",
        help="Include the administrative boundary polygon, when the place has one"
    )
    geocode_parser.add_argument(
        "--simplify",
        type=float,
        default=0.0,
        metavar="DEGREES",
        help="Simplify boundary geometry by this tolerance (e.g. 0.01)"
    )
    _add_output_options(geocode_parser)

    # reverse command
    reverse_parser = subparsers.add_parser(
        "reverse",
        help="Convert coordinates to address",
        description="Reverse geocode coordinates to an address."
    )
    reverse_parser.add_argument(
        "coordinates",
        help="Coordinates as 'lat,lng' or 'lat lng' (e.g., '40.7128,-74.0060')"
    )
    _add_output_options(reverse_parser)

    # distance command
    distance_parser = subparsers.add_parser(
        "distance",
        help="Calculate distance between two points",
        description="Calculate the distance between two geographic points."
    )
    distance_parser.add_argument(
        "--from", "-f",
        dest="from_loc",
        required=True,
        help="Starting point (coordinates or address)"
    )
    distance_parser.add_argument(
        "--to", "-t",
        dest="to_loc",
        required=True,
        help="Ending point (coordinates or address)"
    )
    distance_parser.add_argument(
        "--unit", "-u",
        default="miles",
        choices=["mi", "miles", "km", "kilometers", "m", "meters", "ft", "feet", "nm"],
        help="Distance unit (default: miles)"
    )
    _add_output_options(distance_parser)

    # route command
    route_parser = subparsers.add_parser(
        "route",
        help="Get turn-by-turn directions between two points",
        description="Get turn-by-turn directions between two or more points."
    )
    route_parser.add_argument(
        "--from", "-f",
        dest="from_loc",
        required=True,
        help="Starting point (coordinates or address)"
    )
    route_parser.add_argument(
        "--to", "-t",
        dest="to_loc",
        required=True,
        help="Destination (coordinates or address)"
    )
    route_parser.add_argument(
        "--via", "-v",
        action="append",
        help="Intermediate stop (coordinates or address), repeatable"
    )
    route_parser.add_argument(
        "--mode", "-m",
        default="driving",
        choices=["driving", "walking", "cycling", "motorcycle", "truck"],
        help="Travel mode (default: driving)"
    )
    route_parser.add_argument(
        "--unit", "-u",
        default="miles",
        choices=["mi", "miles", "km", "kilometers", "m", "meters", "ft", "feet"],
        help="Distance unit (default: miles)"
    )
    route_parser.add_argument(
        "--no-steps", "-s",
        action="store_true",
        help="Show only the summary, no turn-by-turn steps"
    )
    _add_output_options(route_parser)

    # interact command
    interact_parser = subparsers.add_parser(
        "interact",
        help="Serve an interactive Leaflet map on localhost",
        description="Serve a full-page Leaflet map on localhost, optionally loaded with GeoJSON."
    )
    interact_parser.add_argument(
        "--center", "-c",
        help="Map center (coordinates or address); defaults to fitting the data"
    )
    interact_parser.add_argument(
        "--zoom", "-z",
        type=int,
        help="Zoom level (0-19)"
    )
    interact_parser.add_argument(
        "--geojson", "-g",
        action="append",
        help="GeoJSON file to render, or '-' for stdin (repeatable)"
    )
    interact_parser.add_argument(
        "--marker", "-m",
        action="append",
        help="Marker as 'LOCATION:Popup text' (repeatable)"
    )
    interact_parser.add_argument(
        "--tiles", "-t",
        default="osm",
        choices=list(TILE_LAYERS),
        help="Basemap tiles (default: osm)"
    )
    interact_parser.add_argument(
        "--port", "-p",
        type=int,
        default=8765,
        help="Port to serve on (default: 8765)"
    )
    interact_parser.add_argument(
        "--no-open",
        action="store_true",
        help="Do not open a browser automatically"
    )

    # Parse args, rescuing coordinates that begin with a negative latitude
    args = parser.parse_args(
        _normalize_coordinate_args(sys.argv[1:], _value_option_strings(parser))
    )

    if not args.command:
        parser.print_help()
        return 1

    # Create client
    client = GeoClient()

    # Dispatch to command handler
    commands = {
        "geocode": cmd_geocode,
        "reverse": cmd_reverse,
        "distance": cmd_distance,
        "route": cmd_route,
        "interact": cmd_interact,
    }

    handler = commands.get(args.command)
    if handler:
        return handler(client, args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
