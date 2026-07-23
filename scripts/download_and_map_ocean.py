#!/usr/bin/env python3
"""Descarga SST / salinidad / clorofila desde Copernicus CDS (cdsapi) y genera mapas.

Autenticación (nunca en el repo):
  - Variables de entorno CDS_URL + CDS_KEY (p. ej. GitHub Secrets), o
  - Archivo local ~/.cdsapirc con:
        url: https://cds.climate.copernicus.eu/api
        key: <token>

Uso:
  python3 scripts/download_and_map_ocean.py --source cds
  python3 scripts/download_and_map_ocean.py --source erddap   # fallback público
"""

from __future__ import annotations

import argparse
import os
import zipfile
from pathlib import Path

import cartopy.crs as ccrs
import cartopy.feature as cfeature
import cdsapi
import matplotlib.pyplot as plt
import numpy as np
import requests
import xarray as xr
from matplotlib.colors import LogNorm

# Bbox intermedio alrededor de Santa Marta
LAT_S, LAT_N = 10.70, 11.60
LON_W, LON_E = -75.40, -73.90
DATE = "2023-07-20"
YEAR, MONTH, DAY = DATE.split("-")

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
FIGS = ROOT / "figures"
ARTIFACTS = Path("/opt/cursor/artifacts")
TMP = Path("/tmp/cds_download")


def ensure_cdsapirc_from_env() -> None:
    """Si hay CDS_URL/CDS_KEY en el entorno y no existe ~/.cdsapirc, lo crea."""
    cdsapirc = Path.home() / ".cdsapirc"
    if cdsapirc.exists():
        return
    url = os.environ.get("CDS_URL") or os.environ.get("CDSAPI_URL")
    key = os.environ.get("CDS_KEY") or os.environ.get("CDSAPI_KEY")
    if not url or not key:
        return
    cdsapirc.write_text(f"url: {url}\nkey: {key}\n", encoding="utf-8")
    cdsapirc.chmod(0o600)
    print("Creado ~/.cdsapirc desde variables de entorno (valores no impresos).")


def cds_client() -> cdsapi.Client:
    ensure_cdsapirc_from_env()
    cdsapirc = Path.home() / ".cdsapirc"
    if not cdsapirc.exists() and not (
        (os.environ.get("CDS_URL") or os.environ.get("CDSAPI_URL"))
        and (os.environ.get("CDS_KEY") or os.environ.get("CDSAPI_KEY"))
    ):
        raise SystemExit(
            "Falta autenticación CDS. Define GitHub Secrets CDS_URL y CDS_KEY "
            "o crea ~/.cdsapirc (url + key). No subas el token al repositorio."
        )
    url = os.environ.get("CDS_URL") or os.environ.get("CDSAPI_URL")
    key = os.environ.get("CDS_KEY") or os.environ.get("CDSAPI_KEY")
    if url and key:
        return cdsapi.Client(url=url, key=key, progress=True)
    return cdsapi.Client(progress=True)


