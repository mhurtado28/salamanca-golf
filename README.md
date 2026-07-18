# Ocean maps — Copernicus CDS (SST, salinidad, clorofila)

Descarga de un día/mes de variables oceánicas desde el **Climate Data Store (CDS)** con `cdsapi`, recorte al bbox del Caribe colombiano y mapas 2D con mapa base sencillo.

## Bbox

| Límite | Valor     |
|--------|-----------|
| Norte  | 11.60° N  |
| Sur    | 10.70° N  |
| Este   | -73.90° W |
| Oeste  | -75.40° W |

## Promedios mensuales desde diarios (SST + clorofila)

Se descargan **todos los días** del año desde CDS, se conservan los recortes diarios del bbox y luego se calcula el **promedio mensual** (no se usan productos mensuales de CDS).

```bash
python3 scripts/daily_to_monthly_sst_chl.py --year 2025
python3 scripts/daily_to_monthly_sst_chl.py --year 2024
# Solo regenerar mapas/promedios si ya están los diarios:
python3 scripts/daily_to_monthly_sst_chl.py --year 2025 --plot-only
```

| Variable | Dataset CDS diario | Salida |
|----------|--------------------|--------|
| SST | `satellite-sea-surface-temperature` L4 v3.0 | `data/daily_YYYY/sst_YYYY-MM-DD.nc` |
| Clorofila-a | `satellite-ocean-colour` `chlor_a` v6.0 | `data/daily_YYYY/chl_YYYY-MM-DD.nc` |
| Promedios | media aritmética de días disponibles | `data/monthly_from_daily_YYYY/` |
| Mapas | solo SST y clorofila | `figures/monthly_YYYY/` |

## Prueba de un día (2023-07-20)

| Variable | Dataset CDS | Tipo |
|----------|-------------|------|
| SST | `satellite-sea-surface-temperature` (L4 combined, v3.0) | Satélite |
| Clorofila-a | `satellite-ocean-colour` (`chlor_a`, v6.0, 4 km) | Ocean Colour satélite |
| Salinidad | `reanalysis-oras5` (`sea_surface_salinity`, operacional) | Reanálisis mensual |

## Autenticación (no va en el repo)

Opción A — GitHub Secrets del repositorio:

- `CDS_URL` = `https://cds.climate.copernicus.eu/api`
- `CDS_KEY` = token de https://cds.climate.copernicus.eu/profile

Opción B — archivo local `~/.cdsapirc`:

```text
url: https://cds.climate.copernicus.eu/api
key: <tu_token>
```

La primera vez hay que aceptar las licencias de cada dataset en la web CDS (el script intenta aceptarlas por API si faltan).

## Cómo regenerar

```bash
python3 -m pip install -r requirements.txt
python3 scripts/download_and_map_ocean.py --source cds
```

Fallback público (sin CDS):

```bash
python3 scripts/download_and_map_ocean.py --source erddap
```

## Salidas

- NetCDF recortados: `data/*_cds_YYYY-MM-DD.nc`
- Mapas: `figures/`
