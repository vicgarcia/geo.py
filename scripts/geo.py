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
import re
import socket
import sys
import webbrowser
from typing import Optional

import requests
from geopy.geocoders import Nominatim
from geopy.distance import geodesic, great_circle
from geopy.point import Point

# User agent for Nominatim (required)
USER_AGENT = "geo.py-cli/1.0"

# How many candidates to pull back so ambiguity is detectable. Same single
# request either way, so this costs nothing against the rate limit.
CANDIDATE_LIMIT = 5

# Nominatim scores every match with an "importance". When the runner-up scores
# this close to the winner the query is a genuine toss-up, not a clear hit.
AMBIGUITY_RATIO = 0.85

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
  geo.py interact --center "Denver" --script viz.js
  geo.py interact --center "Zermatt" --tiles topo --zoom 14
  geo.py interact --center "Manhattan" --tiles satellite

  geo.py destination --start "40.7128,-74.0060" --bearing 270 --distance 100
  geo.py destination --start "Seattle" --bearing 180 --distance 50 --unit km

  geo.py validate "40.7128,-74.0060"
  geo.py validate "91.0,181.0"

  geo.py ip
  geo.py ip 8.8.8.8

  geo.py bbox --center "NYC" --radius 5 --unit km
  geo.py bbox --center "40.7128,-74.006" --radius 1 --unit miles --json --geojson

Smart Location Parsing:
  Most commands accept either raw coordinates or addresses:
    "40.7128,-74.0060"     Raw lat,lng (comma or space separated)
    "40.7128 -74.0060"     Raw lat lng (space separated)
    "New York City"        Address (auto-geocoded)
    "Tokyo, Japan"         Address with region

Basemap Tiles (interact):
  osm           OpenStreetMap standard (default)
  topo          OpenTopoMap, contour lines
  cyclosm       CyclOSM, cycling infrastructure
  humanitarian  Humanitarian OSM Team style
  light         Esri Light Gray Canvas
  dark          Esri Dark Gray Canvas
  satellite     Esri World Imagery
  terrain       Esri World Topo

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


