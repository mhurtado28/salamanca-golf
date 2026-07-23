#!/usr/bin/env python3
"""Descarga TODOS los días de un año (SST + clorofila) desde CDS, guarda recortes
y calcula promedios mensuales a partir de los diarios.

No usa productos mensuales de CDS: el promedio se calcula aquí.

Autenticación: CDS_URL/CDS_KEY o ~/.cdsapirc (nunca en el repo).

Ejemplos:
  python3 scripts/daily_to_monthly_sst_chl.py --year 2024
  python3 scripts/daily_to_monthly_sst_chl.py --year 2025 --plot-only
"""

from __future__ import annotations

import argparse
import calendar
import os
import re
import shutil
import time
import zipfile
from pathlib import Path

import cartopy.crs as ccrs
import cartopy.feature as cfeature
import cdsapi
import matplotlib.pyplot as plt
import numpy as np
import requests
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

# Se rellenan en configure_year()
YEAR = 2025
DAILY = ROOT / "data" / "daily_2025"
MONTHLY = ROOT / "data" / "monthly_from_daily_2025"
FIGS = ROOT / "figures" / "monthly_2025"
TMP = Path("/tmp/cds_daily_2025")


def configure_year(year: int) -> None:
    global YEAR, DAILY, MONTHLY, FIGS, TMP
    YEAR = year
    DAILY = ROOT / "data" / f"daily_{year}"
    MONTHLY = ROOT / "data" / f"monthly_from_daily_{year}"
    FIGS = ROOT / "figures" / f"monthly_{year}"
    TMP = Path(f"/tmp/cds_daily_{year}")


def ensure_cdsapirc_from_env() -> None:
    cdsapirc = Path.home() / ".cdsapirc"
    if cdsapirc.exists():
        return
    url = os.environ.get("CDS_URL") or os.environ.get("CDSAPI_URL")
    key = os.environ.get("CDS_KEY") or os.environ.get("CDSAPI_KEY")
    if url and key:
        cdsapirc.write_text(f"url: {url}\nkey: {key}\n", encoding="utf-8")
        cdsapirc.chmod(0o600)


def cds_client() -> cdsapi.Client:
    ensure_cdsapirc_from_env()
    url = os.environ.get("CDS_URL") or os.environ.get("CDSAPI_URL")
    key = os.environ.get("CDS_KEY") or os.environ.get("CDSAPI_KEY")
    if url and key:
        return cdsapi.Client(url=url, key=key, progress=True)
    if not (Path.home() / ".cdsapirc").exists():
        raise SystemExit("Falta autenticación CDS (CDS_URL/CDS_KEY o ~/.cdsapirc).")
    return cdsapi.Client(progress=True)


def accept_licences(client: cdsapi.Client) -> None:
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
        have = {
            x["id"]
            for x in requests.get(f"{base}/account/licences", headers=headers, timeout=60)
            .json()
            .get("licences", [])
        }
    except Exception:
        return
    for lic_id, rev in [("sst-cci", 2), ("satellite-ocean-colour", 1), ("cc-by", 1)]:
        if lic_id in have:
            continue
        requests.put(
            f"{base}/account/licences/{lic_id}",
            headers=headers,
            json={"revision": rev},
            timeout=60,
        )


def days_in_month(year: int, month: int) -> list[str]:
    n = calendar.monthrange(year, month)[1]
    return [f"{d:02d}" for d in range(1, n + 1)]


def subset_latlon(ds: xr.Dataset) -> xr.Dataset:
    lat_name = "lat" if "lat" in ds.coords else "latitude"
    lon_name = "lon" if "lon" in ds.coords else "longitude"
    lat = ds[lat_name]
    if float(lat[0]) > float(lat[-1]):
        return ds.sel({lat_name: slice(LAT_N, LAT_S), lon_name: slice(LON_W, LON_E)})
    return ds.sel({lat_name: slice(LAT_S, LAT_N), lon_name: slice(LON_W, LON_E)})


