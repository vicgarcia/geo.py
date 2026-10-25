# geo.py

A CLI tool for working with latitude/longitude and geography. Built for agents and humans who need to work with geographic data.

## Features

- **Geocode** - Convert addresses to coordinates
- **Reverse Geocode** - Convert coordinates to addresses
- **Distance** - Calculate distance between two points
- **Route** - Turn-by-turn driving, walking, and cycling directions
- **Interact** - Serve an interactive Leaflet map on localhost
- **GeoJSON** - Geocoding, reverse geocoding, distance and destination calculations, and routes can emit GeoJSON
- **Destination** - Find a point given start, bearing, and distance
- **Validate** - Check if coordinates are valid

**No API key required.** Uses OpenStreetMap's Nominatim for geocoding and Valhalla (FOSSGIS) for routing.

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
  Map:        https://www.google.com/maps?q=38.8976387,-77.0365528
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
geo.py distance --from "Paris" --to "London" --json   # every unit
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
geo.py interact --center "Denver" --no-open
```

Anything that emits GeoJSON pipes straight in:

```bash
geo.py route --from "Seattle" --to "Portland, OR" --geojson | geo.py interact --geojson -
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

The server binds to `127.0.0.1` only, picks a free port if the requested one is busy,
and runs until you press Ctrl+C.

### destination

Calculate where you end up starting from a point, traveling a bearing for a distance.
The destination address is best-effort: a failed lookup warns on stderr but the
calculated coordinates are still returned. Use `--no-address` to skip the lookup;
with coordinate input, this command then works offline.

```bash
geo.py destination --start "Seattle" --bearing 180 --distance 100 --unit km
geo.py destination --start "40.7128,-74.0060" --bearing 270 --distance 50 --no-address
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
    Map:        https://www.google.com/maps?q=46.70434,-122.330062
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

JSON location results include Google Maps URLs (`maps_url`); routes include a `directions_url`. GeoJSON output focuses on geometry and feature properties instead.

```bash
geo.py geocode "NYC" --json | jq -r '.maps_url'
# https://www.google.com/maps?q=40.7127281,-74.0060152
```

## GeoJSON Output

`geocode`, `reverse`, `distance`, `route`, and `destination` accept `--geojson`,
emitting a Feature or FeatureCollection ready to pipe into `geo.py interact --geojson -`.
GeoJSON takes precedence when both `--json` and `--geojson` are supplied:

```bash
geo.py geocode "Tokyo" --geojson
geo.py route --from A --to B --geojson
geo.py distance --from A --to B --geojson    # both points plus the line between
```

Note that `route --geojson` includes the real road geometry, decoded from Valhalla's
polyline, not just the endpoints.

## JSON Output

All commands except `interact` support `--json` / `-j` for machine-readable output.
`interact --geojson` reads map data; it is not an output option:

```bash
geo.py geocode "NYC" --json | jq '.latitude, .longitude'
```

## Services Used

| Feature | Service | Rate Limits |
|---------|---------|-------------|
| Geocoding | [Nominatim](https://nominatim.org/) (OpenStreetMap) | 1 req/sec |
| Routing | [Valhalla](https://valhalla1.openstreetmap.de/) (FOSSGIS public instance) | fair use; 1500 km max route |
| Map tiles | OpenStreetMap, OpenTopoMap, CyclOSM, HOT, Esri | fair use; keep attribution |

No API keys required. Forward and reverse Nominatim lookups share automatic
1.1-second pacing within each process. When launching separate processes in a loop,
still wait at least 1.1 seconds between calls; concurrent processes do not share a limiter.

## Errors and Validation

Errors and warnings go to stderr; stdout is reserved for results. Failures exit 1
(argument syntax errors exit 2). Out-of-range coordinates are rejected with exit 1.
Map inputs are checked for valid GeoJSON
structure, finite coordinates, and closed polygon rings before the server starts.
Simplification preserves a ring unchanged if simplifying would collapse it below
three distinct vertices; it does not guarantee topology preservation.

## Development

Run the offline regression suite (service calls are mocked):

```bash
uv run --with 'geopy>=2.4.0' --with 'requests>=2.31.0' python -m unittest discover -s tests -v
```

CI runs this suite before creating a release.

## Examples for Agents

```bash
# Get coordinates for an address
geo.py geocode "Central Park, NYC" --json | jq -r '"\(.latitude),\(.longitude)"'

# Calculate driving direction
geo.py distance --from "$START" --to "$END" --json | jq '.bearing.cardinal'

# Find what's 10km north
geo.py destination --start "$LOCATION" --bearing 0 --distance 10 --unit km --json
```

## Dependencies

- `geopy>=2.4.0` - Geocoding and distance calculations
- `requests>=2.31.0` - HTTP client for routing

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
