#!/usr/bin/env python3
"""Caudal diario de los ríos (Magdalena, Toribio, Córdoba) desde GloFAS v4.

Descarga (EWDS) un año por petición para una caja que cubre todos los ríos,
elige en cada caja pequeña la celda con mayor caudal medio (cauce principal),
guarda series diarias en CSV y genera figuras de régimen y extremos.
Si existe data/ideam/ideam_daily.csv con caudales de Calamar, valida GloFAS.

Ejemplos:
  python scripts/rivers_glofas.py --start 2015 --end 2025
  python scripts/rivers_glofas.py --start 1995 --end 1998      # periodo del paper
  python scripts/rivers_glofas.py --process-only               # sin descargar
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import xarray as xr

from common import DATA, FIGS, MONTH_LABELS, cds_client, load_config, std_latlon, time_name

RAW = DATA / "rivers" / "glofas_raw"
OUT = DATA / "rivers"
FIG = FIGS / "rivers"


def big_box(rios: dict) -> dict:
    pad = max(r["caja"] for r in rios.values()) + 0.05
    lats = [r["lat"] for r in rios.values()]
    lons = [r["lon"] for r in rios.values()]
    return {"lat_s": min(lats) - pad, "lat_n": max(lats) + pad,
            "lon_w": min(lons) - pad, "lon_e": max(lons) + pad}


def download_year(year: int, box: dict) -> Path:
    RAW.mkdir(parents=True, exist_ok=True)
    out = RAW / f"glofas_dis24_{year}.nc"
    if out.exists() and out.stat().st_size > 0:
        print(f"  {year}: ya descargado")
        return out
    request = {
        "system_version": ["version_4_0"],
        "hydrological_model": ["lisflood"],
        "product_type": ["consolidated"],
        "variable": ["river_discharge_in_the_last_24_hours"],
        "hyear": [str(year)],
        "hmonth": [f"{m:02d}" for m in range(1, 13)],
        "hday": [f"{d:02d}" for d in range(1, 32)],
        "data_format": "netcdf",
        "download_format": "unarchived",
        "area": [round(box["lat_n"], 3), round(box["lon_w"], 3),
                 round(box["lat_s"], 3), round(box["lon_e"], 3)],
    }
    print(f"  {year}: pidiendo GloFAS a EWDS…")
    tmp = out.with_suffix(".part")
    cds_client("ewds").retrieve("cems-glofas-historical", request, str(tmp))
    tmp.rename(out)
    return out


def open_all() -> xr.DataArray:
    files = sorted(RAW.glob("glofas_dis24_*.nc"))
    if not files:
        raise SystemExit(f"No hay archivos GloFAS en {RAW}")
    das = []
    for f in files:
        ds = std_latlon(xr.open_dataset(f))
        var = "dis24" if "dis24" in ds else list(ds.data_vars)[0]
        da = ds[var]
        tn = time_name(da)
        if tn != "time":
            da = da.rename({tn: "time"})
        das.append(da.drop_vars([c for c in da.coords if c not in ("time", "lat", "lon")]))
    da = xr.concat(das, dim="time").sortby("time")
    _, idx = np.unique(da.time.values, return_index=True)
    return da.isel(time=idx)


def pick_cell(da: xr.DataArray, r: dict) -> tuple[float, float]:
    sub = da.sel(lat=slice(r["lat"] - r["caja"], r["lat"] + r["caja"]),
                 lon=slice(r["lon"] - r["caja"], r["lon"] + r["caja"]))
    mean = sub.mean("time", skipna=True)
    if mean.isnull().all():
        raise ValueError(f"Sin datos GloFAS alrededor de {r['nombre']}")
    st = mean.argmax(dim=("lat", "lon"))
    return float(sub.lat[st["lat"]]), float(sub.lon[st["lon"]])


def extremes(series: pd.Series) -> pd.DataFrame:
    df = series.dropna().to_frame("q")
    df["year"] = df.index.year
    g = df.groupby("year")["q"]
    q95 = series.quantile(0.95)
    q05 = series.quantile(0.05)
    return pd.DataFrame({
        "media": g.mean(),
        "max_diario": g.max(),
        "fecha_max": g.idxmax().dt.strftime("%Y-%m-%d"),
        "min_diario": g.min(),
        "dias_sobre_Q95": g.apply(lambda s: int((s > q95).sum())),
        "dias_bajo_Q05": g.apply(lambda s: int((s < q05).sum())),
    })


def validate_calamar(q: pd.DataFrame) -> None:
    ideam = DATA / "ideam" / "ideam_daily.csv"
    if not ideam.exists() or "magdalena_calamar" not in q:
        return
    obs = pd.read_csv(ideam, parse_dates=["fecha"])
    obs = obs[obs["etiqueta"].str.upper().str.startswith("Q_")
              & obs["estacion_nombre"].str.upper().str.contains("CALAMAR")]
    if obs.empty:
        print("  (IDEAM: no hay caudales de Calamar para validar)")
        return
    o = obs.groupby("fecha")["valor"].mean()
    m = q["magdalena_calamar"]
    j = pd.concat([o.rename("ideam"), m.rename("glofas")], axis=1).dropna()
    if len(j) < 30:
        return
    mj = j.resample("MS").mean().dropna()
    stats = {
        "n_dias": len(j),
        "r_diario": j.corr().iloc[0, 1],
        "r_mensual": mj.corr().iloc[0, 1],
        "sesgo_pct": 100 * (j.glofas.mean() - j.ideam.mean()) / j.ideam.mean(),
        "KGE": kge(j.ideam.values, j.glofas.values),
    }
    pd.Series(stats).to_csv(OUT / "validacion_calamar_glofas_vs_ideam.csv", header=["valor"])
    fig, ax = plt.subplots(figsize=(11, 4))
    ax.plot(j.index, j.ideam, lw=0.8, label="IDEAM Calamar")
    ax.plot(j.index, j.glofas, lw=0.8, label="GloFAS v4")
    ax.set_ylabel("Caudal (m³/s)")
    ax.set_title(f"Magdalena en Calamar — r diario={stats['r_diario']:.2f}, "
                 f"sesgo={stats['sesgo_pct']:.0f} %, KGE={stats['KGE']:.2f}")
    ax.legend()
    fig.tight_layout()
    fig.savefig(FIG / "validacion_calamar.png", dpi=150)
    plt.close(fig)
    print(f"  validación Calamar: r={stats['r_diario']:.2f}, KGE={stats['KGE']:.2f}")


def kge(obs: np.ndarray, sim: np.ndarray) -> float:
    r = np.corrcoef(obs, sim)[0, 1]
    alpha = np.std(sim) / np.std(obs)
    beta = np.mean(sim) / np.mean(obs)
    return float(1 - np.sqrt((r - 1) ** 2 + (alpha - 1) ** 2 + (beta - 1) ** 2))


def plots(q: pd.DataFrame, rios: dict, da: xr.DataArray, cells: dict) -> None:
    FIG.mkdir(parents=True, exist_ok=True)
    keys = list(q.columns)

    # Series diarias
    fig, axes = plt.subplots(len(keys), 1, figsize=(12, 2.4 * len(keys)), sharex=True)
    for ax, k in zip(np.atleast_1d(axes), keys):
        ax.plot(q.index, q[k], lw=0.6, color="#1f5f99")
        ax.set_ylabel("m³/s")
        ax.set_title(rios[k]["nombre"], fontsize=10, loc="left")
    fig.suptitle("Caudal diario GloFAS v4")
    fig.tight_layout()
    fig.savefig(FIG / "caudal_diario.png", dpi=150)
    plt.close(fig)

    # Régimen mensual (media y percentiles 10–90 de medias mensuales)
    fig, axes = plt.subplots(1, len(keys), figsize=(3.6 * len(keys), 3.4))
    for ax, k in zip(np.atleast_1d(axes), keys):
        mon = q[k].resample("MS").mean()
        g = mon.groupby(mon.index.month)
        ax.fill_between(range(1, 13), g.quantile(0.1), g.quantile(0.9), alpha=0.25)
        ax.plot(range(1, 13), g.mean(), marker="o")
        ax.set_xticks(range(1, 13), [m[0] for m in MONTH_LABELS])
        ax.set_title(rios[k]["nombre"], fontsize=9)
        ax.set_ylabel("m³/s")
    fig.suptitle("Régimen mensual (media; banda = P10–P90 entre años)")
    fig.tight_layout()
    fig.savefig(FIG / "regimen_mensual.png", dpi=150)
    plt.close(fig)

    # Mapa de control: celda elegida por río
    fig, axes = plt.subplots(1, len(rios), figsize=(3.4 * len(rios), 3.4))
    mean = da.mean("time", skipna=True)
    for ax, (k, r) in zip(np.atleast_1d(axes), rios.items()):
        sub = mean.sel(lat=slice(r["lat"] - r["caja"], r["lat"] + r["caja"]),
                       lon=slice(r["lon"] - r["caja"], r["lon"] + r["caja"]))
        sub.plot(ax=ax, add_colorbar=False, cmap="Blues")
        ax.plot(r["lon"], r["lat"], "k+", ms=10, label="punto config")
        ax.plot(cells[k][1], cells[k][0], "ro", mfc="none", ms=10, label="celda usada")
        ax.set_title(r["nombre"], fontsize=9)
        ax.set_xlabel("")
        ax.set_ylabel("")
    np.atleast_1d(axes)[0].legend(fontsize=7)
    fig.suptitle("Control: celda GloFAS elegida (caudal medio)")
    fig.tight_layout()
    fig.savefig(FIG / "control_celdas_glofas.png", dpi=150)
    plt.close(fig)


def process(cfg: dict) -> None:
    rios = cfg["rios"]
    da = open_all()
    OUT.mkdir(parents=True, exist_ok=True)
    cells, series = {}, {}
    for k, r in rios.items():
        lat, lon = pick_cell(da, r)
        cells[k] = (lat, lon)
        s = da.sel(lat=lat, lon=lon).to_series()
        s.index = pd.to_datetime(s.index).normalize()
        series[k] = s
        print(f"  {r['nombre']}: celda {lat:.3f}, {lon:.3f} — Q medio {s.mean():.1f} m³/s")
    q = pd.DataFrame(series)
    q.index.name = "fecha"
    q.to_csv(OUT / "glofas_caudal_diario.csv", float_format="%.3f")
    pd.DataFrame(cells, index=["lat", "lon"]).T.to_csv(OUT / "glofas_celdas_usadas.csv")
    with pd.ExcelWriter(OUT / "glofas_extremos_anuales.xlsx") as xw:
        for k in q:
            extremes(q[k]).to_excel(xw, sheet_name=k[:31])
    plots(q, rios, da, cells)
    validate_calamar(q)
    print(f"Listo → {OUT} y {FIG}")


def main() -> None:
    cfg = load_config()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", type=int, default=cfg["periodo"]["inicio"])
    ap.add_argument("--end", type=int, default=cfg["periodo"]["fin"])
    ap.add_argument("--process-only", action="store_true")
    a = ap.parse_args()
    if not a.process_only:
        box = big_box(cfg["rios"])
        for y in range(a.start, a.end + 1):
            download_year(y, box)
    process(cfg)


if __name__ == "__main__":
    main()