def parse_date_from_name(name: str, kind: str) -> str | None:
    base = Path(name).name
    if kind == "sst":
        m = re.match(r"^(\d{4})(\d{2})(\d{2})", base)
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.search(r"-(\d{4})(\d{2})(\d{2})-", base)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None


def save_daily_subset(nc_path: Path, kind: str, date: str) -> Path:
    DAILY.mkdir(parents=True, exist_ok=True)
    out = DAILY / f"{kind}_{date}.nc"
    ds = subset_latlon(xr.open_dataset(nc_path))
    varname = "analysed_sst" if kind == "sst" else "chlor_a"
    if varname not in ds:
        raise RuntimeError(f"Variable {varname} no está en {nc_path.name}: {list(ds.data_vars)}")
    da = ds[[varname]]
    if kind == "sst" and float(np.nanmedian(da[varname].values)) > 100:
        da[varname] = da[varname] - 273.15
        da[varname].attrs["units"] = "degree_C"
    t = np.datetime64(date)
    if "time" in da.dims:
        da = da.assign_coords(time=("time", [t]))
    else:
        da = da.expand_dims(time=[t])
    da.attrs.update(
        {
            "bbox": f"{LAT_S},{LON_W},{LAT_N},{LON_E}",
            "source": (
                "CDS satellite-sea-surface-temperature daily L4"
                if kind == "sst"
                else "CDS satellite-ocean-colour daily"
            ),
            "date": date,
        }
    )
    da.to_netcdf(out)
    return out


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def download_month_daily(client: cdsapi.Client, kind: str, year: int, month: int) -> list[Path]:
    """Descarga el mes en bloques (CHL en trozos pequeños para evitar timeouts ~1.5GB)."""
    days = days_in_month(year, month)
    saved: list[Path] = []
    missing = [d for d in days if not (DAILY / f"{kind}_{year}-{month:02d}-{d}.nc").exists()]
    for d in days:
        p = DAILY / f"{kind}_{year}-{month:02d}-{d}.nc"
        if p.exists():
            saved.append(p)
    if not missing:
        print(f"  {kind} {year}-{month:02d}: ya completo ({len(saved)} días)")
        return saved

    # SST ~15MB/día → bloques de 15 días; CHL ~45MB/día → bloques de 7 días
    chunk_size = 15 if kind == "sst" else 7
    print(
        f"  {kind} {year}-{month:02d}: faltan {len(missing)}/{len(days)} días → CDS "
        f"(bloques de {chunk_size})"
    )
    TMP.mkdir(parents=True, exist_ok=True)

    for bi, chunk in enumerate(_chunks(missing, chunk_size), start=1):
        zip_path = TMP / f"{kind}_{year}{month:02d}_p{bi}.zip"
        extract_dir = TMP / f"{kind}_{year}{month:02d}_p{bi}_nc"
        if extract_dir.exists():
            shutil.rmtree(extract_dir)

        if kind == "sst":
            dataset = "satellite-sea-surface-temperature"
            request = {
                "variable": "all",
                "processinglevel": "level_4",
                "sensor_on_satellite": "combined_product",
                "version": "3_0",
                "temporal_resolution": "daily",
                "year": [str(year)],
                "month": [f"{month:02d}"],
                "day": chunk,
            }
        else:
            dataset = "satellite-ocean-colour"
            request = {
                "variable": ["mass_concentration_of_chlorophyll_a"],
                "projection": "regular_latitude_longitude_grid",
                "temporal_resolution": "daily",
                "year": [str(year)],
                "month": [f"{month:02d}"],
                "day": chunk,
                "version": "6_0",
            }

        print(f"    bloque {bi}: días {chunk[0]}–{chunk[-1]}")
        max_attempts = 4
        for attempt in range(1, max_attempts + 1):
            try:
                if zip_path.exists():
                    zip_path.unlink()
                if extract_dir.exists():
                    shutil.rmtree(extract_dir, ignore_errors=True)
                client.retrieve(dataset, request, str(zip_path))
                extract_dir.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(zip_path) as zf:
                    zf.extractall(extract_dir)
                for nc in sorted(extract_dir.rglob("*.nc")):
                    date = parse_date_from_name(nc.name, kind)
                    if not date:
                        print(f"    aviso: no pude parsear fecha de {nc.name}")
                        continue
                    out = save_daily_subset(nc, kind, date)
                    saved.append(out)
                    print(f"    guardado {out.name}")
                break
            except Exception as exc:
                print(
                    f"  FAIL {kind} {year}-{month:02d} bloque {bi} "
                    f"(intento {attempt}/{max_attempts}): {str(exc)[:400]}"
                )
                if attempt < max_attempts:
                    wait = 60 * attempt
                    print(f"    reintento en {wait}s…")
                    time.sleep(wait)
            finally:
                if zip_path.exists():
                    zip_path.unlink(missing_ok=True)
                if extract_dir.exists():
                    shutil.rmtree(extract_dir, ignore_errors=True)

    uniq = {p.resolve(): p for p in saved}
    return sorted(uniq.values())


