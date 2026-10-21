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