def accept_cds_licences(client: cdsapi.Client, licence_ids: list[tuple[str, int]]) -> None:
    """Acepta licencias requeridas vía Profile API si aún no están aceptadas."""
    # Reutiliza la URL/token del cliente sin imprimirlos
    url = getattr(client, "url", None) or os.environ.get("CDS_URL") or "https://cds.climate.copernicus.eu/api"
    key = getattr(client, "key", None) or os.environ.get("CDS_KEY")
    if not key and (Path.home() / ".cdsapirc").exists():
        for line in (Path.home() / ".cdsapirc").read_text().splitlines():
            if line.startswith("key:"):
                key = line.split(":", 1)[1].strip()
            if line.startswith("url:"):
                url = line.split(":", 1)[1].strip()
    if not key:
        return
    headers = {
        "PRIVATE-TOKEN": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    base = f"{url.rstrip('/')}/profiles/v1"
    try:
        current = requests.get(f"{base}/account/licences", headers=headers, timeout=60)
        current.raise_for_status()
        have = {x["id"] for x in current.json().get("licences", [])}
    except Exception as exc:
        print(f"No se pudieron listar licencias CDS ({exc}); continúa.")
        return
    for lic_id, rev in licence_ids:
        if lic_id in have:
            continue
        r = requests.put(
            f"{base}/account/licences/{lic_id}",
            headers=headers,
            json={"revision": rev},
            timeout=60,
        )
        if r.status_code in (200, 201):
            print(f"Licencia CDS aceptada: {lic_id}")
        else:
            print(
                f"No se pudo aceptar licencia {lic_id} ({r.status_code}). "
                f"Acéptala en la web del dataset si el retrieve falla."
            )


def unzip_first_nc(zip_path: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        members = [m for m in zf.namelist() if m.endswith(".nc")]
        if not members:
            raise RuntimeError(f"No hay NetCDF en {zip_path}")
        zf.extract(members[0], dest_dir)
        return dest_dir / members[0]


def subset_latlon(ds: xr.Dataset, lat_name: str, lon_name: str) -> xr.Dataset:
    lat = ds[lat_name]
    lon = ds[lon_name]
    if lat.ndim == 1 and lon.ndim == 1:
        lat_min, lat_max = float(lat.min()), float(lat.max())
        # lat puede ir descendente (ocean colour)
        if lat_min < lat_max and float(lat[0]) > float(lat[-1]):
            return ds.sel({lat_name: slice(LAT_N, LAT_S), lon_name: slice(LON_W, LON_E)})
        return ds.sel({lat_name: slice(LAT_S, LAT_N), lon_name: slice(LON_W, LON_E)})
    # malla curvilínea (ORAS5)
    mask = (lat >= LAT_S) & (lat <= LAT_N) & (lon >= LON_W) & (lon <= LON_E)
    # recorte por índices envolventes
    ys, xs = np.where(mask.values)
    if ys.size == 0:
        raise RuntimeError("Bbox vacío en rejilla curvilínea")
    return ds.isel(y=slice(ys.min(), ys.max() + 1), x=slice(xs.min(), xs.max() + 1))


def _retrieve_or_reuse(client: cdsapi.Client, dataset: str, request: dict, zip_path: Path) -> Path:
    """Reutiliza zip local si ya existe (útil al solo cambiar el bbox)."""
    if zip_path.exists() and zip_path.stat().st_size > 1000:
        print(f"Reutilizando cache local: {zip_path.name}")
        return zip_path
    print(f"CDS -> {dataset}")
    client.retrieve(dataset, request, str(zip_path))
    return zip_path


def download_cds() -> dict:
    DATA.mkdir(parents=True, exist_ok=True)
    TMP.mkdir(parents=True, exist_ok=True)
    client = cds_client()
    accept_cds_licences(
        client,
        [
            ("sst-cci", 2),
            ("satellite-ocean-colour", 1),
            ("cc-by", 1),
        ],
    )

    # 1) SST satélite L4 (global diario → subset local)
    sst_zip = _retrieve_or_reuse(
        client,
        "satellite-sea-surface-temperature",
        {
            "variable": "all",
            "processinglevel": "level_4",
            "sensor_on_satellite": "combined_product",
            "version": "3_0",
            "temporal_resolution": "daily",
            "year": [YEAR],
            "month": [MONTH],
            "day": [DAY],
        },
        TMP / "sst_cds.zip",
    )
    sst_nc = unzip_first_nc(sst_zip, TMP / "sst")
    sst_ds = subset_latlon(xr.open_dataset(sst_nc), "lat", "lon")
    # Kelvin → °C si aplica
    if "analysed_sst" in sst_ds and float(sst_ds["analysed_sst"].median()) > 100:
        sst_ds["analysed_sst"] = sst_ds["analysed_sst"] - 273.15
        sst_ds["analysed_sst"].attrs["units"] = "degree_C"
    sst_out = DATA / f"sst_cds_{DATE}.nc"
    sst_ds[["analysed_sst"]].to_netcdf(sst_out)

    # 2) Clorofila Ocean Colour satélite
    chl_zip = _retrieve_or_reuse(
        client,
        "satellite-ocean-colour",
        {
            "variable": ["mass_concentration_of_chlorophyll_a"],
            "projection": "regular_latitude_longitude_grid",
            "temporal_resolution": "daily",
            "year": [YEAR],
            "month": [MONTH],
            "day": [DAY],
            "version": "6_0",
        },
        TMP / "chl_cds.zip",
    )
    chl_nc = unzip_first_nc(chl_zip, TMP / "chl")
    chl_ds = subset_latlon(xr.open_dataset(chl_nc), "lat", "lon")
    chl_out = DATA / f"chl_cds_{DATE}.nc"
    chl_ds[["chlor_a"]].to_netcdf(chl_out)

    # 3) Salinidad: en CDS no hay SSS satélite diaria; ORAS5 mensual (operacional)
    sss_zip = _retrieve_or_reuse(
        client,
        "reanalysis-oras5",
        {
            "product_type": ["operational"],
            "vertical_resolution": "single_level",
            "variable": ["sea_surface_salinity"],
            "year": [YEAR],
            "month": [MONTH],
        },
        TMP / "sss_cds.zip",
    )
    sss_nc = unzip_first_nc(sss_zip, TMP / "sss")
    sss_ds = subset_latlon(xr.open_dataset(sss_nc), "nav_lat", "nav_lon")
    sss_out = DATA / f"sss_cds_{DATE}.nc"
    sss_ds[["sosaline"]].to_netcdf(sss_out)

    return {
        "sst": {
            "file": sst_out,
            "var": "analysed_sst",
            "title": "SST satélite CDS (ESA CCI L4)",
            "cmap": "turbo",
            "units": "°C",
            "source": "CDS satellite-sea-surface-temperature (L4 combined, v3.0)",
            "grid": "regular",
        },
        "sss": {
            "file": sss_out,
            "var": "sosaline",
            "title": "Salinidad CDS (ORAS5 mensual)",
            "cmap": "viridis",
            "units": "PSU",
            "source": "CDS reanalysis-oras5 sea_surface_salinity (operacional, mensual)",
            "grid": "curvilinear",
            "lon": "nav_lon",
            "lat": "nav_lat",
        },
        "chl": {
            "file": chl_out,
            "var": "chlor_a",
            "title": "Clorofila-a Ocean Colour CDS",
            "cmap": "YlGn",
            "units": "mg m$^{-3}$",
            "source": "CDS satellite-ocean-colour (chlor_a, v6.0, 4 km)",
            "grid": "regular",
            "log": True,
        },
    }


# -------- fallback ERDDAP (público) --------
ERDDAP_DATASETS = {
    "sst": {
        "url": (
            "https://coastwatch.pfeg.noaa.gov/erddap/griddap/jplMURSST41.nc"
            f"?analysed_sst[({DATE}T09:00:00Z):1:({DATE}T09:00:00Z)]"
            f"[({LAT_S}):1:({LAT_N})][({LON_W}):1:({LON_E})]"
        ),
        "file": DATA / f"sst_{DATE}.nc",
        "var": "analysed_sst",
        "title": "SST satélite (MUR L4)",
        "cmap": "turbo",
        "units": "°C",
        "source": "JPL MUR / ERDDAP jplMURSST41",
        "grid": "regular",
    },
    "sss": {
        "url": (
            "https://coastwatch.noaa.gov/erddap/griddap/noaacwSMOSsssDaily.nc"
            f"?sss[({DATE}T12:00:00Z):1:({DATE}T12:00:00Z)][(0.0):1:(0.0)]"
            f"[({LAT_S}):1:({LAT_N})][({LON_W}):1:({LON_E})]"
        ),
        "file": DATA / f"sss_{DATE}.nc",
        "var": "sss",
        "title": "Salinidad satélite (SMOS)",
        "cmap": "viridis",
        "units": "PSU",
        "source": "SMOS MIRAS daily ERDDAP",
        "grid": "regular",
    },
    "chl": {
        "url": (
            "https://coastwatch.pfeg.noaa.gov/erddap/griddap/erdMH1chla1day_R2022NRT.nc"
            f"?chlorophyll[({DATE}T12:00:00Z):1:({DATE}T12:00:00Z)]"
            f"[({LAT_S}):1:({LAT_N})][({LON_W}):1:({LON_E})]"
        ),
        "file": DATA / f"chl_{DATE}.nc",
        "var": "chlorophyll",
        "title": "Clorofila-a Ocean Color (MODIS)",
        "cmap": "YlGn",
        "units": "mg m$^{-3}$",
        "source": "NASA Ocean Color MODIS Aqua ERDDAP",
        "grid": "regular",
        "log": True,
    },
}


def download_http(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": "Mozilla/5.0 (compatible; ocean-maps-script/2.0)", "Accept": "*/*"}
    with requests.Session() as session:
        r = session.get(url, timeout=180, headers=headers, allow_redirects=False)
        if r.status_code in (301, 302, 303, 307, 308) and "Location" in r.headers:
            r = session.get(r.headers["Location"], timeout=180, headers=headers, allow_redirects=True)
        elif r.status_code >= 400:
            r = session.get(url, timeout=180, headers=headers, allow_redirects=True)
        r.raise_for_status()
        dest.write_bytes(r.content)
    print(f"Descargado {dest.name} ({dest.stat().st_size} bytes)")


def download_erddap() -> dict:
    for cfg in ERDDAP_DATASETS.values():
        download_http(cfg["url"], cfg["file"])
    return ERDDAP_DATASETS


def load_field(cfg: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    ds = xr.open_dataset(cfg["file"])
    da = ds[cfg["var"]]
    # Quitar solo ejes temporales de tamaño 1; conservar y/x aunque sean 1 (ORAS5 en bbox chico)
    for dim in list(da.dims):
        if "time" in dim and da.sizes[dim] == 1:
            da = da.squeeze(dim, drop=True)
    if cfg.get("grid") == "curvilinear":
        lon = np.asarray(ds[cfg.get("lon", "nav_lon")].values)
        lat = np.asarray(ds[cfg.get("lat", "nav_lat")].values)
        data = np.asarray(da.values, dtype=float)
        if data.ndim == 1:
            data = data.reshape(lat.shape)
        return lon, lat, data
    da = da.squeeze(drop=True)
    lon_name = next(c for c in ("longitude", "lon", "x") if c in da.coords or c in ds.coords)
    lat_name = next(c for c in ("latitude", "lat", "y") if c in da.coords or c in ds.coords)
    lon = np.asarray(ds[lon_name].values if lon_name in ds.coords else da[lon_name].values)
    lat = np.asarray(ds[lat_name].values if lat_name in ds.coords else da[lat_name].values)
    data = np.asarray(da.values, dtype=float)
    return lon, lat, data


def add_basemap(ax) -> None:
    ax.set_extent([LON_W, LON_E, LAT_S, LAT_N], crs=ccrs.PlateCarree())
    ax.add_feature(cfeature.OCEAN.with_scale("10m"), facecolor="#d9e8f5", zorder=0)
    ax.add_feature(cfeature.LAND.with_scale("10m"), facecolor="#e8e4dc", zorder=2)
    ax.add_feature(cfeature.COASTLINE.with_scale("10m"), linewidth=0.8, edgecolor="#333333", zorder=3)
    ax.add_feature(
        cfeature.BORDERS.with_scale("10m"), linewidth=0.5, edgecolor="#666666", linestyle="--", zorder=3
    )
    ax.set_xticks(np.linspace(LON_W, LON_E, 5), crs=ccrs.PlateCarree())
    ax.set_yticks(np.linspace(LAT_S, LAT_N, 5), crs=ccrs.PlateCarree())
    ax.tick_params(labelsize=8)
    ax.set_xlabel("Longitud", fontsize=9)
    ax.set_ylabel("Latitud", fontsize=9)
    ax.grid(True, linewidth=0.4, color="gray", alpha=0.45, linestyle=":")


def _mesh(ax, lon, lat, data, cfg):
    plot_data = np.ma.masked_invalid(data)
    kwargs = dict(
        transform=ccrs.PlateCarree(),
        cmap=cfg["cmap"],
        shading="auto",
        zorder=1,
        alpha=0.92,
    )
    if cfg.get("log"):
        plot_data = np.ma.masked_where(plot_data <= 0, plot_data)
        vals = plot_data.compressed()
        if vals.size == 0:
            raise RuntimeError(f"Sin datos válidos para {cfg['title']}")
        kwargs["norm"] = LogNorm(vmin=max(float(vals.min()), 1e-2), vmax=float(vals.max()))
    return ax.pcolormesh(lon, lat, plot_data, **kwargs)


def plot_single(cfg: dict, out: Path) -> None:
    lon, lat, data = load_field(cfg)
    fig = plt.figure(figsize=(9.0, 6.8))
    ax = fig.add_subplot(1, 1, 1, projection=ccrs.PlateCarree())
    add_basemap(ax)
    mesh = _mesh(ax, lon, lat, data, cfg)
    cbar = fig.colorbar(mesh, ax=ax, shrink=0.82, pad=0.03)
    cbar.set_label(cfg["units"])
    ax.set_title(
        f"{cfg['title']}\n{DATE}  |  {LAT_S:.1f}–{LAT_N:.1f}N, {LON_W:.1f}–{LON_E:.1f}W",
        fontsize=12,
    )
    fig.text(0.01, 0.01, cfg["source"], fontsize=7, color="#444444")
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Mapa: {out}")


def plot_combined(datasets: dict, out: Path) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(15.5, 5.4), subplot_kw={"projection": ccrs.PlateCarree()})
    for ax, cfg in zip(axes, datasets.values()):
        lon, lat, data = load_field(cfg)
        add_basemap(ax)
        mesh = _mesh(ax, lon, lat, data, cfg)
        cbar = fig.colorbar(mesh, ax=ax, shrink=0.75, pad=0.04)
        cbar.set_label(cfg["units"], fontsize=8)
        ax.set_title(cfg["title"], fontsize=10)
    fig.suptitle(
        f"Copernicus CDS — {DATE}\n"
        f"Bbox: {LAT_S:.1f}–{LAT_N:.1f}°N, {LON_W:.1f}–{LON_E:.1f}°W",
        fontsize=12,
        y=1.02,
    )
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Mapa combinado: {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("cds", "erddap"), default="cds")
    args = parser.parse_args()

    DATA.mkdir(parents=True, exist_ok=True)
    FIGS.mkdir(parents=True, exist_ok=True)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)

    datasets = download_cds() if args.source == "cds" else download_erddap()
    tag = args.source

    for key, cfg in datasets.items():
        plot_single(cfg, FIGS / f"{key}_{DATE}_{tag}.png")
        plot_single(cfg, ARTIFACTS / f"{key}_{DATE}_{tag}.png")
        # alias estables
        plot_single(cfg, FIGS / f"{key}_{DATE}.png")
        plot_single(cfg, ARTIFACTS / f"{key}_{DATE}.png")

    plot_combined(datasets, FIGS / f"ocean_vars_{DATE}_{tag}.png")
    plot_combined(datasets, ARTIFACTS / f"ocean_vars_{DATE}_{tag}.png")
    plot_combined(datasets, FIGS / f"ocean_vars_{DATE}.png")
    plot_combined(datasets, ARTIFACTS / f"ocean_vars_{DATE}.png")

    print("\nResumen:")
    for key, cfg in datasets.items():
        lon, lat, data = load_field(cfg)
        valid = np.isfinite(data)
        print(
            f"  {key}: shape={data.shape}, valid={int(valid.sum())}, "
            f"min={np.nanmin(data):.3f}, max={np.nanmax(data):.3f}"
        )


if __name__ == "__main__":
    main()
