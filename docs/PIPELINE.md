# Pipeline en Codespace: ríos, lluvia, viento y batimetría

Todo corre dentro de un **GitHub Codespace** (máquina en la nube de GitHub):
las descargas no pasan por tu PC. Los pulsos fríos de TSM se tratan aparte
(con pasadas satelitales individuales, no con medias diarias).

## 1. Preparar el Codespace (una sola vez)

1. Token de ECMWF: entra a https://cds.climate.copernicus.eu/profile y copia tu token.
2. En GitHub: **Settings → Codespaces → Secrets → New secret**
   - `CDS_KEY` = tu token (repositorio: `mhurtado28/salamanca-golf`)
   - `CDS_URL` = `https://cds.climate.copernicus.eu/api`
3. Acepta una vez las licencias de cada dataset (botón *Accept* en la pestaña *Download*):
   - ERA5: https://cds.climate.copernicus.eu/datasets/reanalysis-era5-single-levels
   - GloFAS (está en EWDS, no en CDS): https://ewds.climate.copernicus.eu/datasets/cems-glofas-historical
     — el mismo token sirve para EWDS.
4. En el repo: **Code → Codespaces → Create codespace on `<rama>`**.
   El contenedor instala Python, GDAL/PROJ y `requirements.txt` solo.

## 2. Qué descarga solo y qué tienes que subir tú

| Dato | Fuente | Cómo llega | Script |
|---|---|---|---|
| Caudal Magdalena, Toribio, Córdoba | GloFAS v4 (0.05°, diario) | automático (EWDS) | `rivers_glofas.py` |
| Viento 10 m → índice de surgencia | ERA5 horario | automático (CDS) | `wind_era5.py` |
| Lluvia diaria | CHIRPS v3 (0.05°, solo tierra) | automático (CHC) | `rain_chirps_download.py` |
| Estaciones lluvia + caudal Calamar | IDEAM DHIME | **manual** → `data/ideam/raw/` | `ideam_dhime.py` |
| Batimetría + origen del dato | GEBCO_2025 + TID | **manual** → `data/bathymetry/raw/` | `bathymetry_gebco.py` |

**IDEAM (DHIME)** — http://dhime.ideam.gov.co/atencionciudadano/
- Precipitación diaria (`PTPM_CON`): aeropuertos Simón Bolívar (Santa Marta) y
  Ernesto Cortissoz (Barranquilla) + estaciones de la vertiente de la Sierra / Ciénaga.
- Caudal medio diario (`Q_MEDIA_D`): Magdalena en **Calamar**; Toribio y Córdoba
  si aparecen estaciones al filtrar por corriente.
- Sube los CSV tal cual (arrástralos al explorador de VS Code en el Codespace).

**GEBCO** — https://download.gebco.net: caja 10.90–11.40 N, 74.95–74.10 W,
formato NetCDF, marcar *GEBCO_2025 Grid* y *TID Grid*.

## 3. Correr

```bash
cd scripts
python rivers_glofas.py --start 2015 --end 2025
python wind_era5.py --start 2015 --end 2025
python rain_chirps_download.py --start 2015 --end 2025
python ideam_dhime.py            # después de subir los CSV de DHIME
python rain_analysis.py
python bathymetry_gebco.py       # después de subir GEBCO
```

O todo junto: `bash scripts/run_pipeline.sh`.
Para el periodo del paper (campañas 1996–1998) repite las descargas con
`--start 1995 --end 1998`. Cada script es reanudable: si se corta, vuelve a correrlo.
Con `--process-only` (ríos y viento) se regeneran tablas y figuras sin descargar.

## 4. Salidas

- `data/rivers/`: caudal diario por río, celdas GloFAS usadas, extremos anuales
  (máximo, mínimo, días > Q95 / < Q05), validación GloFAS vs IDEAM en Calamar (r, sesgo, KGE).
- `data/wind/`: viento diario, índice de surgencia de Ekman (golfo y punto), eventos
  de viento favorable a surgencia (UI > P90 por ≥ 2 días).
- `data/rain/`: lluvia diaria por cuenca, régimen mensual, extremos anuales
  (total, Rx1day, Rx5day, R20mm, días y total > P95), validación CHIRPS vs estaciones.
- `data/bathymetry/`: isóbatas GEBCO (CSV) y % de celdas con sondeo real vs interpolado.
- `figures/rivers|wind|rain|bathymetry/`: figuras correspondientes.

## 5. Antes de usar en el paper — revisar

- **Coordenadas de Toribio y Córdoba** (`config/pipeline.yaml`, marcadas VERIFICAR) y la
  figura `figures/rivers/control_celdas_glofas.png`: la celda elegida debe caer en el río.
- **GloFAS en ríos pequeños**: a 0.05° las cuencas de Toribio y Córdoba quedan mal
  resueltas; repórtalo como indicador y apóyate en el índice de lluvia por cuenca.
- **Cajas de cuencas** de CHIRPS son aproximadas: reemplazarlas por polígonos de HydroSHEDS.
- **Dirección mar afuera** del índice de surgencia (`viento.offshore_deg`): 0° (norte) para
  la costa O–E del golfo; para la costa N–S frente a Santa Marta sería 270° (oeste).
- **TID de GEBCO**: si la mayoría del golfo es "indirecta/interpolada", la comparación
  con la carta COL 407 y con la Fig. 20 del paper pierde fuerza; dilo en métodos.

## 6. Alternativa sin Codespace: GitHub Actions

El workflow `.github/workflows/pipeline.yml` corre los mismos pasos en la nube de
GitHub, sin mantener nada abierto, y sube los datos y figuras a la rama.

1. **Settings → Secrets and variables → Actions → New repository secret**:
   `CDS_KEY` = tu token de ECMWF.
2. Acepta las licencias de ERA5 y GloFAS (sección 1).
3. **Actions → "Pipeline ríos, lluvia, viento y batimetría" → Run workflow**,
   elige la rama, los años y los pasos.

Cada ejecución tiene un límite de ~6 h; si la cola de Copernicus no alcanza,
vuelve a lanzarlo: los scripts retoman donde quedaron. Para IDEAM y GEBCO, sube
primero los archivos a `data/ideam/raw/` y `data/bathymetry/raw/` en la rama.
