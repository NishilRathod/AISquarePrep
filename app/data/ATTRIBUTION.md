# Data attribution

`cities15000.json` is derived from the [GeoNames](https://www.geonames.org/)
`cities15000` dump (all cities with a population above 15,000) combined with
`admin1CodesASCII.txt` for region names.

GeoNames data is licensed under
[Creative Commons Attribution 4.0](https://creativecommons.org/licenses/by/4.0/).

The vendored file keeps `[geonameid, name, state, country, population, latitude,
longitude]` per city; the `alternatenames` column and the remaining metadata are
dropped. Coordinates feed the anomaly sweep, and `geonameid` is the join key
between this index and the climate-normals artefact. Regenerate it with:

```
python scripts/build_city_index.py
```

## Climate normals

`climate_normals.bin.gz` is derived from
[Open-Meteo](https://open-meteo.com/)'s Historical Weather API, which serves the
ECMWF **ERA5** reanalysis. It holds, per city and per calendar month, the mean
and standard deviation of daily mean temperature and daily mean relative
humidity over a five-year window.

Open-Meteo data is licensed under
[Creative Commons Attribution 4.0](https://creativecommons.org/licenses/by/4.0/);
the free tier is for non-commercial use. ERA5 is generated using Copernicus
Climate Change Service information — neither ECMWF nor the European Commission
is responsible for any use of it here.

The artefact is joined to the city index **by position**, so the two must be
rebuilt together; `climate_normals.meta.json` carries a digest of the geonameids
it was built against and the service refuses to start on a mismatch. Regenerate
it with:

```
python scripts/build_climate_normals.py --cities 2000
```

The run is rate-limited and resumable — see the script's docstring.