# Keyless raster tile providers. Each is free to use under its own fair-use
# policy - keep traffic light and leave the attribution intact.
TILE_LAYERS = {
    "osm": {
        "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        "attribution": '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
        "max_zoom": 19,
    },
    "topo": {
        "url": "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
        "attribution": '&copy; OpenStreetMap contributors | &copy; <a href="https://opentopomap.org">OpenTopoMap</a> (CC-BY-SA)',
        "max_zoom": 17,
    },
    "cyclosm": {
        "url": "https://{s}.tile-cyclosm.openstreetmap.fr/cyclosm/{z}/{x}/{y}.png",
        "attribution": '&copy; OpenStreetMap contributors | tiles <a href="https://www.cyclosm.org/">CyclOSM</a>',
        "max_zoom": 20,
    },
    "humanitarian": {
        "url": "https://{s}.tile.openstreetmap.fr/hot/{z}/{x}/{y}.png",
        "attribution": '&copy; OpenStreetMap contributors | tiles <a href="https://www.hotosm.org/">HOT</a>',
        "max_zoom": 20,
    },
    "light": {
        "url": "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}",
        "attribution": 'Tiles &copy; <a href="https://www.esri.com/">Esri</a> | &copy; OpenStreetMap contributors',
        "max_zoom": 16,
    },
    "dark": {
        "url": "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}",
        "attribution": 'Tiles &copy; <a href="https://www.esri.com/">Esri</a> | &copy; OpenStreetMap contributors',
        "max_zoom": 16,
    },
    "satellite": {
        "url": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "attribution": 'Tiles &copy; <a href="https://www.esri.com/">Esri</a>, Maxar, Earthstar Geographics',
        "max_zoom": 19,
    },
    "terrain": {
        "url": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
        "attribution": 'Tiles &copy; <a href="https://www.esri.com/">Esri</a>',
        "max_zoom": 19,
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
const HAS_SCRIPT = __HAS_SCRIPT__;

const map = L.map('map');
L.tileLayer(__TILE_URL__, {
  maxZoom: __TILE_MAX_ZOOM__,
  attribution: __TILE_ATTRIBUTION__
}).addTo(map);

// Layers keyed for --script to reach: layers.data is the loaded GeoJSON
const layers = {};
let geo = null;

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
  const bounds = layers.data && layers.data.getBounds();
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
    geo = data;
    if (data.features && data.features.length) {
      layers.data = L.geoJSON(data, {
        style: styleFor, pointToLayer: markerFor, onEachFeature: popupFor
      }).addTo(map);
    }
    frame();
    // Load user script last so map, L, layers and geo are all ready
    if (HAS_SCRIPT) {
      const tag = document.createElement('script');
      tag.src = 'script.js';
      document.body.appendChild(tag);
    }
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

    # Regex patterns for coordinate detection
    COORD_PATTERN = re.compile(
        r'^[-+]?([1-8]?\d(\.\d+)?|90(\.0+)?)\s*[,\s]\s*[-+]?(180(\.0+)?|((1[0-7]\d)|([1-9]?\d))(\.\d+)?)$'
    )

    # Simpler pattern that's more permissive for parsing
    SIMPLE_COORD_PATTERN = re.compile(
        r'^([-+]?\d+\.?\d*)\s*[,\s]\s*([-+]?\d+\.?\d*)$'
    )

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
        self._session = requests.Session()

    def _looks_like_coordinates(self, location: str) -> bool:
        """Check if a string looks like lat,lng coordinates."""
        return bool(self.SIMPLE_COORD_PATTERN.match(location.strip()))

    def _parse_coordinates(self, location: str) -> tuple[float, float]:
        """Parse a coordinate string into (lat, lng) tuple."""
        match = self.SIMPLE_COORD_PATTERN.match(location.strip())
        if not match:
            raise GeoError(f"Could not parse coordinates: {location}")

        lat, lng = float(match.group(1)), float(match.group(2))
        return lat, lng

    def validate_coordinates(self, lat: float, lng: float) -> dict:
        """
        Validate if coordinates are within valid ranges.

        Returns dict with validation results and details.
        """
        errors = []
        warnings = []

        # Check latitude range
        if lat < -90 or lat > 90:
            errors.append(f"Latitude {lat} out of range [-90, 90]")

        # Check longitude range
        if lng < -180 or lng > 180:
            errors.append(f"Longitude {lng} out of range [-180, 180]")

        # Check for suspicious values
        if lat == 0 and lng == 0:
            warnings.append("Coordinates are at Null Island (0,0) - often indicates missing data")

        # Determine hemisphere info
        lat_hemisphere = "N" if lat >= 0 else "S"
        lng_hemisphere = "E" if lng >= 0 else "W"

        return {
            "valid": len(errors) == 0,
            "latitude": lat,
            "longitude": lng,
            "lat_hemisphere": lat_hemisphere,
            "lng_hemisphere": lng_hemisphere,
            "errors": errors,
            "warnings": warnings,
        }

    def parse_location(self, location: str) -> tuple[float, float, Optional[str]]:
        """
        Smart location parsing - accepts coordinates or addresses.

        Returns: (lat, lng, resolved_address or None)
        """
        location = location.strip()

        # Try to parse as coordinates first
        if self._looks_like_coordinates(location):
            lat, lng = self._parse_coordinates(location)
            validation = self.validate_coordinates(lat, lng)
            if not validation["valid"]:
                raise GeoError(f"Invalid coordinates: {', '.join(validation['errors'])}")
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

            found = self.geolocator.geocode(address, **kwargs)
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
            location = self.geolocator.reverse(f"{lat}, {lng}", addressdetails=True)
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
        method: str = "geodesic"
    ) -> dict:
        """
        Calculate distance between two points.

        method: 'geodesic' (more accurate) or 'great_circle' (faster)
        """
        point1 = (from_lat, from_lng)
        point2 = (to_lat, to_lng)

        if method == "geodesic":
            dist = geodesic(point1, point2)
        else:
            dist = great_circle(point1, point2)

        return {
            "from": {"latitude": from_lat, "longitude": from_lng},
            "to": {"latitude": to_lat, "longitude": to_lng},
            "method": method,
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

    def calculate_destination(
        self,
        start_lat: float,
        start_lng: float,
        bearing: float,
        distance_km: float
    ) -> dict:
        """
        Calculate destination point given start, bearing, and distance.

        bearing: degrees from north (0-360)
        distance_km: distance in kilometers
        """
        start = Point(start_lat, start_lng)
        dest = geodesic(kilometers=distance_km).destination(start, bearing)

        return {
            "start": {"latitude": start_lat, "longitude": start_lng},
            "bearing": bearing,
            "distance_km": distance_km,
            "destination": {
                "latitude": round(dest.latitude, 6),
                "longitude": round(dest.longitude, 6),
            },
        }

    def calculate_bbox(
        self,
        center_lat: float,
        center_lng: float,
        radius_km: float
    ) -> dict:
        """
        Calculate bounding box from center point and radius.

        Returns dict with bounds and GeoJSON representation.
        """
        center = Point(center_lat, center_lng)

        # Calculate the four cardinal points
        north = geodesic(kilometers=radius_km).destination(center, 0)
        south = geodesic(kilometers=radius_km).destination(center, 180)
        east = geodesic(kilometers=radius_km).destination(center, 90)
        west = geodesic(kilometers=radius_km).destination(center, 270)

        min_lat = round(south.latitude, 6)
        max_lat = round(north.latitude, 6)
        min_lng = round(west.longitude, 6)
        max_lng = round(east.longitude, 6)

        # GeoJSON bbox is [west, south, east, north] = [minLng, minLat, maxLng, maxLat]
        bbox = [min_lng, min_lat, max_lng, max_lat]

        # GeoJSON Polygon (coordinates are [lng, lat] order)
        geojson = {
            "type": "Feature",
            "bbox": bbox,
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [min_lng, min_lat],  # SW
                    [max_lng, min_lat],  # SE
                    [max_lng, max_lat],  # NE
                    [min_lng, max_lat],  # NW
                    [min_lng, min_lat],  # SW (close)
                ]]
            },
            "properties": {
                "center": {"latitude": center_lat, "longitude": center_lng},
                "radius_km": radius_km,
            }
        }

        return {
            "center": {"latitude": center_lat, "longitude": center_lng},
            "radius_km": radius_km,
            "bounds": {
                "north": max_lat,
                "south": min_lat,
                "east": max_lng,
                "west": min_lng,
            },
            "bbox": bbox,
            "geojson": geojson,
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
            raw_maneuvers = leg.get("maneuvers", [])
            for index, m in enumerate(raw_maneuvers):
                traveled += m.get("length", 0.0)
                if not steps:
                    continue
                following = raw_maneuvers[index + 1] if index + 1 < len(raw_maneuvers) else None
                maneuvers.append({
                    "instruction": m.get("instruction", ""),
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
                "shape": _decode_polyline(leg["shape"]) if leg.get("shape") else [],
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

    def geolocate_ip(self, ip: Optional[str] = None) -> dict:
        """
        Get geographic location from IP address.

        If ip is None, uses the current public IP.
        """
        try:
            if ip:
                url = f"http://ip-api.com/json/{ip}"
            else:
                url = "http://ip-api.com/json/"

            response = self._session.get(url, timeout=10)
            data = response.json()

            if data.get("status") == "fail":
                raise GeoError(f"IP geolocation failed: {data.get('message', 'Unknown error')}")

            return {
                "ip": data.get("query"),
                "latitude": data.get("lat"),
                "longitude": data.get("lon"),
                "city": data.get("city"),
                "region": data.get("regionName"),
                "country": data.get("country"),
                "country_code": data.get("countryCode"),
                "timezone": data.get("timezone"),
                "isp": data.get("isp"),
                "org": data.get("org"),
            }
        except requests.RequestException as e:
            raise GeoError(f"IP geolocation request failed: {e}")


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
    bounds = client.calculate_bbox(lat, lng, radius_km)["bounds"]

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

    return [pt for pt, k in zip(points, keep) if k]


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

def _load_geojson_inputs(sources: list) -> list:
    """
    Read GeoJSON from files or stdin ('-') and flatten into a list of features.

    Accepts a FeatureCollection, a bare Feature, or a raw geometry.
    """
    features = []

    for source in sources:
        try:
            raw = sys.stdin.read() if source == "-" else open(source).read()
        except OSError as e:
            raise GeoError(f"Could not read {source}: {e}")

        try:
            data = json.loads(raw)
        except ValueError as e:
            label = "stdin" if source == "-" else source
            raise GeoError(f"{label} is not valid JSON: {e}")

        kind = data.get("type") if isinstance(data, dict) else None
        if kind == "FeatureCollection":
            features.extend(data.get("features") or [])
        elif kind == "Feature":
            features.append(data)
        elif kind:
            features.append(_geojson_feature(data))
        else:
            label = "stdin" if source == "-" else source
            raise GeoError(f"{label} is not GeoJSON (no 'type' member)")

    return features


def _serve_map(page: str, data: dict, script: Optional[str], port: int, open_browser: bool) -> int:
    """Serve the Leaflet page on localhost until interrupted."""
    routes = {
        "/": ("text/html; charset=utf-8", page.encode()),
        "/data.json": ("application/json", json.dumps(data).encode()),
        "/script.js": ("application/javascript", (script or "").encode()),
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
    print(f"  {feature_count} feature{'s' if feature_count != 1 else ''} loaded"
          + ("  |  script: yes" if script else ""))
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

        script = None
        if args.script:
            try:
                script = open(args.script).read()
            except OSError as e:
                raise GeoError(f"Could not read {args.script}: {e}")

        tiles = TILE_LAYERS[args.tiles]
        if args.zoom is not None and args.zoom > tiles["max_zoom"]:
            print(f"  Note: {args.tiles} tiles stop at zoom {tiles['max_zoom']}")

        page = (MAP_PAGE
                .replace("__CENTER__", json.dumps(center))
                .replace("__ZOOM__", json.dumps(args.zoom))
                .replace("__HAS_SCRIPT__", "true" if script else "false")
                .replace("__TILE_URL__", json.dumps(tiles["url"]))
                .replace("__TILE_ATTRIBUTION__", json.dumps(tiles["attribution"]))
                .replace("__TILE_MAX_ZOOM__", str(tiles["max_zoom"])))

        return _serve_map(page, _geojson_collection(features), script,
                          args.port, not args.no_open)
    except GeoError as e:
        print(f"Error: {e}")
        return 1


def cmd_geocode(client: GeoClient, args: argparse.Namespace) -> int:
    """Handle the geocode command."""
    try:
        viewbox = None
        if args.near:
            viewbox = _viewbox_around(client, args.near, args.within, args.unit)

        result = client.geocode(args.address, polygon=args.polygon, viewbox=viewbox)

        if not result:
            if args.near:
                print(f"No results for '{args.address}' within "
                      f"{args.within} {args.unit} of {args.near}")
            else:
                print(f"No results found for: {args.address}")
            return 1

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
        print(f"Error: {e}")
        return 1


def cmd_reverse(client: GeoClient, args: argparse.Namespace) -> int:
    """Handle the reverse geocode command."""
    try:
        # Parse coordinates
        lat, lng = client._parse_coordinates(args.coordinates)

        # Validate
        validation = client.validate_coordinates(lat, lng)
        if not validation["valid"]:
            print(f"Error: Invalid coordinates - {', '.join(validation['errors'])}")
            return 1

        result = client.reverse_geocode(lat, lng)

        if not result:
            print(f"No address found for coordinates: {lat}, {lng}")
            return 1

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
        print(f"Error: {e}")
        return 1


def cmd_distance(client: GeoClient, args: argparse.Namespace) -> int:
    """Handle the distance command."""
    try:
        # Parse both locations (smart parsing)
        from_lat, from_lng, from_addr = client.parse_location(args.from_loc)
        to_lat, to_lng, to_addr = client.parse_location(args.to_loc)

        # Calculate distance
        result = client.calculate_distance(from_lat, from_lng, to_lat, to_lng, args.method)

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

            # Show all units
            if args.all_units:
                print("  All Units:")
                d = result["distance"]
                print(f"    {d['miles']:>12,.3f} miles")
                print(f"    {d['kilometers']:>12,.3f} km")
                print(f"    {d['meters']:>12,.3f} meters")
                print(f"    {d['feet']:>12,.3f} feet")
                print(f"    {d['nautical_miles']:>12,.3f} nautical miles")
                print()

        return 0
    except GeoError as e:
        print(f"Error: {e}")
        return 1


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
                    "stroke": "#3182bd",
                    "stroke-width": 5,
                    "stroke-opacity": 0.8,
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
        print(f"Error: {e}")
        return 1


def cmd_destination(client: GeoClient, args: argparse.Namespace) -> int:
    """Handle the destination command."""
    try:
        # Parse start location (smart parsing)
        start_lat, start_lng, start_addr = client.parse_location(args.start)

        # Convert distance to km based on unit
        distance = args.distance
        unit = args.unit.lower()

        if unit in ("mi", "miles"):
            distance_km = distance * 1.60934
        elif unit in ("km", "kilometers"):
            distance_km = distance
        elif unit in ("m", "meters"):
            distance_km = distance / 1000
        elif unit in ("ft", "feet"):
            distance_km = distance * 0.0003048
        elif unit in ("nm", "nautical"):
            distance_km = distance * 1.852
        else:
            distance_km = distance * 1.60934  # default to miles

        result = client.calculate_destination(start_lat, start_lng, args.bearing, distance_km)

        # Reverse geocode the destination
        dest_lat = result["destination"]["latitude"]
        dest_lng = result["destination"]["longitude"]
        dest_info = client.reverse_geocode(dest_lat, dest_lng)
        dest_addr = dest_info["address"] if dest_info else None

        cardinal = _bearing_to_cardinal(args.bearing)
        dest_maps = _maps_url(dest_lat, dest_lng)

        if args.geojson:
            _print_geojson(_geojson_collection([
                _geojson_point(start_lat, start_lng,
                               {"title": "Start",
                                "description": start_addr or f"{start_lat}, {start_lng}",
                                "marker-color": "#31a354"}),
                _geojson_point(dest_lat, dest_lng,
                               {"title": "Destination",
                                "description": dest_addr or f"{dest_lat}, {dest_lng}",
                                "marker-color": "#e6550d"}),
                _geojson_line([(start_lat, start_lng), (dest_lat, dest_lng)],
                              {"title": f"{args.distance} {args.unit} at {args.bearing}° ({cardinal})",
                               "stroke": "#3182bd", "stroke-width": 3}),
            ]))
        elif args.json:
            result["distance_input"] = {"value": args.distance, "unit": args.unit}
            result["destination"]["maps_url"] = dest_maps
            if start_addr:
                result["start"]["address"] = start_addr
            if dest_addr:
                result["destination"]["address"] = dest_addr
            print(json.dumps(result, indent=2))
        else:
            start_display = start_addr or f"{start_lat}, {start_lng}"

            print(f"\n  Destination Calculation")
            print("  " + "=" * 58)
            print(f"  Start:      {start_display}")
            if start_addr:
                print(f"              ({start_lat}, {start_lng})")
            print(f"  Bearing:    {args.bearing}° ({cardinal})")
            print(f"  Distance:   {args.distance} {args.unit}")
            print()
            print(f"  Destination:")
            print(f"    Latitude:   {dest_lat}")
            print(f"    Longitude:  {dest_lng}")
            if dest_addr:
                print(f"    Address:    {dest_addr}")
            print(f"    Map:        {dest_maps}")
            print()

        return 0
    except GeoError as e:
        print(f"Error: {e}")
        return 1


def cmd_validate(client: GeoClient, args: argparse.Namespace) -> int:
    """Handle the validate command."""
    try:
        lat, lng = client._parse_coordinates(args.coordinates)
        result = client.validate_coordinates(lat, lng)

        if result["valid"]:
            result["maps_url"] = _maps_url(lat, lng)

        if args.json:
            import json
            print(json.dumps(result, indent=2))
        else:
            status = "VALID" if result["valid"] else "INVALID"
            status_icon = "+" if result["valid"] else "x"

            print(f"\n  Coordinate Validation: [{status_icon}] {status}")
            print("  " + "=" * 58)
            print(f"  Input:      {args.coordinates}")
            print(f"  Latitude:   {result['latitude']} ({result['lat_hemisphere']})")
            print(f"  Longitude:  {result['longitude']} ({result['lng_hemisphere']})")
            if result["valid"]:
                print(f"  Map:        {result['maps_url']}")

            if result["errors"]:
                print()
                print("  Errors:")
                for err in result["errors"]:
                    print(f"    - {err}")

            if result["warnings"]:
                print()
                print("  Warnings:")
                for warn in result["warnings"]:
                    print(f"    - {warn}")

            print()

        return 0 if result["valid"] else 1
    except GeoError as e:
        print(f"Error: {e}")
        return 1


def cmd_ip(client: GeoClient, args: argparse.Namespace) -> int:
    """Handle the IP geolocation command."""
    try:
        result = client.geolocate_ip(args.ip)
        maps_url = _maps_url(result['latitude'], result['longitude'])

        if args.geojson:
            where = ", ".join(
                part for part in (result.get("city"), result.get("region"), result.get("country"))
                if part
            )
            _print_geojson(_geojson_point(
                result["latitude"], result["longitude"],
                {"title": result.get("ip"), "description": where,
                 "isp": result.get("isp"), "timezone": result.get("timezone"),
                 "marker-color": "#756bb1"},
            ))
        elif args.json:
            import json
            result["maps_url"] = maps_url
            print(json.dumps(result, indent=2))
        else:
            ip_display = result["ip"]
            if not args.ip:
                ip_display += " (your IP)"

            print(f"\n  IP Geolocation: {ip_display}")
            print("  " + "=" * 58)
            print(f"  Latitude:   {result['latitude']}")
            print(f"  Longitude:  {result['longitude']}")
            print(f"  Map:        {maps_url}")
            print()
            print(f"  City:       {result['city']}")
            print(f"  Region:     {result['region']}")
            print(f"  Country:    {result['country']} ({result['country_code']})")
            print(f"  Timezone:   {result['timezone']}")
            print()
            if result.get("isp"):
                print(f"  ISP:        {result['isp']}")
            if result.get("org"):
                print(f"  Org:        {result['org']}")
            print()

        return 0
    except GeoError as e:
        print(f"Error: {e}")
        return 1


def cmd_bbox(client: GeoClient, args: argparse.Namespace) -> int:
    """Handle the bbox command."""
    try:
        # Parse center location (smart parsing)
        center_lat, center_lng, center_addr = client.parse_location(args.center)

        # Convert radius to km based on unit
        radius = args.radius
        unit = args.unit.lower()

        if unit in ("mi", "miles"):
            radius_km = radius * 1.60934
        elif unit in ("km", "kilometers"):
            radius_km = radius
        elif unit in ("m", "meters"):
            radius_km = radius / 1000
        elif unit in ("ft", "feet"):
            radius_km = radius * 0.0003048
        else:
            radius_km = radius * 1.60934  # default to miles

        result = client.calculate_bbox(center_lat, center_lng, radius_km)
        maps_url = _maps_url(center_lat, center_lng)

        if args.geojson:
            geojson = dict(result["geojson"])
            geojson["properties"] = {
                **(geojson.get("properties") or {}),
                "title": center_addr or f"{center_lat}, {center_lng}",
                "description": f"{args.radius} {args.unit} radius",
                "stroke": "#3182bd",
                "fill": "#3182bd",
                "fill-opacity": 0.15,
            }
            _print_geojson(geojson)
        elif args.json:
            result["maps_url"] = maps_url
            if center_addr:
                result["center"]["address"] = center_addr
            print(json.dumps(result, indent=2))
        else:
            center_display = center_addr or f"{center_lat}, {center_lng}"
            b = result["bounds"]
            bbox_str = f"{b['west']},{b['south']},{b['east']},{b['north']}"

            print(f"\n  Bounding Box")
            print("  " + "=" * 58)
            print(f"  Center:     {center_display}")
            if center_addr:
                print(f"              ({center_lat}, {center_lng})")
            print(f"  Radius:     {args.radius} {args.unit}")
            print(f"  Map:        {maps_url}")
            print()
            print(f"  Bounds:")
            print(f"    North:    {b['north']}")
            print(f"    South:    {b['south']}")
            print(f"    East:     {b['east']}")
            print(f"    West:     {b['west']}")
            print()
            print(f"  bbox:       {bbox_str}")
            print()

        return 0
    except GeoError as e:
        print(f"Error: {e}")
        return 1


# ============================================================================
# Main CLI
# ============================================================================

COORDINATE_TOKEN = re.compile(
    r"^[-+]?\d{1,3}(?:\.\d+)?\s*[,\s]\s*[-+]?\d{1,3}(?:\.\d+)?$"
)


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
        if token.startswith("-") and COORDINATE_TOKEN.match(token):
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


def main() -> int:
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
    geocode_parser.add_argument(
        "--json", "-j",
        action="store_true",
        help="Output as JSON"
    )
    geocode_parser.add_argument(
        "--geojson", "-g",
        action="store_true",
        help="Output as GeoJSON (pipe into 'geo.py interact --geojson -')"
    )

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
    reverse_parser.add_argument(
        "--json", "-j",
        action="store_true",
        help="Output as JSON"
    )
    reverse_parser.add_argument(
        "--geojson", "-g",
        action="store_true",
        help="Output as GeoJSON (pipe into 'geo.py interact --geojson -')"
    )

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
    distance_parser.add_argument(
        "--method",
        default="geodesic",
        choices=["geodesic", "great_circle"],
        help="Calculation method (default: geodesic, more accurate)"
    )
    distance_parser.add_argument(
        "--all-units", "-a",
        action="store_true",
        help="Show distance in all units"
    )
    distance_parser.add_argument(
        "--json", "-j",
        action="store_true",
        help="Output as JSON"
    )
    distance_parser.add_argument(
        "--geojson", "-g",
        action="store_true",
        help="Output as GeoJSON (pipe into 'geo.py interact --geojson -')"
    )

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
    route_parser.add_argument(
        "--json", "-j",
        action="store_true",
        help="Output as JSON"
    )
    route_parser.add_argument(
        "--geojson", "-g",
        action="store_true",
        help="Output as GeoJSON (pipe into 'geo.py interact --geojson -')"
    )

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
        "--script", "-s",
        help="JavaScript file run after load, with map, L, layers and geo in scope"
    )
    interact_parser.add_argument(
        "--tiles", "-t",
        default="osm",
        choices=sorted(TILE_LAYERS),
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

    # destination command
    dest_parser = subparsers.add_parser(
        "destination",
        help="Calculate destination from start, bearing, and distance",
        description="Calculate the destination point given a start, bearing, and distance."
    )
    dest_parser.add_argument(
        "--start", "-s",
        required=True,
        help="Starting point (coordinates or address)"
    )
    dest_parser.add_argument(
        "--bearing", "-b",
        type=float,
        required=True,
        help="Bearing in degrees from north (0-360)"
    )
    dest_parser.add_argument(
        "--distance", "-d",
        type=float,
        required=True,
        help="Distance to travel"
    )
    dest_parser.add_argument(
        "--unit", "-u",
        default="miles",
        choices=["mi", "miles", "km", "kilometers", "m", "meters", "ft", "feet", "nm"],
        help="Distance unit (default: miles)"
    )
    dest_parser.add_argument(
        "--json", "-j",
        action="store_true",
        help="Output as JSON"
    )
    dest_parser.add_argument(
        "--geojson", "-g",
        action="store_true",
        help="Output as GeoJSON (pipe into 'geo.py interact --geojson -')"
    )

    # validate command
    validate_parser = subparsers.add_parser(
        "validate",
        help="Validate coordinates",
        description="Check if coordinates are valid lat/lng values."
    )
    validate_parser.add_argument(
        "coordinates",
        help="Coordinates to validate as 'lat,lng'"
    )
    validate_parser.add_argument(
        "--json", "-j",
        action="store_true",
        help="Output as JSON"
    )

    # ip command
    ip_parser = subparsers.add_parser(
        "ip",
        help="Get location from IP address",
        description="Geolocate an IP address (or your current IP if none specified)."
    )
    ip_parser.add_argument(
        "ip",
        nargs="?",
        default=None,
        help="IP address to locate (optional, defaults to your IP)"
    )
    ip_parser.add_argument(
        "--json", "-j",
        action="store_true",
        help="Output as JSON"
    )
    ip_parser.add_argument(
        "--geojson", "-g",
        action="store_true",
        help="Output as GeoJSON (pipe into 'geo.py interact --geojson -')"
    )

    # bbox command
    bbox_parser = subparsers.add_parser(
        "bbox",
        help="Calculate bounding box from center and radius",
        description="Calculate a bounding box given a center point and radius. Returns GeoJSON."
    )
    bbox_parser.add_argument(
        "--center", "-c",
        required=True,
        help="Center point (coordinates or address)"
    )
    bbox_parser.add_argument(
        "--radius", "-r",
        type=float,
        required=True,
        help="Radius from center"
    )
    bbox_parser.add_argument(
        "--unit", "-u",
        default="miles",
        choices=["mi", "miles", "km", "kilometers", "m", "meters", "ft", "feet"],
        help="Radius unit (default: miles)"
    )
    bbox_parser.add_argument(
        "--geojson", "-g",
        action="store_true",
        help="Output GeoJSON only (with --json) or include GeoJSON (without --json)"
    )
    bbox_parser.add_argument(
        "--json", "-j",
        action="store_true",
        help="Output as JSON"
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
        "destination": cmd_destination,
        "validate": cmd_validate,
        "ip": cmd_ip,
        "bbox": cmd_bbox,
    }

    handler = commands.get(args.command)
    if handler:
        return handler(client, args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