def monthly_mean_from_daily(kind: str, year: int) -> Path | None:
    MONTHLY.mkdir(parents=True, exist_ok=True)
    varname = "analysed_sst" if kind == "sst" else "chlor_a"
    monthly_arrays = []
    for month in range(1, 13):
        files = sorted(DAILY.glob(f"{kind}_{year}-{month:02d}-*.nc"))
        if not files:
            print(f"  sin diarios para {kind} {year}-{month:02d}")
            continue
        datasets = [xr.open_dataset(f) for f in files]
        daily = xr.concat(datasets, dim="time").sortby("time")
        mean = daily.mean(dim="time", skipna=True)
        mean = mean.expand_dims(time=[np.datetime64(f"{year}-{month:02d}-15")])
        mean.attrs["n_days"] = len(files)
        mean.attrs["method"] = "mean of available daily files"
        out_m = MONTHLY / f"{kind}_{year}-{month:02d}_mean.nc"
        mean.to_netcdf(out_m)
        monthly_arrays.append(mean)
        print(f"  promedio {kind} {year}-{month:02d}: {len(files)} días → {out_m.name}")
        for ds in datasets:
            ds.close()

    if not monthly_arrays:
        return None
    stack = xr.concat(monthly_arrays, dim="time").sortby("time")
    if varname not in stack:
        stack = stack.rename({list(stack.data_vars)[0]: varname})
    out = MONTHLY / f"{kind}_monthly_mean_{year}.nc"
    stack.to_netcdf(out)
    print(f"Stack mensual {kind}: {out}")
    return out


def add_basemap(ax) -> None:
    ax.set_extent([LON_W, LON_E, LAT_S, LAT_N], crs=ccrs.PlateCarree())
    ax.add_feature(cfeature.OCEAN.with_scale("10m"), facecolor="#d9e8f5", zorder=0)
    ax.add_feature(cfeature.LAND.with_scale("10m"), facecolor="#e8e4dc", zorder=2)
    ax.add_feature(cfeature.COASTLINE.with_scale("10m"), linewidth=0.7, edgecolor="#333", zorder=3)
    ax.set_xticks([])
    ax.set_yticks([])


