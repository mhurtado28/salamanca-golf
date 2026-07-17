# Ocean maps — SST, salinidad y clorofila

Descarga de un día de variables oceánicas 2D sobre un bbox costero (cerca de Santa Marta, Colombia) y generación de mapas con mapa base sencillo (tierra/costa Natural Earth).

## Bbox

| Límite | Valor      |
|--------|------------|
| Norte  | 11.32° N   |
| Sur    | 11.00° N   |
| Este   | -74.20° W  |
| Oeste  | -74.83° W  |

## Día usado

**2023-07-20** — elegido porque ese día hay cobertura conjunta de clorofila óptica (sin nubes totales), SST MUR y al menos algunos píxeles SMOS de salinidad en/cerca del bbox.

## Fuentes (NOAA CoastWatch ERDDAP)

| Variable | Producto | Dataset ID |
|----------|----------|------------|
| SST | JPL MUR ~1 km | `jplMURSST41` |
| Salinidad | SMOS MIRAS daily ~0.25° | `noaacwSMOSsssDaily` |
| Clorofila-a | MODIS Aqua L3 4 km | `erdMH1chla1day_R2022NRT` |

## Cómo regenerar

```bash
python3 -m pip install -r requirements.txt
python3 scripts/download_and_map_ocean.py
```

Salidas:

- NetCDF: `data/sst_YYYY-MM-DD.nc`, `data/sss_*.nc`, `data/chl_*.nc`
- Mapas: `figures/*_YYYY-MM-DD.png` y panel combinado `figures/ocean_vars_*.png`

## Nota sobre salinidad

SMOS es grueso (~0.25°) y suele tener huecos cerca de la costa; en este bbox solo aparecen pocos píxeles válidos. SST y clorofila tienen mucha más resolución espacial.
