#!/usr/bin/env bash
# Corre todo el pipeline (ríos, viento, lluvia, batimetría) en el Codespace.
# Uso: bash scripts/run_pipeline.sh [AÑO_INICIO] [AÑO_FIN]
set -uo pipefail
cd "$(dirname "$0")"
START="${1:-2015}"
END="${2:-2025}"

run() { echo; echo "=== $* ==="; python "$@" || echo "!!! falló: $* (sigo con lo demás)"; }

run rivers_glofas.py --start "$START" --end "$END"
run wind_era5.py --start "$START" --end "$END"
run rain_chirps_download.py --start "$START" --end "$END"

if compgen -G "../data/ideam/raw/*.[cC][sS][vV]" > /dev/null; then
  run ideam_dhime.py
  run rivers_glofas.py --process-only   # rehace la validación de Calamar con IDEAM
else
  echo; echo "(sin CSV de DHIME en data/ideam/raw/: se omite IDEAM)"
fi
run rain_analysis.py --start "$START" --end "$END"

if compgen -G "../data/bathymetry/raw/*.nc" > /dev/null || compgen -G "../data/bathymetry/raw/*/*.nc" > /dev/null; then
  run bathymetry_gebco.py
else
  echo; echo "(sin GEBCO en data/bathymetry/raw/: se omite batimetría)"
fi
echo; echo "Fin. Revisa figures/ y data/."