def plot_monthly_grid(stack_path: Path, kind: str) -> None:
    """Mapas mensuales con colorbar a la derecha, fuera de los paneles."""
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

    if kind == "sst":
        cmap, units = "turbo", "°C"
        title = f"SST promedio mensual {YEAR} (desde diarios CDS)"
        norm = Normalize(vmin=float(np.nanpercentile(valid, 2)), vmax=float(np.nanpercentile(valid, 98)))
    else:
        cmap, units = "YlGn", "mg m$^{-3}$"
        title = f"Clorofila-a promedio mensual {YEAR} (desde diarios CDS)"
        pos = valid[valid > 0]
        vmin = max(float(np.nanpercentile(pos, 5)), 0.05) if pos.size else 0.05
        vmax = float(np.nanpercentile(pos, 98)) if pos.size else 1.0
        norm = LogNorm(vmin=vmin, vmax=max(vmax, vmin * 1.1))

    n = da.sizes["time"]
    ncols, nrows = 4, int(np.ceil(n / 4))
    fig, axes = plt.subplots(
        nrows,
        ncols,
        figsize=(12.8, 3.35 * nrows + 0.8),
        subplot_kw={"projection": ccrs.PlateCarree()},
    )
    axes = np.atleast_2d(axes)
    # Colorbar horizontal abajo: nunca tapa los mapas
    fig.subplots_adjust(left=0.04, right=0.98, top=0.90, bottom=0.12, wspace=0.10, hspace=0.28)

    mesh = None
    for i in range(nrows * ncols):
        r, c = divmod(i, ncols)
        ax = axes[r, c]
        if i >= n:
            ax.set_visible(False)
            continue
        add_basemap(ax)
        data = np.ma.masked_invalid(da.isel(time=i).values)
        if kind == "chl":
            data = np.ma.masked_where(data <= 0, data)
        mesh = ax.pcolormesh(
            lon,
            lat,
            data,
            transform=ccrs.PlateCarree(),
            cmap=cmap,
            norm=norm,
            shading="auto",
            zorder=1,
            alpha=0.92,
        )
        t = np.datetime_as_string(da.time.values[i], unit="M")
        ax.set_title(f"{MONTH_LABELS[int(t.split('-')[1]) - 1]} {YEAR}", fontsize=10)

    fig.suptitle(
        f"{title}\nBbox: {LAT_S:.2f}–{LAT_N:.2f}°N, {LON_W:.2f}–{LON_E:.2f}°W",
        fontsize=12,
        y=0.98,
    )
    if mesh is not None:
        cax = fig.add_axes([0.18, 0.04, 0.64, 0.03])  # barra horizontal bajo los mapas
        cbar = fig.colorbar(mesh, cax=cax, orientation="horizontal")
        cbar.set_label(units, fontsize=10)

    FIGS.mkdir(parents=True, exist_ok=True)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    for dest in (FIGS / f"{kind}_monthly_{YEAR}.png", ARTIFACTS / f"{kind}_monthly_{YEAR}.png"):
        # sin bbox_inches='tight' para no compactar y solapar la colorbar
        fig.savefig(dest, dpi=150)
        print(f"Mapa: {dest}")
    plt.close(fig)


def plot_timeseries(sst_stack: Path | None, chl_stack: Path | None) -> None:
    fig, axes = plt.subplots(2, 1, figsize=(9.5, 6.2), sharex=True)
    if sst_stack and sst_stack.exists():
        da = xr.open_dataset(sst_stack)["analysed_sst"]
        series = da.mean(dim=[d for d in da.dims if d != "time"], skipna=True)
        axes[0].plot(series.time.values, series.values, marker="o", color="#c0392b")
        axes[0].set_ylabel("SST (°C)")
        axes[0].set_title("Promedio espacial del promedio mensual — SST")
        axes[0].grid(True, alpha=0.35)
    if chl_stack and chl_stack.exists():
        da = xr.open_dataset(chl_stack)["chlor_a"]
        log = np.log(da.where(da > 0))
        series = np.exp(log.mean(dim=[d for d in da.dims if d != "time"], skipna=True))
        axes[1].plot(series.time.values, series.values, marker="o", color="#1e8449")
        axes[1].set_ylabel("Clorofila-a (mg m$^{-3}$)")
        axes[1].set_title("Promedio espacial del promedio mensual — Clorofila-a")
        axes[1].set_yscale("log")
        axes[1].grid(True, alpha=0.35)
    axes[1].set_xlabel("Mes")
    fig.suptitle(
        f"Series {YEAR} (medias mensuales desde diarios) — "
        f"{LAT_S:.2f}–{LAT_N:.2f}N, {LON_W:.2f}–{LON_E:.2f}W",
        fontsize=11,
    )
    fig.tight_layout()
    FIGS.mkdir(parents=True, exist_ok=True)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    for dest in (FIGS / f"timeseries_sst_chl_{YEAR}.png", ARTIFACTS / f"timeseries_sst_chl_{YEAR}.png"):
        fig.savefig(dest, dpi=150, bbox_inches="tight")
        print(f"Serie: {dest}")
    plt.close(fig)


