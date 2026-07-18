# Ocean maps — SST, salinidad y clorofila (satélite / Ocean Color)

Descarga de un día de variables oceánicas **satélite** sobre un bbox ampliado del Caribe colombiano, y mapas 2D con mapa base sencillo.

## Bbox ampliado

| Límite | Valor     |
|--------|-----------|
| Norte  | 12.5° N   |
| Sur    | 10.0° N   |
| Este   | -73.0° W  |
| Oeste  | -76.5° W  |

(Antes era ~0.3°×0.6°; ahora ~2.5°×3.5°.)

## Día usado

**2023-07-20**

## Fuente por defecto (sin login): satélite + Ocean Color

| Variable | Producto | Origen |
|----------|----------|--------|
| SST | JPL MUR L4 (~1 km) | GHRSST / ERDDAP `jplMURSST41` |
| Salinidad | SMOS MIRAS daily (~0.25°) | satélite / ERDDAP `noaacwSMOSsssDaily` |
| Clorofila-a | MODIS Aqua L3 4 km | **NASA Ocean Color** / ERDDAP `erdMH1chla1day_R2022NRT` |

```bash
python3 -m pip install -r requirements.txt
python3 scripts/download_and_map_ocean.py --source erddap
```

## Copernicus Marine (opcional, satélite / Ocean Colour)

Si quieres descargar desde Copernicus, define tus credenciales y corre:

```bash
export COPERNICUSMARINE_SERVICE_USERNAME="tu_usuario"
export COPERNICUSMARINE_SERVICE_PASSWORD="tu_password"
python3 scripts/download_and_map_ocean.py --source copernicus
```

Productos configurados (satélite / Ocean Colour, no reanálisis):

- SST: `cmems_obs-sst_glo_phy_nrt_l4_P1D-m`
- SSS: `cmems_obs-mob_glo_phy-sss_nrt_multiobs_0.25deg_P1D`
- CHL: `cmems_obs-oc_glo_bgc-plankton_nrt_l3-multi-4km_P1D`

## Salidas

- NetCDF: `data/`
- Mapas: `figures/`
