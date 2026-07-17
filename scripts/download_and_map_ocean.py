#!/usr/bin/env python3
"""Descarga 1 día de SST, salinidad y clorofila (bbox Santa Marta) y genera mapas 2D."""

from __future__ import annotations

from pathlib import Path

import cartopy.crs as ccrs
import cartopy.feature as cfeature
import matplotlib.pyplot as plt
import numpy as np
import requests
import xarray as xr
from matplotlib.colors import LogNorm

# Bbox solicitado
LAT_S, LAT_N = 11.00, 11.32
LON_W, LON_E = -74.83, -74.20
# Día con cobertura conjunta (clorofila óptica + SMOS + MUR)
DATE = "2023-07-20"

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
FIGS = ROOT / "figures"
ARTIFACTS = Path("/opt/cursor/artifacts")

DATASETS = {
    "sst": {
        "url": (
            "https://coastwatch.pfeg.noaa.gov/erddap/griddap/jplMURSST41.nc"
            f"?analysed_sst[({DATE}T09:00:00Z):1:({DATE}T09:00:00Z)]"
            f"[({LAT_S}):1:({LAT_N})][({LON_W}):1:({LON_E})]"
        ),
        "file": DATA / f"sst_{DATE}.nc",
        "var": "analysed_sst",
        "title": "Temperatura superficial del mar (SST)",
        "cmap": "turbo",
        "units": "°C",
        "source": "JPL MUR SST (ERDDAP jplMURSST41)",
    },
    "sss": {
        # SMOS ~0.25°: se descarga un poco más amplio para capturar píxeles cercanos
        "url": (
            "https://coastwatch.noaa.gov/erddap/griddap/noaacwSMOSsssDaily.nc"
            f"?sss[({DATE}T12:00:00Z):1:({DATE}T12:00:00Z)][(0.0):1:(0.0)]"
            f"[(10.8):1:(11.5)][(-75.1):1:(-74.0)]"
        ),
        "file": DATA / f"sss_{DATE}.nc",
        "var": "sss",
        "title": "Salinidad superficial (SSS)",
        "cmap": "viridis",
        "units": "PSU",
        "source": "SMOS MIRAS daily (ERDDAP noaacwSMOSsssDaily)",
    },
    "chl": {
        "url": (
            "https://coastwatch.pfeg.noaa.gov/erddap/griddap/erdMH1chla1day_R2022NRT.nc"
            f"?chlorophyll[({DATE}T12:00:00Z):1:({DATE}T12:00:00Z)]"
            f"[({LAT_S}):1:({LAT_N})][({LON_W}):1:({LON_E})]"
        ),
        "file": DATA / f"chl_{DATE}.nc",
        "var": "chlorophyll",
        "title": "Clorofila-a",
        "cmap": "YlGn",
        "units": "mg m$^{-3}$",
        "source": "MODIS Aqua L3 SMI 4 km (ERDDAP erdMH1chla1day_R2022NRT)",
        "log": True,
    },
}


