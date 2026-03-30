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
import re
import sys
from typing import Optional

import requests
from geopy.geocoders import Nominatim
from geopy.distance import geodesic, great_circle
from geopy.point import Point

# User agent for Nominatim (required)
USER_AGENT = "geo.py-cli/1.0"

CLI_EPILOG = """\
Examples:
  geo.py geocode "1600 Pennsylvania Ave, Washington DC"
  geo.py geocode "Tokyo, Japan" --json

  geo.py reverse 40.7128,-74.0060
  geo.py reverse "51.5074, -0.1278"

  geo.py distance --from "New York" --to "Los Angeles"
  geo.py distance --from "40.7128,-74.0060" --to "34.0522,-118.2437" --unit km

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

Distance Units:
  mi, miles     Miles (default)
  km            Kilometers
  m, meters     Meters
  ft, feet      Feet
  nm            Nautical miles
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

        return result["latitude"], result["longitude"], result["address"]

    def geocode(self, address: str) -> Optional[dict]:
        """
        Geocode an address to coordinates.

        Returns dict with lat, lng, address, and raw response.
        """
        try:
            location = self.geolocator.geocode(address, addressdetails=True)
            if not location:
                return None

            return {
                "latitude": location.latitude,
                "longitude": location.longitude,
                "address": location.address,
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


def _maps_url(lat: float, lng: float) -> str:
    """Generate Google Maps URL for coordinates."""
    return f"https://www.google.com/maps?q={lat},{lng}"


# ============================================================================
# Command handlers
# ============================================================================

def cmd_geocode(client: GeoClient, args: argparse.Namespace) -> int:
    """Handle the geocode command."""
    try:
        result = client.geocode(args.address)

        if not result:
            print(f"No results found for: {args.address}")
            return 1

        maps_url = _maps_url(result['latitude'], result['longitude'])

        if args.json:
            import json
            result["maps_url"] = maps_url
            print(json.dumps(result, indent=2))
        else:
            print(f"\n  Geocode: {args.address}")
            print("  " + "=" * 58)
            print(f"  Latitude:   {result['latitude']}")
            print(f"  Longitude:  {result['longitude']}")
            print(f"  Address:    {result['address']}")
            print(f"  Map:        {maps_url}")
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

        if args.json:
            import json
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

        if args.json:
            import json
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

        if args.json:
            import json
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

        if args.json:
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

        if args.json:
            import json
            # Return just the GeoJSON if requested
            if args.geojson:
                print(json.dumps(result["geojson"], indent=2))
            else:
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

            if args.geojson:
                import json
                print("  GeoJSON:")
                print(json.dumps(result["geojson"], indent=2))
                print()

        return 0
    except GeoError as e:
        print(f"Error: {e}")
        return 1


# ============================================================================
# Main CLI
# ============================================================================

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
        "--json", "-j",
        action="store_true",
        help="Output as JSON"
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

    # Parse args
    args = parser.parse_args()

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
