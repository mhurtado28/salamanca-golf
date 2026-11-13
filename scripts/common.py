"""Utilidades compartidas del pipeline: configuración, rutas y clientes CDS/EWDS."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
FIGS = ROOT / "figures"

CDS_URL_DEFAULT = "https://cds.climate.copernicus.eu/api"
EWDS_URL_DEFAULT = "https://ewds.climate.copernicus.eu/api"  # GloFAS vive aquí, no en CDS

MONTH_LABELS = ["Ene", "Feb", "Mar", "Abr", "May", "Jun",
                "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]


def load_config() -> dict:
    with open(ROOT / "config" / "pipeline.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _token() -> str | None:
    key = os.environ.get("CDS_KEY") or os.environ.get("CDSAPI_KEY")
    if key:
        return key.strip()
    rc = Path.home() / ".cdsapirc"
    if rc.exists():
        for line in rc.read_text().splitlines():
            if line.startswith("key:"):
                return line.split(":", 1)[1].strip()
    return None


def cds_client(service: str = "cds"):
    """Cliente cdsapi. service='cds' (ERA5, satélite) o 'ewds' (GloFAS).

    El mismo token personal de ECMWF sirve para ambos servicios. Las licencias
    de cada dataset hay que aceptarlas una vez en la web correspondiente.
    """
    import cdsapi

    key = _token()
    if not key:
        raise SystemExit(
            "Falta el token de ECMWF: define el secreto CDS_KEY en el Codespace "
            "o crea ~/.cdsapirc."
        )
    if service == "ewds":
        url = os.environ.get("EWDS_URL", EWDS_URL_DEFAULT)
    else:
        url = os.environ.get("CDS_URL") or os.environ.get("CDSAPI_URL") or CDS_URL_DEFAULT
    return cdsapi.Client(url=url, key=key, progress=False)


def cds_area(box: dict, pad: float = 0.0) -> list[float]:
    """Caja → formato 'area' de CDS: [N, W, S, E]."""
    return [box["lat_n"] + pad, box["lon_w"] - pad, box["lat_s"] - pad, box["lon_e"] + pad]


def std_latlon(ds):
    """Renombra coordenadas a lat/lon y ordena lat ascendente."""
    ren = {}
    for c in ds.coords:
        lc = c.lower()
        if lc in ("latitude", "y") and "lat" not in ds.coords:
            ren[c] = "lat"
        if lc in ("longitude", "x") and "lon" not in ds.coords:
            ren[c] = "lon"
    if ren:
        ds = ds.rename(ren)
    if "lat" in ds.coords and ds.lat.size > 1 and ds.lat[0] > ds.lat[-1]:
        ds = ds.sortby("lat")
    return ds


def time_name(ds) -> str:
    for c in ("time", "valid_time", "date"):
        if c in ds.coords or c in ds.dims:
            return c
    raise KeyError(f"No encuentro coordenada temporal en {list(ds.coords)}")
