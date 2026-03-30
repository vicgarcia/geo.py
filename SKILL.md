---
name: geo
description: CLI tool for working with latitude/longitude and geography - geocoding, distance calculations, and IP geolocation
compatibility: Requires 'geo.py' script in PATH. No API keys needed. Uses Nominatim (OpenStreetMap) and ip-api.com.
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

All commands that return coordinates include a `maps_url` field - a clickable link that opens the location in a maps application. **Always provide this URL to the user** so they can visualize the location.

Example output:
```
Map: maps://?ll=40.7827725,-73.9653627&z=18
```

In JSON output:
```json
{
  "latitude": 40.7827725,
  "longitude": -73.9653627,
  "maps_url": "maps://?ll=40.7827725,-73.9653627&z=18"
}
```

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

4. **Get bearing as cardinal direction**:
   ```bash
   geo.py distance --from A --to B --json | jq '.bearing.cardinal'
   ```

5. **Chain with other tools**:
   ```bash
   # Get weather for an IP's location
   coords=$(geo.py ip 8.8.8.8 --json | jq -r '"\(.latitude),\(.longitude)"')
   weather.py current --location "$coords"
   ```

## Distance Units

`mi` (miles), `km` (kilometers), `m` (meters), `ft` (feet), `nm` (nautical miles)
