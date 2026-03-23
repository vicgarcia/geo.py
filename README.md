# geo.py

A CLI tool for working with latitude/longitude and geography. Built for agents and humans who need to work with geographic data.

## Features

- **Geocode** - Convert addresses to coordinates
- **Reverse Geocode** - Convert coordinates to addresses
- **Distance** - Calculate distance between two points
- **Destination** - Find a point given start, bearing, and distance
- **Validate** - Check if coordinates are valid
- **IP Geolocation** - Get location from an IP address

**No API key required.** Uses OpenStreetMap's Nominatim for geocoding and ip-api.com for IP geolocation.

## Installation

```bash
# Just run it - uv handles dependencies automatically
./geo.py --help

# Or add to PATH
cp geo.py/geo.py ~/.local/bin/geo
```

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

## JSON Output

All commands support `--json` / `-j` for machine-readable output:

```bash
geo.py geocode "NYC" --json | jq '.latitude, .longitude'
```

## Services Used

| Feature | Service | Rate Limits |
|---------|---------|-------------|
| Geocoding | [Nominatim](https://nominatim.org/) (OpenStreetMap) | 1 req/sec |
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
