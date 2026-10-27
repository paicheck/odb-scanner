# Vendored: Chart.js

The dashboard used to load Chart.js from `https://cdn.jsdelivr.net`, and the
Content-Security-Policy had to allow that origin. Two problems:

1. A garage laptop may have no internet, in which case every chart silently
   disappeared.
2. The same CSP did **not** allow inline scripts (`script-src 'self'
   https://cdn.jsdelivr.net` and no nonce/hash), so all of the dashboard's
   inline chart-initialisation code was refused by the browser. The library
   loaded, but nothing ever drew. That was the real "the charts don't work"
   bug.

Chart.js is now served from this directory, so `script-src 'self'` is enough
and the charts work offline.

| | |
|---|---|
| Package | `chart.js` |
| Version | `4.5.1` |
| File | `dist/chart.umd.min.js` (UMD build) |
| Source | `https://cdn.jsdelivr.net/npm/chart.js@4.5.1/dist/chart.umd.min.js` |
| Downloaded | 2026-10-21 |
| SHA-256 | `48444a82d4edcb5bec0f1965faacdde18d9c17db3063d042abada2f705c9f54a` |
| License | MIT (Chart.js Contributors) |

To update: download the same path for the new version, replace the file, and
update the version/hash/date in this table. Do not edit the minified file.

# Vendored: Leaflet

The overview's car-position map. Served from here for the same reasons as
Chart.js (`script-src 'self'`, works offline). Only the map *tiles* come from
outside, from OpenStreetMap, and only when `web.map_tiles` is `osm`.

| | |
|---|---|
| Package | `leaflet` |
| Version | `1.9.4` |
| Files | `leaflet/leaflet.js`, `leaflet/leaflet.css` (from `dist/`) |
| Source | `https://unpkg.com/leaflet@1.9.4/dist/` |
| Downloaded | 2026-10-27 |
| SHA-256 (hex) | js `db49d009c841f5ca34a888c96511ae936fd9f5533e90d8b2c4d57596f4e5641a`, css `a7837102824184820dfa198d1ebcd109ff6d0ff9a2672a074b9a1b4d147d04c6` |
| SRI (as published by leafletjs.com) | js `sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=`, css `sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=` |
| License | BSD-2-Clause (Volodymyr Agafonkin, CloudMade) |

The CSS references marker and layer-control images that are not vendored:
the map draws its own circle markers and has no layer control.
