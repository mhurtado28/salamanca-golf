#!/usr/bin/env python3
"""Descarga satélite (SST, SSS) + clorofila Ocean Color y genera mapas 2D.

Por defecto usa NOAA CoastWatch / NASA Ocean Color vía ERDDAP (sin login).

Opcional Copernicus Marine (satélite / Ocean Colour):
  export COPERNICUSMARINE_SERVICE_USERNAME=...
  export COPERNICUSMARINE_SERVICE_PASSWORD=...
  python3 scripts/download_and_map_ocean.py --source copernicus
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import cartopy.crs as ccrs
import cartopy.feature as cfeature
import matplotlib.pyplot as plt
import numpy as np
import requests
import xarray as xr
from matplotlib.colors import LogNorm

# Bbox ampliado (Caribe colombiano alrededor de Santa Marta)
LAT_S, LAT_N = 10.0, 12.5
LON_W, LON_E = -76.5, -73.0
DATE = "2023-07-20"

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
FIGS = ROOT / "figures"
ARTIFACTS = Path("/opt/cursor/artifacts")

# Productos satélite vía ERDDAP (NASA Ocean Color / GHRSST / SMOS)
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
        "source": "JPL MUR / GHRSST — ERDDAP jplMURSST41 (satélite)",
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
        "source": "SMOS MIRAS daily — ERDDAP noaacwSMOSsssDaily (satélite)",
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
        "source": "NASA Ocean Color MODIS Aqua L3 4 km — ERDDAP erdMH1chla1day_R2022NRT",
        "log": True,
    },
}

# Productos Copernicus satélite / Ocean Colour (requieren credenciales)
COPERNICUS_PRODUCTS = {
    "sst": {
        "dataset_id": "cmems_obs-sst_glo_phy_nrt_l4_P1D-m",
        "variables": ["analysed_sst"],
        "file": DATA / f"sst_cmems_{DATE}.nc",
        "var": "analysed_sst",
        "title": "SST satélite (Copernicus L4)",
        "cmap": "turbo",
        "units": "°C",
        "source": "Copernicus Marine cmems_obs-sst_glo_phy_nrt_l4_P1D-m",
        "kelvin_to_c": True,
    },
    "sss": {
        "dataset_id": "cmems_obs-mob_glo_phy-sss_nrt_multiobs_0.25deg_P1D",
        "variables": ["sos"],
        "file": DATA / f"sss_cmems_{DATE}.nc",
        "var": "sos",
        "title": "Salinidad satélite multi-obs (Copernicus)",
        "cmap": "viridis",
        "units": "PSU",
        "source": "Copernicus Marine cmems_obs-mob_glo_phy-sss_nrt_multiobs_0.25deg_P1D",
    },
    "chl": {
        "dataset_id": "cmems_obs-oc_glo_bgc-plankton_nrt_l3-multi-4km_P1D",
        "variables": ["CHL"],
        "file": DATA / f"chl_cmems_{DATE}.nc",
        "var": "CHL",
        "title": "Clorofila-a Ocean Colour (Copernicus)",
        "cmap": "YlGn",
        "units": "mg m$^{-3}$",
        "source": "Copernicus OC L3 multi-4km — cmems_obs-oc_glo_bgc-plankton_nrt_l3-multi-4km_P1D",
        "log": True,
    },
}


def download_http(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"Descargando -> {dest.name}")
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; ocean-maps-script/1.1)",
        "Accept": "*/*",
    }
    with requests.Session() as session:
        r = session.get(url, timeout=180, headers=headers, allow_redirects=False)
        if r.status_code in (301, 302, 303, 307, 308) and "Location" in r.headers:
            r = session.get(r.headers["Location"], timeout=180, headers=headers, allow_redirects=True)
        elif r.status_code >= 400:
            r = session.get(url, timeout=180, headers=headers, allow_redirects=True)
        r.raise_for_status()
        dest.write_bytes(r.content)
    print(f"  OK ({dest.stat().st_size} bytes)")


def download_erddap() -> dict:
    for cfg in ERDDAP_DATASETS.values():
        download_http(cfg["url"], cfg["file"])
    return ERDDAP_DATASETS


def _cmems_credentials() -> tuple[str | None, str | None]:
    user = (
        os.environ.get("COPERNICUSMARINE_SERVICE_USERNAME")
        or os.environ.get("COPERNICUS_USERNAME")
        or os.environ.get("CMEMS_USERNAME")
    )
    password = (
        os.environ.get("COPERNICUSMARINE_SERVICE_PASSWORD")
        or os.environ.get("COPERNICUS_PASSWORD")
        or os.environ.get("CMEMS_PASSWORD")
    )
    return user, password


def download_copernicus() -> dict:
    try:
        import copernicusmarine
    except ImportError as exc:
        raise SystemExit(
            "Falta el paquete copernicusmarine. Instala con: pip install copernicusmarine"
        ) from exc

    user, password = _cmems_credentials()
    if not user or not password:
        raise SystemExit(
            "No hay credenciales Copernicus en el entorno.\n"
            "Define COPERNICUSMARINE_SERVICE_USERNAME y COPERNICUSMARINE_SERVICE_PASSWORD."
        )

    DATA.mkdir(parents=True, exist_ok=True)
    for key, cfg in COPERNICUS_PRODUCTS.items():
        print(f"Copernicus -> {cfg['file'].name} ({cfg['dataset_id']})")
        kwargs = dict(
            dataset_id=cfg["dataset_id"],
            variables=cfg["variables"],
            minimum_longitude=LON_W,
            maximum_longitude=LON_E,
            minimum_latitude=LAT_S,
            maximum_latitude=LAT_N,
            start_datetime=f"{DATE}T00:00:00",
            end_datetime=f"{DATE}T23:59:59",
            output_filename=cfg["file"].name,
            output_directory=str(DATA),
            username=user,
            password=password,
            force_download=True,
            overwrite_output_data=True,
        )
        # Algunos productos de SSS/SST usan profundidad superficial
        if key in ("sss", "sst"):
            kwargs["minimum_depth"] = 0.0
            kwargs["maximum_depth"] = 1.0
        try:
            copernicusmarine.subset(**kwargs)
        except TypeError:
            # API antigua sin force_download/overwrite
            kwargs.pop("force_download", None)
            kwargs.pop("overwrite_output_data", None)
            copernicusmarine.subset(**kwargs)
        print(f"  OK ({cfg['file'].stat().st_size} bytes)")
    return COPERNICUS_PRODUCTS


def load_field(cfg: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    ds = xr.open_dataset(cfg["file"])
    var = cfg["var"]
    if var not in ds:
        # fallback: primera variable de datos
        var = list(ds.data_vars)[0]
        print(f"  aviso: usando variable {var}")
    da = ds[var].squeeze(drop=True)
    # Coordenadas posibles
    lon_name = next(c for c in ("longitude", "lon", "x") if c in da.coords or c in ds.coords)
    lat_name = next(c for c in ("latitude", "lat", "y") if c in da.coords or c in ds.coords)
    lon = np.asarray(ds[lon_name].values if lon_name in ds.coords else da[lon_name].values)
    lat = np.asarray(ds[lat_name].values if lat_name in ds.coords else da[lat_name].values)
    data = np.asarray(da.values, dtype=float)
    if cfg.get("kelvin_to_c") and np.nanmedian(data) > 100:
        data = data - 273.15
    return lon, lat, data


def add_basemap(ax) -> None:
    ax.set_extent([LON_W, LON_E, LAT_S, LAT_N], crs=ccrs.PlateCarree())
    ax.add_feature(cfeature.OCEAN.with_scale("10m"), facecolor="#d9e8f5", zorder=0)
    ax.add_feature(cfeature.LAND.with_scale("10m"), facecolor="#e8e4dc", zorder=2)
    ax.add_feature(cfeature.COASTLINE.with_scale("10m"), linewidth=0.8, edgecolor="#333333", zorder=3)
    ax.add_feature(
        cfeature.BORDERS.with_scale("10m"), linewidth=0.5, edgecolor="#666666", linestyle="--", zorder=3
    )
    ax.set_xticks(np.linspace(LON_W, LON_E, 6), crs=ccrs.PlateCarree())
    ax.set_yticks(np.linspace(LAT_S, LAT_N, 6), crs=ccrs.PlateCarree())
    ax.tick_params(labelsize=8)
    ax.set_xlabel("Longitud", fontsize=9)
    ax.set_ylabel("Latitud", fontsize=9)
    ax.grid(True, linewidth=0.4, color="gray", alpha=0.45, linestyle=":")


def _mesh(ax, lon, lat, data, cfg):
    plot_data = np.ma.masked_invalid(data)
    if cfg.get("log"):
        plot_data = np.ma.masked_where(plot_data <= 0, plot_data)
        vals = plot_data.compressed()
        if vals.size == 0:
            raise RuntimeError(f"Sin datos válidos para {cfg['title']}")
        return ax.pcolormesh(
            lon,
            lat,
            plot_data,
            transform=ccrs.PlateCarree(),
            cmap=cfg["cmap"],
            shading="auto",
            norm=LogNorm(vmin=max(float(vals.min()), 1e-2), vmax=float(vals.max())),
            zorder=1,
            alpha=0.92,
        )
    return ax.pcolormesh(
        lon,
        lat,
        plot_data,
        transform=ccrs.PlateCarree(),
        cmap=cfg["cmap"],
        shading="auto",
        zorder=1,
        alpha=0.92,
    )


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
        f"Satélite / Ocean Color — {DATE}\n"
        f"Bbox ampliado: {LAT_S:.1f}–{LAT_N:.1f}°N, {LON_W:.1f}–{LON_E:.1f}°W",
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
    parser.add_argument(
        "--source",
        choices=("erddap", "copernicus"),
        default="erddap",
        help="erddap = NOAA/NASA satélite+Ocean Color (default); copernicus = CMEMS (requiere login)",
    )
    args = parser.parse_args()

    DATA.mkdir(parents=True, exist_ok=True)
    FIGS.mkdir(parents=True, exist_ok=True)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)

    if args.source == "copernicus":
        datasets = download_copernicus()
        tag = "cmems"
    else:
        datasets = download_erddap()
        tag = "sat"

    for key, cfg in datasets.items():
        plot_single(cfg, FIGS / f"{key}_{DATE}_{tag}.png")
        plot_single(cfg, ARTIFACTS / f"{key}_{DATE}_{tag}.png")

    plot_combined(datasets, FIGS / f"ocean_vars_{DATE}_{tag}.png")
    plot_combined(datasets, ARTIFACTS / f"ocean_vars_{DATE}_{tag}.png")

    # Alias estables usados en el README / PR
    plot_combined(datasets, FIGS / f"ocean_vars_{DATE}.png")
    plot_combined(datasets, ARTIFACTS / f"ocean_vars_{DATE}.png")
    for key, cfg in datasets.items():
        plot_single(cfg, FIGS / f"{key}_{DATE}.png")
        plot_single(cfg, ARTIFACTS / f"{key}_{DATE}.png")

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
