---
name: geo
description: CLI tool for geography and maps - geocoding, reverse geocoding, distance and bearing, turn-by-turn directions, boundary polygons, GeoJSON output, and an interactive Leaflet map served on localhost
compatibility: Requires 'geo.py' script in PATH. No API keys needed. Uses Nominatim (OpenStreetMap), Valhalla (FOSSGIS), and OSM/Esri tiles.
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
geo.py geocode "Switzerland" --geojson --polygon
```

`--polygon` returns whatever geometry OpenStreetMap actually holds for the place: a
`Polygon`/`MultiPolygon` for an area (state, city, building footprint), but a `Point` for
anything mapped as a single node - a peak, a small POI. **Check `.geometry.type` if you
need an area**; there is no error or warning when you get a point back:

```bash
geo.py geocode "Colorado, USA" --geojson --polygon | jq -r '.geometry.type'   # Polygon
geo.py geocode "Mount Rainier" --geojson --polygon | jq -r '.geometry.type'   # Point
```

Boundaries can be large - Switzerland is 1.3 MB raw - so any outline over 2,000 points
is simplified automatically (Douglas-Peucker, tightest tolerance that fits) for both
`--geojson` and `--json`, with a note on stderr. Collapsed rings retain their original
vertices; simplification does not guarantee topology preservation.

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
geo.py route --from "Hollywood, FL" --to "Islamorada, FL" --avoid tolls,highways
geo.py route --from "Hollywood, FL" --to "Islamorada, FL" --shortest
```

Modes: `driving` (default), `walking`, `cycling`, `truck`. Options work with any mode:
`--avoid tolls,highways,ferries` (hard exclusions - if no other route exists the result
still uses them and stderr warns) and `--shortest` (distance instead of time). JSON output
echoes them as `avoid` and `shortest`.

Output includes total distance, duration, whether the route uses tolls/ferries/highways,
a Google Maps directions link, and numbered turn-by-turn steps. Use `--no-steps` when you
only need the summary.

Each step includes `instruction`, `type` (Valhalla's maneuver type), `location`
(`[lat, lng]` where the maneuver begins), `street`, `toward` (the street it puts you on
when the instruction text doesn't name it), `exit`, `toll`, `distance`,
`cumulative_distance`, and `duration_seconds`.

### interact
Serve an interactive Leaflet map on localhost (blocks until Ctrl+C):
```bash
geo.py interact --center "Seattle" --zoom 12
geo.py interact --marker "Space Needle:Start here" --marker "Pike Place Market"
geo.py route --from "Seattle" --to "Portland, OR" --geojson | geo.py interact --geojson -
geo.py interact --center "Zermatt" --tiles satellite
```

Tiles: `osm` (default), `satellite`, `light`, `dark`. All keyless.

**This command blocks until interrupted.** Run it in the background, or the session
hangs. It prints the URL it bound to - report that URL to the user:

```
  Serving map at http://127.0.0.1:8765/
  3 features loaded
```

Defaults to port 8765 and **falls back to a free port if that is taken**, so read the
printed URL rather than assuming 8765. `--no-open` suppresses launching a browser.
`--marker` splits on the *first* colon: `"LOCATION:Popup text"`.

Styling follows the simplestyle-spec (`marker-color`, `marker-symbol`, `stroke`, `fill`,
...), with `title`/`description` heading the popup. A point with `marker-symbol` renders as
a round badge; `route --geojson` uses this for a turn arrow at every maneuver.

## Wrong-Match Protection

Nominatim does free-form search and **will silently return a confidently wrong place**.
`"Washington, USA"` resolves to Washington **D.C.**, not the state. `"134 Angell St,
Providence, RI"` resolves to a same-named street in Woonsocket, 15 miles away. Nothing
errors - you just get the wrong coordinates. Four guardrails address this.

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

These are free, shared services. Forward and reverse Nominatim lookups share automatic
1.1-second pacing within a process. When launching separate CLI processes in a loop,
**sleep at least 1.1s between calls**; concurrent processes do not share a limiter.

| Service | Used by | Limit |
|---------|---------|-------|
| Nominatim | geocode, reverse, any address input | 1 request/second |
| Valhalla (FOSSGIS) | route | fair use; 1500 km max route |
| OSM/Esri tiles | interact | fair use |

Passing **coordinates instead of addresses skips geocoding entirely** - it is faster and
consumes no quota, so resolve a place once and reuse its coordinates in a loop:

```bash
hub=$(geo.py geocode "Seattle, WA" --json | jq -r '"\(.latitude),\(.longitude)"')
geo.py distance --from "$hub" --to "47.61,-122.20" --json   # no network geocoding
```

## Errors

Failures print `Error: <message>` to **stderr** and exit **1**; success exits **0**.
Argument syntax errors exit **2**. A geocode with no match also exits 1. Stdout stays
reserved for results. Out-of-range coordinates are rejected with exit 1. Check the
exit status rather than parsing prose.

## Smart Location Parsing

Commands that accept locations understand both formats:
- Raw coordinates: `"40.7128,-74.0060"` or `"40.7128 -74.0060"`
- Addresses: `"New York City"` or `"Tokyo, Japan"`

Southern-hemisphere coordinates that begin with a minus sign work normally
(`geo.py reverse "-33.87,151.21"`, `--to "-33.87,151.21"`) - quote them as usual.

## Maps Links

JSON location results include a `maps_url` field; routes include `directions_url`. GeoJSON output focuses on geometry rather than Maps URLs. **Always provide this URL to the user as a clickable markdown link** so they can visualize the location.

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

`geocode`, `reverse`, `distance`, and `route` accept `--geojson` and
write a Feature or FeatureCollection to stdout, ready to pipe into the map:

```bash
geo.py geocode "Tokyo" --geojson
geo.py route --from A --to B --geojson | geo.py interact --geojson -
geo.py distance --from A --to B --geojson      # both points plus the line between
```

`route --geojson` carries the real road geometry decoded from Valhalla's polyline, not
just endpoints. `--geojson` takes precedence over `--json` when both are passed.

`interact --geojson` accepts a FeatureCollection, a bare Feature, a raw geometry, or `-`
for stdin, and is repeatable. Invalid feature/geometry structure, non-finite coordinates,
and unclosed/degenerate rings are rejected before serving. It rebuilds the collection from `features`, so **foreign
top-level members are dropped**.

## JSON Output

All commands except `interact` support `--json` for machine-readable output:
```bash
geo.py geocode "NYC" --json | jq '.latitude'
```

## Tips for Agents

1. **Always share the maps URL** with users when displaying location results - it lets them see the location visually.

2. **Extract just coordinates** from geocode:
   ```bash
   geo.py geocode "Address" --json | jq -r '"\(.latitude),\(.longitude)"'
   ```

3. **Route summary without the step list** (much less output):
   ```bash
   geo.py route --from A --to B --no-steps --json | jq '{miles: .distance.miles, mins: (.duration_seconds/60)}'
   ```

4. **Share the directions URL** from `route` as a clickable markdown link, same as `maps_url`.

5. **Get bearing as cardinal direction**:
   ```bash
   geo.py distance --from A --to B --json | jq '.bearing.cardinal'
   ```

6. **Chain with other tools**:
   ```bash
   # Get weather for a place
   coords=$(geo.py geocode "Seattle, WA" --json | jq -r '"\(.latitude),\(.longitude)"')
   weather.py current --location "$coords"
   ```

## Distance Units

`mi` (miles), `km` (kilometers), `m` (meters), `ft` (feet), `nm` (nautical miles)
