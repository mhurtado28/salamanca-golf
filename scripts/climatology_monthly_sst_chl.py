#!/usr/bin/env python3
"""Climatología mensual 2015–2025: promedio de todos los eneros, febreros, etc.

Usa los diarios ya descargados en data/daily_YYYY/ (SST y clorofila).
No vuelve a pedir productos mensuales a CDS.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import cartopy.crs as ccrs
import cartopy.feature as cfeature
import matplotlib.pyplot as plt
import numpy as np
import xarray as xr
from matplotlib.colors import LogNorm, Normalize

LAT_S, LAT_N = 10.70, 11.60
LON_W, LON_E = -75.40, -73.90
ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = Path("/opt/cursor/artifacts")

MONTH_LABELS = [
    "Ene", "Feb", "Mar", "Abr", "May", "Jun",
    "Jul", "Ago", "Sep", "Oct", "Nov", "Dic",
]


def add_basemap(ax) -> None:
    ax.set_extent([LON_W, LON_E, LAT_S, LAT_N], crs=ccrs.PlateCarree())
    ax.add_feature(cfeature.OCEAN.with_scale("10m"), facecolor="#d9e8f5", zorder=0)
    ax.add_feature(cfeature.LAND.with_scale("10m"), facecolor="#e8e4dc", zorder=2)
    ax.add_feature(cfeature.COASTLINE.with_scale("10m"), linewidth=0.7, edgecolor="#333", zorder=3)
    ax.set_xticks([])
    ax.set_yticks([])


def collect_daily(kind: str, month: int, years: list[int]) -> list[Path]:
    files: list[Path] = []
    for year in years:
        d = ROOT / "data" / f"daily_{year}"
        files.extend(sorted(d.glob(f"{kind}_{year}-{month:02d}-*.nc")))
    return files


def climatology_month(kind: str, month: int, years: list[int]) -> xr.Dataset | None:
    files = collect_daily(kind, month, years)
    if not files:
        print(f"  sin datos {kind} mes={month:02d}")
        return None
    varname = "analysed_sst" if kind == "sst" else "chlor_a"
    datasets = [xr.open_dataset(f) for f in files]
    stacked = xr.concat(datasets, dim="time")
    # media aritmética de todos los días de ese mes en todos los años
    clim = stacked.mean(dim="time", skipna=True)
    clim = clim.expand_dims(month=[month])
    clim.attrs["n_days"] = len(files)
    clim.attrs["years"] = f"{years[0]}-{years[-1]}"
    clim.attrs["method"] = "mean of all daily files for calendar month across years"
    print(f"  {kind} {MONTH_LABELS[month-1]}: {len(files)} días → clim")
    for ds in datasets:
        ds.close()
    return clim


def build_climatology(kind: str, years: list[int], out_dir: Path) -> Path | None:
    out_dir.mkdir(parents=True, exist_ok=True)
    months = []
    for m in range(1, 13):
        clim = climatology_month(kind, m, years)
        if clim is None:
            continue
        # guardar mensual individual
        clim.to_netcdf(out_dir / f"{kind}_clim_{m:02d}.nc")
        months.append(clim)
    if not months:
        return None
    stack = xr.concat(months, dim="month").sortby("month")
    out = out_dir / f"{kind}_monthly_climatology_{years[0]}_{years[-1]}.nc"
    stack.to_netcdf(out)
    print(f"Stack climatología {kind}: {out}")
    return out


def plot_climatology(stack_path: Path, kind: str, years: list[int], figs: Path) -> None:
    ds = xr.open_dataset(stack_path)
    varname = "analysed_sst" if kind == "sst" else "chlor_a"
    da = ds[varname]
    lon = ds["lon"].values if "lon" in ds.coords else ds["longitude"].values
    lat = ds["lat"].values if "lat" in ds.coords else ds["latitude"].values
    vals = da.values.astype(float)
    valid = vals[np.isfinite(vals)]
    if valid.size == 0:
        print(f"Sin datos para graficar {kind}")
        return

    y0, y1 = years[0], years[-1]
    if kind == "sst":
        cmap, units = "turbo", "°C"
        title = f"SST climatología mensual {y0}–{y1}\n(promedio de todos los días de cada mes)"
        norm = Normalize(vmin=float(np.nanpercentile(valid, 2)), vmax=float(np.nanpercentile(valid, 98)))
    else:
        cmap, units = "YlGn", "mg m$^{-3}$"
        title = f"Clorofila-a climatología mensual {y0}–{y1}\n(promedio de todos los días de cada mes)"
        pos = valid[valid > 0]
        vmin = max(float(np.nanpercentile(pos, 5)), 0.05) if pos.size else 0.05
        vmax = float(np.nanpercentile(pos, 98)) if pos.size else 1.0
        norm = LogNorm(vmin=vmin, vmax=max(vmax, vmin * 1.1))

    n = da.sizes["month"]
    ncols, nrows = 4, int(np.ceil(n / 4))
    fig, axes = plt.subplots(
        nrows,
        ncols,
        figsize=(12.8, 3.35 * nrows + 0.8),
        subplot_kw={"projection": ccrs.PlateCarree()},
    )
    axes = np.atleast_2d(axes)
    fig.subplots_adjust(left=0.04, right=0.98, top=0.88, bottom=0.12, wspace=0.10, hspace=0.28)

    mesh = None
    for i in range(nrows * ncols):
        r, c = divmod(i, ncols)
        ax = axes[r, c]
        if i >= n:
            ax.set_visible(False)
            continue
        add_basemap(ax)
        data = np.ma.masked_invalid(da.isel(month=i).values)
        if kind == "chl":
            data = np.ma.masked_where(data <= 0, data)
        mesh = ax.pcolormesh(
            lon, lat, data, transform=ccrs.PlateCarree(), cmap=cmap, norm=norm, shading="auto", zorder=1, alpha=0.92
        )
        month = int(da.month.values[i])
        n_days = int(ds.attrs.get("n_days", 0)) if False else None
        # n_days por mes desde archivo individual si existe
        ax.set_title(MONTH_LABELS[month - 1], fontsize=11)

    fig.suptitle(
        f"{title}\nBbox: {LAT_S:.2f}–{LAT_N:.2f}°N, {LON_W:.2f}–{LON_E:.2f}°W",
        fontsize=12,
        y=0.98,
    )
    if mesh is not None:
        cax = fig.add_axes([0.18, 0.04, 0.64, 0.03])
        cbar = fig.colorbar(mesh, cax=cax, orientation="horizontal")
        cbar.set_label(units, fontsize=10)

    figs.mkdir(parents=True, exist_ok=True)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    tag = f"{y0}_{y1}"
    for dest in (
        figs / f"{kind}_climatology_monthly_{tag}.png",
        ARTIFACTS / f"{kind}_climatology_monthly_{tag}.png",
    ):
        fig.savefig(dest, dpi=150)
        print(f"Mapa: {dest}")
    plt.close(fig)


def plot_annual_cycle(sst_stack: Path | None, chl_stack: Path | None, years: list[int], figs: Path) -> None:
    fig, axes = plt.subplots(2, 1, figsize=(9.5, 6.2), sharex=True)
    months = np.arange(1, 13)
    if sst_stack and sst_stack.exists():
        da = xr.open_dataset(sst_stack)["analysed_sst"]
        series = da.mean(dim=[d for d in da.dims if d != "month"], skipna=True)
        axes[0].plot(series.month.values, series.values, marker="o", color="#c0392b")
        axes[0].set_ylabel("SST (°C)")
        axes[0].set_title("Ciclo anual climatológico — SST")
        axes[0].grid(True, alpha=0.35)
    if chl_stack and chl_stack.exists():
        da = xr.open_dataset(chl_stack)["chlor_a"]
        log = np.log(da.where(da > 0))
        series = np.exp(log.mean(dim=[d for d in da.dims if d != "month"], skipna=True))
        axes[1].plot(series.month.values, series.values, marker="o", color="#1e8449")
        axes[1].set_ylabel("Clorofila-a (mg m$^{-3}$)")
        axes[1].set_title("Ciclo anual climatológico — Clorofila-a")
        axes[1].set_yscale("log")
        axes[1].grid(True, alpha=0.35)
    axes[1].set_xticks(months)
    axes[1].set_xticklabels(MONTH_LABELS)
    axes[1].set_xlabel("Mes")
    fig.suptitle(
        f"Climatología {years[0]}–{years[-1]} — "
        f"{LAT_S:.2f}–{LAT_N:.2f}N, {LON_W:.2f}–{LON_E:.2f}W",
        fontsize=11,
    )
    fig.tight_layout()
    tag = f"{years[0]}_{years[-1]}"
    figs.mkdir(parents=True, exist_ok=True)
    for dest in (
        figs / f"timeseries_climatology_sst_chl_{tag}.png",
        ARTIFACTS / f"timeseries_climatology_sst_chl_{tag}.png",
    ):
        fig.savefig(dest, dpi=150, bbox_inches="tight")
        print(f"Serie: {dest}")
    plt.close(fig)


def write_manifest(years: list[int], out_dir: Path) -> None:
    lines = [
        f"# Climatología mensual {years[0]}–{years[-1]}",
        "",
        f"Bbox: {LAT_S},{LON_W},{LAT_N},{LON_E}",
        "",
        "Cada mes = promedio de **todos los días** de ese mes en todos los años.",
        "Ej.: enero = media de todos los 1–31 ene de 2015…2025.",
        "",
        "## Entrada",
        f"- Diarios por año en `data/daily_YYYY/` (años: {years[0]}–{years[-1]})",
        "",
        "## Salida",
        f"- `{out_dir.name}/sst_clim_MM.nc`, `chl_clim_MM.nc`",
        f"- stacks `*_monthly_climatology_{years[0]}_{years[-1]}.nc`",
        "",
        "Conservar para evaluaciones futuras.",
    ]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def years_with_data(start: int, end: int) -> list[int]:
    years = []
    for y in range(start, end + 1):
        d = ROOT / "data" / f"daily_{y}"
        if d.exists() and (list(d.glob("sst_*.nc")) or list(d.glob("chl_*.nc"))):
            years.append(y)
    return years


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start", type=int, default=2015)
    p.add_argument("--end", type=int, default=2025)
    args = p.parse_args()

    years = years_with_data(args.start, args.end)
    if not years:
        raise SystemExit("No hay diarios en data/daily_YYYY/. Descarga primero.")
    print(f"Climatología con años disponibles: {years}")

    out_dir = ROOT / "data" / f"climatology_{args.start}_{args.end}"
    figs = ROOT / "figures" / f"climatology_{args.start}_{args.end}"

    sst_stack = build_climatology("sst", years, out_dir)
    chl_stack = build_climatology("chl", years, out_dir)
    write_manifest(years, out_dir)

    if sst_stack:
        plot_climatology(sst_stack, "sst", years, figs)
    if chl_stack:
        plot_climatology(chl_stack, "chl", years, figs)
    plot_annual_cycle(sst_stack, chl_stack, years, figs)

    print("\nListo.")
    for f in sorted(out_dir.glob("*")):
        print(f"  {f.name}")


if __name__ == "__main__":
    main()