def download(url: str, dest: Path) -> None:
    """Descarga NetCDF. Maneja redirects 302 que a veces pierden cabeceras."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"Descargando -> {dest.name}")
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; ocean-maps-script/1.0)",
        "Accept": "*/*",
    }
    with requests.Session() as session:
        r = session.get(url, timeout=120, headers=headers, allow_redirects=False)
        if r.status_code in (301, 302, 303, 307, 308) and "Location" in r.headers:
            r = session.get(r.headers["Location"], timeout=120, headers=headers, allow_redirects=True)
        elif r.status_code >= 400:
            # reintento directo sin seguir redirect automático problemático
            r = session.get(url, timeout=120, headers=headers, allow_redirects=True)
        r.raise_for_status()
        dest.write_bytes(r.content)
    print(f"  OK ({dest.stat().st_size} bytes)")


def load_field(cfg: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    ds = xr.open_dataset(cfg["file"])
    da = ds[cfg["var"]].squeeze(drop=True)
    # Asegurar lon/lat 1D
    if "longitude" in da.coords:
        lon = da["longitude"].values
        lat = da["latitude"].values
    else:
        lon = da["lon"].values
        lat = da["lat"].values
    data = np.asarray(da.values, dtype=float)
    return lon, lat, data


def add_basemap(ax) -> None:
    ax.set_extent([LON_W, LON_E, LAT_S, LAT_N], crs=ccrs.PlateCarree())
    ax.add_feature(cfeature.OCEAN.with_scale("10m"), facecolor="#d9e8f5", zorder=0)
    ax.add_feature(cfeature.LAND.with_scale("10m"), facecolor="#e8e4dc", zorder=2)
    ax.add_feature(cfeature.COASTLINE.with_scale("10m"), linewidth=0.9, edgecolor="#333333", zorder=3)
    ax.add_feature(
        cfeature.BORDERS.with_scale("10m"), linewidth=0.5, edgecolor="#666666", linestyle="--", zorder=3
    )
    # Etiquetas manuales (evita bug cartopy/shapely con gridliner en extents pequeños)
    ax.set_xticks(np.linspace(LON_W, LON_E, 5), crs=ccrs.PlateCarree())
    ax.set_yticks(np.linspace(LAT_S, LAT_N, 5), crs=ccrs.PlateCarree())
    ax.tick_params(labelsize=8)
    ax.set_xlabel("Longitud (°W)", fontsize=9)
    ax.set_ylabel("Latitud (°N)", fontsize=9)
    ax.grid(True, linewidth=0.4, color="gray", alpha=0.45, linestyle=":")


def plot_single(key: str, cfg: dict, out: Path) -> None:
    lon, lat, data = load_field(cfg)
    fig = plt.figure(figsize=(8.2, 6.2))
    ax = fig.add_subplot(1, 1, 1, projection=ccrs.PlateCarree())
    add_basemap(ax)

    plot_data = np.ma.masked_invalid(data)
    if cfg.get("log"):
        plot_data = np.ma.masked_where(plot_data <= 0, plot_data)
        mesh = ax.pcolormesh(
            lon,
            lat,
            plot_data,
            transform=ccrs.PlateCarree(),
            cmap=cfg["cmap"],
            shading="auto",
            norm=LogNorm(
                vmin=max(float(np.nanmin(plot_data.compressed())), 1e-2),
                vmax=float(np.nanmax(plot_data.compressed())),
            ),
            zorder=1,
            alpha=0.92,
        )
    else:
        mesh = ax.pcolormesh(
            lon,
            lat,
            plot_data,
            transform=ccrs.PlateCarree(),
            cmap=cfg["cmap"],
            shading="auto",
            zorder=1,
            alpha=0.92,
        )

    cbar = fig.colorbar(mesh, ax=ax, shrink=0.82, pad=0.03)
    cbar.set_label(cfg["units"])
    ax.set_title(f"{cfg['title']}\n{DATE}  |  {LAT_S:.2f}–{LAT_N:.2f}N, {LON_W:.2f}–{LON_E:.2f}W", fontsize=12)
    fig.text(0.01, 0.01, cfg["source"], fontsize=7, color="#444444")
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=160, bbox_inches="tight")
    plt.close(fig)
    print(f"Mapa: {out}")


def plot_combined(out: Path) -> None:
    fig, axes = plt.subplots(
        1,
        3,
        figsize=(14.5, 5.2),
        subplot_kw={"projection": ccrs.PlateCarree()},
    )
    for ax, (key, cfg) in zip(axes, DATASETS.items()):
        lon, lat, data = load_field(cfg)
        add_basemap(ax)
        plot_data = np.ma.masked_invalid(data)
        if cfg.get("log"):
            plot_data = np.ma.masked_where(plot_data <= 0, plot_data)
            mesh = ax.pcolormesh(
                lon,
                lat,
                plot_data,
                transform=ccrs.PlateCarree(),
                cmap=cfg["cmap"],
                shading="auto",
                norm=LogNorm(
                    vmin=max(float(np.nanmin(plot_data.compressed())), 1e-2),
                    vmax=float(np.nanmax(plot_data.compressed())),
                ),
                zorder=1,
                alpha=0.92,
            )
        else:
            mesh = ax.pcolormesh(
                lon,
                lat,
                plot_data,
                transform=ccrs.PlateCarree(),
                cmap=cfg["cmap"],
                shading="auto",
                zorder=1,
                alpha=0.92,
            )
        cbar = fig.colorbar(mesh, ax=ax, shrink=0.75, pad=0.04)
        cbar.set_label(cfg["units"], fontsize=8)
        ax.set_title(cfg["title"], fontsize=10)

    fig.suptitle(
        f"Variables oceánicas 2D — {DATE}\n"
        f"Bbox: {LAT_S:.2f}–{LAT_N:.2f}°N, {LON_W:.2f}–{LON_E:.2f}°W",
        fontsize=12,
        y=1.02,
    )
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=160, bbox_inches="tight")
    plt.close(fig)
    print(f"Mapa combinado: {out}")


def main() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    FIGS.mkdir(parents=True, exist_ok=True)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)

    for cfg in DATASETS.values():
        download(cfg["url"], cfg["file"])

    for key, cfg in DATASETS.items():
        plot_single(key, cfg, FIGS / f"{key}_{DATE}.png")
        plot_single(key, cfg, ARTIFACTS / f"{key}_{DATE}.png")

    plot_combined(FIGS / f"ocean_vars_{DATE}.png")
    plot_combined(ARTIFACTS / f"ocean_vars_{DATE}.png")

    # Resumen numérico
    print("\nResumen:")
    for key, cfg in DATASETS.items():
        lon, lat, data = load_field(cfg)
        valid = np.isfinite(data)
        print(
            f"  {key}: shape={data.shape}, valid={int(valid.sum())}, "
            f"min={np.nanmin(data):.3f}, max={np.nanmax(data):.3f} {cfg['units']}"
        )


if __name__ == "__main__":
    main()
