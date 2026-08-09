# Data attribution

`cities15000.json` is derived from the [GeoNames](https://www.geonames.org/)
`cities15000` dump (all cities with a population above 15,000) combined with
`admin1CodesASCII.txt` for region names.

GeoNames data is licensed under
[Creative Commons Attribution 4.0](https://creativecommons.org/licenses/by/4.0/).

The vendored file keeps only `[name, state, country, population]` per city; the
`alternatenames` column and all geographic coordinates are dropped. Regenerate it
with:

```
python scripts/build_city_index.py
```
