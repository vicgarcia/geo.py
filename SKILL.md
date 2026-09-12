---
name: geo
description: CLI tool for geography and maps - geocoding, reverse geocoding, distance and bearing, turn-by-turn directions, boundary polygons, IP geolocation, GeoJSON output, and an interactive Leaflet map served on localhost
compatibility: Requires 'geo.py' script in PATH. No API keys needed. Uses Nominatim (OpenStreetMap), Valhalla (FOSSGIS), OSM/Esri tiles, and ip-api.com.
---

# geo.py

A geographic utility tool for working with coordinates, addresses, and locations.

## Commands

### geocode
Convert address to coordinates:
```bash
geo.py geocode "1600 Pennsylvania Ave, Washington DC"
geo.py geocode "Tokyo, Japan" --json
geo.py geocode "Colorado, USA" --geojson --polygon
geo.py geocode "Switzerland" --geojson --polygon --simplify 0.01
```

`--polygon` returns whatever geometry OpenStreetMap actually holds for the place: a
`Polygon`/`MultiPolygon` for an area (state, city, building footprint), but a `Point` for
anything mapped as a single node - a peak, a small POI. **Check `.geometry.type` if you
need an area**; there is no error or warning when you get a point back:

```bash
geo.py geocode "Colorado, USA" --geojson --polygon | jq -r '.geometry.type'   # Polygon
geo.py geocode "Mount Rainier" --geojson --polygon | jq -r '.geometry.type'   # Point
```

Boundaries can be large - Switzerland is 1.3 MB raw - so pair it with
`--simplify DEGREES` (Douglas-Peucker; `0.01` is roughly 1 km and typically cuts size by
10-50x while keeping computed areas within ~0.3% of official figures).

### reverse
Convert coordinates to address:
```bash
geo.py reverse "40.7128,-74.0060"
```

### distance
Calculate distance between two points (accepts addresses or coordinates):
```bash
geo.py distance --from "New York" --to "Los Angeles"
geo.py distance --from "40.7128,-74.0060" --to "34.0522,-118.2437" --unit km
```

### route
Get turn-by-turn driving/walking/cycling directions:
```bash
geo.py route --from "Seattle" --to "Portland, OR"
geo.py route --from "Times Square" --to "Central Park" --mode walking --unit km
geo.py route --from "Boston" --to "NYC" --via "Hartford, CT" --no-steps
geo.py route --from "Seattle" --to "Bellevue, WA" --json
```

Modes: `driving` (default), `walking`, `cycling`, `motorcycle`, `truck`.

Output includes total distance, duration, whether the route uses tolls/ferries/highways,
a Google Maps directions link, and numbered turn-by-turn steps. Use `--no-steps` when you
only need the summary.

