# geo.py

A CLI tool for working with latitude/longitude and geography. Built for agents and humans who need to work with geographic data.

## Features

- **Geocode** - Convert addresses to coordinates
- **Reverse Geocode** - Convert coordinates to addresses
- **Distance** - Calculate distance between two points
- **Route** - Turn-by-turn driving, walking, and cycling directions
- **Interact** - Serve an interactive Leaflet map on localhost
- **GeoJSON** - Every command can emit GeoJSON to pipe into the map
- **Destination** - Find a point given start, bearing, and distance
- **Validate** - Check if coordinates are valid
- **IP Geolocation** - Get location from an IP address
- **Bounding Box** - Calculate bbox from center + radius (GeoJSON output)

**No API key required.** Uses OpenStreetMap's Nominatim for geocoding, Valhalla (FOSSGIS) for routing, and ip-api.com for IP geolocation.

## Installation

### Install for uv

```bash
# Clone the repo
git clone https://github.com/vicgarcia/geo.py

# Run directly (dependencies auto-installed)
uv run --script scripts/geo.py --help

# Install in PATH
cp scripts/geo.py ~/.local/bin/geo.py
chmod +x ~/.local/bin/geo.py
geo.py --help
```

### Install for Claude Desktop

Download `geo-v*.zip` from [Releases](https://github.com/vicgarcia/geo.py/releases).
Install in Claude Desktop as a skill.

## Commands

### geocode

Convert an address to coordinates.

```bash
geo.py geocode "1600 Pennsylvania Ave, Washington DC"
geo.py geocode "Tokyo, Japan" --json
```

Output:
```
  Geocode: 1600 Pennsylvania Ave, Washington DC
  ==========================================================
  Latitude:   38.8976387
  Longitude:  -77.0365528
  Address:    White House, 1600, Pennsylvania Avenue Northwest...
  Map:        maps://?ll=38.8976387,-77.0365528&z=18
```

**Avoiding wrong matches.** Free-form geocoding can return a confidently wrong place -
`"Washington, USA"` is Washington D.C., not the state. `geocode` warns on stderr when a
query is ambiguous, and offers three ways to pin it down:

```bash
geo.py geocode "Washington, USA" --limit 5              # list candidates
geo.py geocode "134 Angell St" --near "Providence, RI"  # constrain the search area
geo.py geocode "Washington State, USA" --expect-type administrative   # exit 1 on mismatch
```

`--near` takes an address or coordinates with `--within` (default 25 miles).
`--expect-type` accepts a comma-separated list checked against OSM's classification.

### reverse

Convert coordinates to an address.

```bash
geo.py reverse "40.7128,-74.0060"
geo.py reverse "51.5074, -0.1278" --json
```

### distance

Calculate distance between two points. Supports smart location parsing - use coordinates or addresses.

```bash
geo.py distance --from "New York" --to "Los Angeles"
geo.py distance --from "40.7128,-74.0060" --to "34.0522,-118.2437" --unit km
geo.py distance --from "Paris" --to "London" --all-units
```

Output:
```
  Distance Calculation
  ==========================================================
  From:       New York, United States
              (40.7127281, -74.0060152)
  To:         Los Angeles, Los Angeles County, California, United States
              (34.0536909, -118.242766)

  Distance:   2,450.859 mi
  Bearing:    273.69° (W)
```

### route

Get turn-by-turn directions between two or more points. Accepts coordinates or addresses.

```bash
geo.py route --from "Seattle" --to "Portland, OR"
geo.py route --from "Times Square" --to "Central Park" --mode walking --unit km
geo.py route --from "Boston" --to "NYC" --via "Hartford, CT"
geo.py route --from "Seattle" --to "Bellevue, WA" --no-steps
```

Travel modes: `driving` (default), `walking`, `cycling`, `motorcycle`, `truck`.

Output:
```
  Directions (driving)
  ==========================================================
  From:      Space Needle, 400, Broad Street, Seattle, Washington, United States
  To:        Bellevue, King County, Washington, United States

  Distance:   10.563 mi
  Duration:   15m
  Route uses: tolls, highways
  Map:        https://www.google.com/maps/dir/?api=1&origin=47.6205131,-122.3493036&destination=47.6144219,-122.192337&travelmode=driving

    1. Drive northeast.
       toward 5th Avenue North · 0.075 mi (1m) · 0.075 mi total
    2. Turn left onto 5th Avenue North.
       0.250 mi (45s) · 0.325 mi total
    3. Turn right onto Mercer Street.
       0.617 mi (1m) · 0.942 mi total
    ...
    6. Take exit 168B onto SR 520 toward Bellevue/Kirkland.
       toll · 6.793 mi (7m) · 8.942 mi total
```

Each step carries a detail line with whatever context applies: the street it puts you on
(when the instruction itself doesn't name it), the exit number, a toll marker, the step's
own distance and time, and the running total from the start.

Use `--no-steps` for just the summary, and `--json` for machine-readable output.

### interact

Serve a full-page interactive Leaflet map on localhost and open it in the browser.

```bash
geo.py interact --center "Seattle" --zoom 12
geo.py interact --marker "Space Needle:Start here" --marker "Pike Place Market"
geo.py interact --geojson places.json --tiles light
geo.py interact --center "Zermatt" --tiles satellite --zoom 14
geo.py interact --center "Denver" --script viz.js --no-open
```

Anything that emits GeoJSON pipes straight in:

```bash
geo.py route --from "Seattle" --to "Portland, OR" --geojson | geo.py interact --geojson -
geo.py bbox --center "NYC" --radius 5 --geojson | geo.py interact --geojson -
```

Features are styled from their properties using the
[simplestyle-spec](https://github.com/mapbox/simplestyle-spec): `marker-color`,
`marker-size`, `stroke`, `stroke-width`, `stroke-opacity`, `fill`, `fill-opacity`.
`title` and `description` become the popup; any other property is listed beneath it.

**Basemap tiles** (`--tiles`), all keyless:

| Value | Basemap |
|-------|---------|
| `osm` | OpenStreetMap standard (default) |
| `topo` | OpenTopoMap, contour lines (max zoom 17) |
| `cyclosm` | CyclOSM, cycling infrastructure |
| `humanitarian` | Humanitarian OSM Team style |
| `light` | Esri Light Gray Canvas (max zoom 16) |
| `dark` | Esri Dark Gray Canvas (max zoom 16) |
| `satellite` | Esri World Imagery |
| `terrain` | Esri World Topo |

**Custom JavaScript** (`--script`) runs after the map and data are ready, with `map`,
`L`, `layers` (`layers.data` is the loaded GeoJSON layer) and `geo` (the raw
FeatureCollection) in scope:

```javascript
// viz.js
L.circle([37.9, -122.06], {radius: 1500, color: 'crimson'})
 .addTo(map).bindPopup('coverage area');
console.log(geo.features.length + ' features');
map.fitBounds(layers.data.getBounds());
```

The server binds to `127.0.0.1` only, picks a free port if the requested one is busy,
and runs until you press Ctrl+C.

### destination

Calculate where you end up starting from a point, traveling a bearing for a distance.

```bash
geo.py destination --start "Seattle" --bearing 180 --distance 100 --unit km
geo.py destination --start "40.7128,-74.0060" --bearing 270 --distance 50
```

Output:
```
  Destination Calculation
  ==========================================================
  Start:      Seattle, King County, Washington, United States
              (47.6038321, -122.330062)
  Bearing:    180.0° (S)
  Distance:   100.0 km

  Destination:
    Latitude:   46.70434
    Longitude:  -122.330062
    Address:    Forest Road 7415, Lewis County, Washington, United States
    Map:        maps://?ll=46.70434,-122.330062&z=18
```

### validate

Check if coordinates are valid.

```bash
geo.py validate "40.7128,-74.0060"   # Valid
geo.py validate "91.0,181.0"          # Invalid - out of range
geo.py validate "0,0"                 # Valid but warns about Null Island
```

Returns exit code 0 for valid, 1 for invalid.

### ip

Get location from an IP address. Uses your current IP if none specified.

```bash
geo.py ip                # Your IP
geo.py ip 8.8.8.8        # Google DNS
geo.py ip --json
```

Output:
```
  IP Geolocation: 8.8.8.8
  ==========================================================
  Latitude:   39.03
  Longitude:  -77.5
  Map:        maps://?ll=39.03,-77.5&z=18

  City:       Ashburn
  Region:     Virginia
  Country:    United States (US)
  Timezone:   America/New_York

  ISP:        Google LLC
  Org:        Google Public DNS
```

### bbox

Calculate a bounding box from a center point and radius. Returns GeoJSON.

```bash
geo.py bbox --center "NYC" --radius 5 --unit km
geo.py bbox --center "40.7128,-74.006" --radius 1 --unit miles --json
geo.py bbox --center "Seattle" --radius 10 --unit km --json --geojson
```

Output:
```
  Bounding Box
  ==========================================================
  Center:     New York, United States
              (40.7127281, -74.0060152)
  Radius:     5.0 km
  Map:        maps://?ll=40.7127281,-74.0060152&z=18

  Bounds:
    North:    40.757753
    South:    40.667702
    East:     -73.946843
    West:     -74.065187

  bbox:       -74.065187,40.667702,-73.946843,40.757753
```

With `--json --geojson`, returns pure GeoJSON:
```json
{
  "type": "Feature",
  "bbox": [-74.025046, 40.698308, -73.986954, 40.727292],
  "geometry": {
    "type": "Polygon",
    "coordinates": [[[-74.025, 40.698], [-73.986, 40.698], ...]]
  },
  "properties": {
    "center": {"latitude": 40.7128, "longitude": -74.006},
    "radius_km": 1.60934
  }
}
```

## Smart Location Parsing

Most commands accept either raw coordinates or addresses:

| Input | Type |
|-------|------|
| `"40.7128,-74.0060"` | Raw coordinates (comma) |
| `"40.7128 -74.0060"` | Raw coordinates (space) |
| `"New York City"` | Address (auto-geocoded) |
| `"Tokyo, Japan"` | Address with region |

## Distance Units

| Unit | Aliases |
|------|---------|
| Miles | `mi`, `miles` |
| Kilometers | `km`, `kilometers` |
| Meters | `m`, `meters` |
| Feet | `ft`, `feet` |
| Nautical Miles | `nm` |

## Maps Links

All commands that return coordinates include a `maps_url` - a deep link that opens the location in a maps application. The `maps://` scheme works across platforms.

```bash
geo.py geocode "NYC" --json | jq -r '.maps_url'
# maps://?ll=40.7127281,-74.0060152&z=18
```

## GeoJSON Output

Every command that produces a location accepts `--geojson`, emitting a Feature or
FeatureCollection on stdout, ready to pipe into `geo.py interact --geojson -`:

```bash
geo.py geocode "Tokyo" --geojson
geo.py route --from A --to B --geojson
geo.py distance --from A --to B --geojson    # both points plus the line between
geo.py bbox --center "NYC" --radius 5 --geojson
```

Note that `route --geojson` includes the real road geometry, decoded from Valhalla's
polyline, not just the endpoints.

## JSON Output

All commands support `--json` / `-j` for machine-readable output:

```bash
geo.py geocode "NYC" --json | jq '.latitude, .longitude'
```

## Services Used

| Feature | Service | Rate Limits |
|---------|---------|-------------|
| Geocoding | [Nominatim](https://nominatim.org/) (OpenStreetMap) | 1 req/sec |
| Routing | [Valhalla](https://valhalla1.openstreetmap.de/) (FOSSGIS public instance) | fair use; 1500 km max route |
| Map tiles | OpenStreetMap, OpenTopoMap, CyclOSM, HOT, Esri | fair use; keep attribution |
| IP Geolocation | [ip-api.com](http://ip-api.com/) | 45 req/min |

No API keys required. Please respect rate limits.

## Examples for Agents

```bash
# Get coordinates for an address
geo.py geocode "Central Park, NYC" --json | jq -r '"\(.latitude),\(.longitude)"'

# Check if user input is valid coordinates
geo.py validate "$USER_INPUT" && echo "Valid" || echo "Invalid"

# Calculate driving direction
geo.py distance --from "$START" --to "$END" --json | jq '.bearing.cardinal'

# Find what's 10km north
geo.py destination --start "$LOCATION" --bearing 0 --distance 10 --unit km --json
```

## Dependencies

- `geopy>=2.4.0` - Geocoding and distance calculations
- `requests>=2.31.0` - HTTP client for IP geolocation

Managed automatically by `uv` via PEP 723 inline script metadata.

## Agent Skill

This project includes a `SKILL.md` file for use with AI coding agents.

### Installation

```bash
# Create skills directory
mkdir -p /path/to/agent/skills/geo

# Copy skill files
cp SKILL.md /path/to/agent/skills/geo/
cp -r scripts/ /path/to/agent/skills/geo/

# Ensure geo.py is in PATH
cp scripts/geo.py ~/.local/bin/
chmod +x ~/.local/bin/geo.py
```

The agent reads `SKILL.md` to understand available commands, parameters, and how to work with geographic data.
