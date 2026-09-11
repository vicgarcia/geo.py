---
name: geo
description: CLI tool for working with latitude/longitude and geography - geocoding, distance calculations, and IP geolocation
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
```

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

**This command blocks.** Only run it when the user wants to look at a map, and tell
them the URL. Use `--no-open` if a browser should not be launched.

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

## Smart Location Parsing

Commands that accept locations understand both formats:
- Raw coordinates: `"40.7128,-74.0060"` or `"40.7128 -74.0060"`
- Addresses: `"New York City"` or `"Tokyo, Japan"`

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

Every location-producing command accepts `--geojson`, which pipes straight into the map:
```bash
geo.py route --from A --to B --geojson | geo.py interact --geojson -
```
`route --geojson` carries the real road geometry, not just endpoints.

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
