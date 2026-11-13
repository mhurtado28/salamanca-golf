#!/usr/bin/env python3
"""Viento ERA5 (horario → diario) e índice de surgencia de Ekman para el golfo.

- Esfuerzo del viento horario: tau = rho_a * Cd * |U| * U  (Cd = 1.3e-3)
- Transporte de Ekman por metro de costa: Mx = tau_y/(rho_w f), My = -tau_x/(rho_w f)
- Índice de surgencia (UI) = componente mar afuera del transporte (m²/s);
  positivo = surgencia. La dirección mar afuera se define en config/pipeline.yaml.

El viento es reanálisis (ERA5): es lo estándar para forzantes; la TSM sigue
siendo satelital.

Ejemplos:
  python scripts/wind_era5.py --start 2015 --end 2025
  python scripts/wind_era5.py --process-only
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import xarray as xr

from common import DATA, FIGS, MONTH_LABELS, cds_area, cds_client, load_config, std_latlon, time_name

RAW = DATA / "wind" / "era5_raw"
OUT = DATA / "wind"
FIG = FIGS / "wind"

RHO_A, RHO_W, CD = 1.22, 1025.0, 1.3e-3


def download_year(year: int, box: dict) -> Path:
    RAW.mkdir(parents=True, exist_ok=True)
    out = RAW / f"era5_wind10m_{year}.nc"
    if out.exists() and out.stat().st_size > 0:
        print(f"  {year}: ya descargado")
        return out
    request = {
        "product_type": ["reanalysis"],
        "variable": ["10m_u_component_of_wind", "10m_v_component_of_wind"],
        "year": [str(year)],
        "month": [f"{m:02d}" for m in range(1, 13)],
        "day": [f"{d:02d}" for d in range(1, 32)],
        "time": [f"{h:02d}:00" for h in range(24)],
        "data_format": "netcdf",
        "download_format": "unarchived",
        "area": cds_area(box),
    }
    print(f"  {year}: pidiendo ERA5 a CDS…")
    tmp = out.with_suffix(".part")
    cds_client("cds").retrieve("reanalysis-era5-single-levels", request, str(tmp))
    tmp.rename(out)
    return out


def hourly_to_daily(ds: xr.Dataset, offshore_deg: float) -> xr.Dataset:
    ds = std_latlon(ds)
    tn = time_name(ds)
    if tn != "time":
        ds = ds.rename({tn: "time"})
    u, v = ds["u10"], ds["v10"]
    lat = ds["lat"]
    speed = np.hypot(u, v)
    taux = RHO_A * CD * speed * u
    tauy = RHO_A * CD * speed * v
    f = 2 * 7.2921e-5 * np.sin(np.deg2rad(lat))
    mx = tauy / (RHO_W * f)
    my = -taux / (RHO_W * f)
    th = np.deg2rad(offshore_deg)
    ui = mx * np.sin(th) + my * np.cos(th)
    hourly = xr.Dataset({"u10": u, "v10": v, "speed": speed,
                         "taux": taux, "tauy": tauy, "UI": ui})
    daily = hourly.resample(time="1D").mean()
    daily["UI"].attrs = {"units": "m2 s-1", "long_name": "Índice de surgencia de Ekman (mar afuera +)"}
    daily["taux"].attrs = daily["tauy"].attrs = {"units": "N m-2"}
    daily["speed"].attrs = {"units": "m s-1", "long_name": "media diaria de |U| horario"}
    return daily


def process(cfg: dict) -> None:
    files = sorted(RAW.glob("era5_wind10m_*.nc"))
    if not files:
        raise SystemExit(f"No hay archivos ERA5 en {RAW}")
    vc = cfg["viento"]
    daily = xr.concat([hourly_to_daily(xr.open_dataset(f), vc["offshore_deg"]) for f in files],
                      dim="time").sortby("time")
    drop = [c for c in daily.coords if c not in ("time", "lat", "lon")]
    daily = daily.drop_vars(drop)
    OUT.mkdir(parents=True, exist_ok=True)
    daily.to_netcdf(OUT / "era5_viento_diario.nc")

    p = vc["punto_indice"]
    pt = daily.sel(lat=p["lat"], lon=p["lon"], method="nearest")
    g = cfg["golfo"]
    gb = daily.sel(lat=slice(g["lat_s"], g["lat_n"]), lon=slice(g["lon_w"], g["lon_e"]))
    if gb.sizes.get("lat", 0) == 0 or gb.sizes.get("lon", 0) == 0:
        gb = pt
    gmean = gb.mean([d for d in ("lat", "lon") if d in gb.dims])
    df = pd.DataFrame({
        "u10": gmean.u10.values, "v10": gmean.v10.values, "rapidez": gmean.speed.values,
        "UI_golfo": gmean.UI.values, "UI_punto": pt.UI.values,
    }, index=pd.to_datetime(gmean.time.values))
    df.index.name = "fecha"
    df.to_csv(OUT / "indice_surgencia_diario.csv", float_format="%.4f")

    # Eventos de surgencia fuerte: UI > P90 por >= 2 días seguidos
    thr = df["UI_golfo"].quantile(0.90)
    above = df["UI_golfo"] > thr
    grp = (above != above.shift()).cumsum()
    ev = (df[above].groupby(grp[above])
          .agg(inicio=("UI_golfo", lambda s: s.index.min()),
               fin=("UI_golfo", lambda s: s.index.max()),
               dias=("UI_golfo", "size"), UI_max=("UI_golfo", "max")))
    ev = ev[ev.dias >= 2].reset_index(drop=True)
    ev.to_csv(OUT / "eventos_viento_surgencia_P90.csv", index=False)
    print(f"  {len(ev)} eventos de viento favorable a surgencia (UI>P90={thr:.2f}, ≥2 días)")

    plots(df, daily, ev)
    print(f"Listo → {OUT} y {FIG}")


def plots(df: pd.DataFrame, daily: xr.Dataset, ev: pd.DataFrame) -> None:
    FIG.mkdir(parents=True, exist_ok=True)
    # Ciclo anual del índice
    g = df["UI_golfo"].groupby(df.index.month)
    fig, ax = plt.subplots(figsize=(8, 3.6))
    ax.fill_between(range(1, 13), g.quantile(0.1), g.quantile(0.9), alpha=0.25, color="#2a7")
    ax.plot(range(1, 13), g.mean(), marker="o", color="#174")
    ax.axhline(0, color="k", lw=0.6)
    ax.set_xticks(range(1, 13), MONTH_LABELS)
    ax.set_ylabel("UI (m²/s)")
    ax.set_title("Índice de surgencia de Ekman — ciclo anual (media; banda P10–P90 diaria)")
    fig.tight_layout()
    fig.savefig(FIG / "UI_ciclo_anual.png", dpi=150)
    plt.close(fig)

    # Serie diaria con eventos
    fig, ax = plt.subplots(figsize=(13, 3.6))
    ax.plot(df.index, df["UI_golfo"], lw=0.5, color="#174")
    for _, e in ev.iterrows():
        ax.axvspan(e.inicio, e.fin + pd.Timedelta(days=1), color="#c33", alpha=0.25, lw=0)
    ax.axhline(0, color="k", lw=0.6)
    ax.set_ylabel("UI (m²/s)")
    ax.set_title("Índice de surgencia diario (rojo: eventos UI>P90 ≥2 días)")
    fig.tight_layout()
    fig.savefig(FIG / "UI_diario_eventos.png", dpi=150)
    plt.close(fig)

    # Climatología mensual de viento (vectores)
    mon = daily[["u10", "v10", "speed"]].groupby("time.month").mean()
    fig, axes = plt.subplots(3, 4, figsize=(14, 8.5), sharex=True, sharey=True)
    vmax = float(mon.speed.max())
    for m, ax in zip(range(1, 13), axes.ravel()):
        s = mon.sel(month=m)
        pc = ax.pcolormesh(s.lon, s.lat, s.speed, vmin=0, vmax=vmax, cmap="viridis", shading="auto")
        ax.quiver(s.lon, s.lat, s.u10, s.v10, color="w", scale=120)
        ax.set_title(MONTH_LABELS[m - 1])
    fig.colorbar(pc, ax=axes, shrink=0.6, label="m/s")
    fig.suptitle("ERA5 — viento a 10 m, climatología mensual")
    fig.savefig(FIG / "viento_climatologia_mensual.png", dpi=150)
    plt.close(fig)


def main() -> None:
    cfg = load_config()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", type=int, default=cfg["periodo"]["inicio"])
    ap.add_argument("--end", type=int, default=cfg["periodo"]["fin"])
    ap.add_argument("--process-only", action="store_true")
    a = ap.parse_args()
    if not a.process_only:
        for y in range(a.start, a.end + 1):
            download_year(y, cfg["viento"])
    process(cfg)


if __name__ == "__main__":
    main()