def write_manifest() -> None:
    sst_days = sorted(DAILY.glob(f"sst_{YEAR}-*.nc"))
    chl_days = sorted(DAILY.glob(f"chl_{YEAR}-*.nc"))
    lines = [
        f"# Datos {YEAR} — diarios + promedios mensuales (calculados)",
        "",
        f"Bbox: {LAT_S},{LON_W},{LAT_N},{LON_E}",
        "",
        "## Diarios (conservar para evaluaciones futuras)",
        f"- SST: {len(sst_days)} archivos en `data/daily_{YEAR}/sst_YYYY-MM-DD.nc`",
        f"- CHL: {len(chl_days)} archivos en `data/daily_{YEAR}/chl_YYYY-MM-DD.nc`",
        "",
        "## Promedios mensuales (media aritmética de días disponibles)",
        f"- `data/monthly_from_daily_{YEAR}/sst_YYYY-MM_mean.nc`",
        f"- `data/monthly_from_daily_{YEAR}/chl_YYYY-MM_mean.nc`",
        f"- stacks: `sst_monthly_mean_{YEAR}.nc`, `chl_monthly_mean_{YEAR}.nc`",
        "",
        "Fuente: Copernicus CDS diarios (no productos mensuales).",
    ]
    DAILY.mkdir(parents=True, exist_ok=True)
    MONTHLY.mkdir(parents=True, exist_ok=True)
    (DAILY / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (MONTHLY / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=2025, help="Año a procesar (default 2025)")
    parser.add_argument(
        "--plot-only",
        action="store_true",
        help="Solo recalcular promedios/mapas con diarios ya descargados",
    )
    args = parser.parse_args()
    configure_year(args.year)

    DAILY.mkdir(parents=True, exist_ok=True)
    MONTHLY.mkdir(parents=True, exist_ok=True)

    if not args.plot_only:
        client = cds_client()
        accept_licences(client)
        print(
            f"Descarga diaria {YEAR} SST+CHL | bbox "
            f"{LAT_S}–{LAT_N}N, {LON_W}–{LON_E}W"
        )
        for month in range(1, 13):
            print(f"== {YEAR}-{month:02d} ==")
            download_month_daily(client, "sst", YEAR, month)
            download_month_daily(client, "chl", YEAR, month)
    else:
        print(f"Modo plot-only para {YEAR}")

    print("\nCalculando promedios mensuales desde diarios...")
    sst_stack = monthly_mean_from_daily("sst", YEAR)
    chl_stack = monthly_mean_from_daily("chl", YEAR)
    write_manifest()

    if sst_stack:
        plot_monthly_grid(sst_stack, "sst")
    if chl_stack:
        plot_monthly_grid(chl_stack, "chl")
    plot_timeseries(sst_stack, chl_stack)

    print("\nResumen:")
    print(f"  diarios SST: {len(list(DAILY.glob(f'sst_{YEAR}-*.nc')))}")
    print(f"  diarios CHL: {len(list(DAILY.glob(f'chl_{YEAR}-*.nc')))}")
    print(f"  mensuales: {MONTHLY}")


if __name__ == "__main__":
    main()
