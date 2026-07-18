# Ocean maps — Copernicus CDS (SST, salinidad, clorofila)

Descarga de un día/mes de variables oceánicas desde el **Climate Data Store (CDS)** con `cdsapi`, recorte al bbox del Caribe colombiano y mapas 2D con mapa base sencillo.

## Bbox

| Límite | Valor     |
|--------|-----------|
| Norte  | 11.32° N  |
| Sur    | 11.00° N  |
| Este   | -74.20° W |
| Oeste  | -74.83° W |

## Fecha

**2023-07-20** (SST y clorofila diarias; salinidad ORAS5 = media de julio 2023)

## Datasets CDS

| Variable | Dataset CDS | Tipo |
|----------|-------------|------|
| SST | `satellite-sea-surface-temperature` (L4 combined, v3.0) | Satélite |
| Clorofila-a | `satellite-ocean-colour` (`chlor_a`, v6.0, 4 km) | Ocean Colour satélite |
| Salinidad | `reanalysis-oras5` (`sea_surface_salinity`, operacional) | Reanálisis mensual (CDS no ofrece SSS satélite diaria) |

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