Each step includes `instruction`, `street`, `toward` (the street it puts you on when the
instruction text doesn't name it), `exit`, `toll`, `distance`, `cumulative_distance`, and
`duration_seconds`.

### interact
Serve an interactive Leaflet map on localhost (blocks until Ctrl+C):
```bash
geo.py interact --center "Seattle" --zoom 12
geo.py interact --marker "Space Needle:Start here" --marker "Pike Place Market"
geo.py route --from "Seattle" --to "Portland, OR" --geojson | geo.py interact --geojson -
geo.py interact --center "Zermatt" --tiles satellite --script viz.js
```

Tiles: `osm` (default), `topo`, `cyclosm`, `humanitarian`, `light`, `dark`,
`satellite`, `terrain`. All keyless.

**This command blocks until interrupted.** Run it in the background, or the session
hangs. It prints the URL it bound to - report that URL to the user:

```
  Serving map at http://127.0.0.1:8765/
  3 features loaded  |  script: yes
```

Defaults to port 8765 and **falls back to a free port if that is taken**, so read the
printed URL rather than assuming 8765. `--no-open` suppresses launching a browser.
`--marker` splits on the *first* colon: `"LOCATION:Popup text"`.

Styling follows the simplestyle-spec (`marker-color`, `stroke`, `fill`, ...), with
`title`/`description` becoming the popup. `--script FILE.js` runs after load with
`map`, `L`, `layers` and `geo` in scope for anything the flags don't cover.

### destination
Calculate endpoint from start + bearing + distance:
```bash
geo.py destination --start "Seattle" --bearing 180 --distance 100 --unit km
```

### validate
Check if coordinates are valid:
```bash
geo.py validate "40.7128,-74.0060"
```

### ip
Get location from IP address:
```bash
geo.py ip              # Your IP
geo.py ip 8.8.8.8      # Specific IP
```

### bbox
Calculate bounding box from center + radius (returns GeoJSON):
```bash
geo.py bbox --center "NYC" --radius 5 --unit km
geo.py bbox --center "40.7128,-74.006" --radius 1 --unit miles --json --geojson
```

## Wrong-Match Protection

Nominatim does free-form search and **will silently return a confidently wrong place**.
`"Washington, USA"` resolves to Washington **D.C.**, not the state. `"134 Angell St,
Providence, RI"` resolves to a same-named street in Woonsocket, 15 miles away. Nothing
errors - you just get the wrong coordinates. Three tools address this.

**1. Ambiguity warnings are automatic.** When the runner-up scores within 15% of the
winner, a warning goes to **stderr** (stdout stays clean, so pipes are unaffected):

```
Warning: 'Springfield' is ambiguous - two close matches
  using:   Springfield, Sangamon County, Illinois, United States
  also:    Springfield, Hampden County, Massachusetts, United States (administrative)
```

This fires for any address input, including `distance`, `route` and `interact`.

**2. `--limit N` lists the candidates.** The right answer is often the runner-up:

```bash
geo.py geocode "Washington, USA" --limit 5
#   1. Washington, District of Columbia   (city, 0.815)
#   2. Washington, United States          (administrative, 0.764)  <- the state
```

`--json` carries the same list under `alternatives`, each with `address`, `type`,
`class`, `importance` and coordinates.

**3. `--near` constrains the search area.** The reliable fix for street addresses:

```bash
geo.py geocode "134 Angell St, Providence, RI"            # -> Woonsocket. Wrong.
geo.py geocode "134 Angell St" --near "Providence, RI"    # -> Providence. Correct.
geo.py geocode "Main St" --near "47.60,-122.33" --within 5 --unit km
```

`--within` defaults to 25 miles; `--near` takes an address or coordinates. The search is
*bounded*, so a query with no match in the area fails rather than wandering.

**4. `--expect-type` fails loudly instead of silently.** Best guardrail for scripts:

```bash
geo.py geocode "Washington, USA" --expect-type administrative
# Error: expected administrative but matched city/place    (exit 1)
geo.py geocode "Washington State, USA" --expect-type administrative   # exit 0
```

Takes a comma-separated list, checked against Nominatim's `type`, `class` and
`addresstype`. Common values: `administrative` (state/country/county/city boundary),
`city`, `town`, `village`, `building`, `house`, `peak`.

**Do not** try to judge a match by `importance` alone - it is ~0 for *all* street
addresses whether right or wrong (a correct address scored 0.00007, a wrong one 0.00006).
Use the tools above, and echo the resolved address back to the user.

## Rate Limits and Batching

These are free, shared services. When geocoding more than a handful of places, **sleep
~1.1s between calls** or Nominatim will start refusing them.

| Service | Used by | Limit |
|---------|---------|-------|
| Nominatim | geocode, reverse, any address input | 1 request/second |
| Valhalla (FOSSGIS) | route | fair use; 1500 km max route |
| ip-api.com | ip | 45 requests/minute |
| OSM/Esri tiles | interact | fair use; keep attribution |

Passing **coordinates instead of addresses skips geocoding entirely** - it is faster and
consumes no quota, so resolve a place once and reuse its coordinates in a loop:

```bash
hub=$(geo.py geocode "Seattle, WA" --json | jq -r '"\(.latitude),\(.longitude)"')
geo.py distance --from "$hub" --to "47.61,-122.20" --json   # no network geocoding
```

## Errors

Failures print `Error: <message>` and exit **1**; success exits **0**. A geocode with no
match prints `No results found for: ...` and also exits 1. Check the exit status rather
than parsing prose.

## Smart Location Parsing

Commands that accept locations understand both formats:
- Raw coordinates: `"40.7128,-74.0060"` or `"40.7128 -74.0060"`
- Addresses: `"New York City"` or `"Tokyo, Japan"`

Southern-hemisphere coordinates that begin with a minus sign work normally
(`geo.py reverse "-33.87,151.21"`, `--to "-33.87,151.21"`) - quote them as usual.

## Maps Links

All commands that return coordinates include a `maps_url` field - a Google Maps link to view the location. **Always provide this URL to the user as a clickable markdown link** so they can visualize the location.

Example output:
```
Map: https://www.google.com/maps?q=40.7827725,-73.9653627
```

In JSON output:
```json
{
  "latitude": 40.7827725,
  "longitude": -73.9653627,
  "maps_url": "https://www.google.com/maps?q=40.7827725,-73.9653627"
}
```

**For chat sessions:** Format the URL as a clickable markdown link for the user:
```
[View on Google Maps](https://www.google.com/maps?q=40.7827725,-73.9653627)
```

## GeoJSON Output

Every location-producing command accepts `--geojson` and writes a Feature or
FeatureCollection to stdout, ready to pipe into the map:

```bash
geo.py geocode "Tokyo" --geojson
geo.py route --from A --to B --geojson | geo.py interact --geojson -
geo.py distance --from A --to B --geojson      # both points plus the line between
geo.py bbox --center "NYC" --radius 5 --geojson
```

`route --geojson` carries the real road geometry decoded from Valhalla's polyline, not
just endpoints. `--geojson` takes precedence over `--json` when both are passed.

`interact --geojson` accepts a FeatureCollection, a bare Feature, a raw geometry, or `-`
for stdin, and is repeatable. It rebuilds the collection from `features`, so **foreign
top-level members are dropped** - put configuration in a `--script` file, not alongside
`features`.

## JSON Output

All commands support `--json` for machine-readable output:
```bash
geo.py geocode "NYC" --json | jq '.latitude'
```

## Tips for Agents

1. **Always share the maps URL** with users when displaying location results - it lets them see the location visually.

2. **Validate user input** before using coordinates:
   ```bash
   geo.py validate "$coords" && echo "Valid"
   ```

3. **Extract just coordinates** from geocode:
   ```bash
   geo.py geocode "Address" --json | jq -r '"\(.latitude),\(.longitude)"'
   ```

4. **Route summary without the step list** (much less output):
   ```bash
   geo.py route --from A --to B --no-steps --json | jq '{miles: .distance.miles, mins: (.duration_seconds/60)}'
   ```

5. **Share the directions URL** from `route` as a clickable markdown link, same as `maps_url`.

6. **Get bearing as cardinal direction**:
   ```bash
   geo.py distance --from A --to B --json | jq '.bearing.cardinal'
   ```

7. **Chain with other tools**:
   ```bash
   # Get weather for an IP's location
   coords=$(geo.py ip 8.8.8.8 --json | jq -r '"\(.latitude),\(.longitude)"')
   weather.py current --location "$coords"
   ```

## Distance Units

`mi` (miles), `km` (kilometers), `m` (meters), `ft` (feet), `nm` (nautical miles)
