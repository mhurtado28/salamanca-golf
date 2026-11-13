#!/usr/bin/env python3
"""Régimen de lluvias y extremos con CHIRPS v3 + validación con estaciones IDEAM.

Entradas:
  data/rain/chirps/chirps_v3_<prod>_<año>.nc   (rain_chirps_download.py)
  data/ideam/ideam_daily.csv                   (ideam_dhime.py, opcional)

Salidas (data/rain/, figures/rain/):
  - índice diario de lluvia por cuenca/zona (media espacial de la caja del config)
  - régimen mensual (mm/mes) por cuenca y mapa de climatología mensual
  - extremos anuales por cuenca: total, Rx1day, Rx5day, R20mm, días>P95, total>P95
  - estaciones IDEAM vs celda CHIRPS más cercana: r diario/mensual, sesgo, P95, Rx1day
"""

from __future__ import annotations

import argparse

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import xarray as xr

from common import DATA, FIGS, MONTH_LABELS, load_config

IN = DATA / "rain" / "chirps"
OUT = DATA / "rain"
FIG = FIGS / "rain"
WET = 1.0  # mm: umbral de día lluvioso (ETCCDI)


def open_chirps(prod: str, start: int | None, end: int | None) -> xr.DataArray:
    files = sorted(IN.glob(f"chirps_v3_{prod}_*.nc"))
    if start or end:
        files = [f for f in files
                 if (start or 0) <= int(f.stem.rsplit("_", 1)[1]) <= (end or 9999)]
    if not files:
        raise SystemExit(f"No hay CHIRPS en {IN}")
    return xr.concat([xr.open_dataset(f)["precip"].load() for f in files], dim="time").sortby("time")


def basin_series(pr: xr.DataArray, cuencas: dict) -> pd.DataFrame:
    out = {}
    for k, b in cuencas.items():
        sub = pr.sel(lat=slice(b["lat_s"], b["lat_n"]), lon=slice(b["lon_w"], b["lon_e"]))
        out[k] = sub.mean(("lat", "lon"), skipna=True).to_series()
    df = pd.DataFrame(out)
    df.index.name = "fecha"
    return df


def extremes(s: pd.Series) -> pd.DataFrame:
    s = s.dropna()
    p95 = s[s >= WET].quantile(0.95)
    g = s.groupby(s.index.year)
    rx5 = s.rolling(5).sum()
    return pd.DataFrame({
        "total_mm": g.sum(),
        "dias_lluvia": g.apply(lambda x: int((x >= WET).sum())),
        "Rx1day_mm": g.max(),
        "fecha_Rx1day": g.idxmax().dt.strftime("%Y-%m-%d"),
        "Rx5day_mm": rx5.groupby(rx5.index.year).max(),
        "R20mm_dias": g.apply(lambda x: int((x >= 20).sum())),
        "dias_sobre_P95": g.apply(lambda x: int((x > p95).sum())),
        "total_sobre_P95_mm": g.apply(lambda x: float(x[x > p95].sum())),
    }).assign(P95_referencia_mm=round(float(p95), 2))


def monthly_regime(df: pd.DataFrame) -> pd.DataFrame:
    mon = df.resample("MS").sum(min_count=20)
    return mon.groupby(mon.index.month).mean()


def plots(df: pd.DataFrame, pr: xr.DataArray, ext: dict) -> None:
    FIG.mkdir(parents=True, exist_ok=True)
    reg = monthly_regime(df)
    fig, ax = plt.subplots(figsize=(9, 4))
    w = 0.8 / len(reg.columns)
    for i, c in enumerate(reg.columns):
        ax.bar(np.arange(1, 13) + (i - (len(reg.columns) - 1) / 2) * w, reg[c], w, label=c)
    ax.set_xticks(range(1, 13), MONTH_LABELS)
    ax.set_ylabel("mm/mes")
    ax.set_title("Régimen mensual de lluvia (CHIRPS v3)")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(FIG / "regimen_mensual_cuencas.png", dpi=150)
    plt.close(fig)

    clim = pr.resample(time="MS").sum(min_count=20).groupby("time.month").mean()
    fig, axes = plt.subplots(3, 4, figsize=(14, 9), sharex=True, sharey=True)
    vmax = float(clim.quantile(0.98))
    for m, ax in zip(range(1, 13), axes.ravel()):
        pc = ax.pcolormesh(clim.lon, clim.lat, clim.sel(month=m), vmin=0, vmax=vmax,
                           cmap="YlGnBu", shading="auto")
        ax.set_title(MONTH_LABELS[m - 1])
    fig.colorbar(pc, ax=axes, shrink=0.6, label="mm/mes")
    fig.suptitle("CHIRPS v3 — lluvia mensual climatológica")
    fig.savefig(FIG / "climatologia_mensual_mapa.png", dpi=150)
    plt.close(fig)

    fig, axes = plt.subplots(2, 2, figsize=(12, 7), sharex=True)
    for ax, col, lab in zip(axes.ravel(),
                            ["total_mm", "Rx1day_mm", "Rx5day_mm", "dias_sobre_P95"],
                            ["Total anual (mm)", "Rx1day (mm)", "Rx5day (mm)", "Días > P95"]):
        for k, e in ext.items():
            ax.plot(e.index, e[col], marker="o", ms=3, label=k)
        ax.set_title(lab, fontsize=10)
    axes[0, 0].legend(fontsize=7)
    fig.suptitle("Extremos anuales de lluvia por cuenca (CHIRPS v3)")
    fig.tight_layout()
    fig.savefig(FIG / "extremos_anuales.png", dpi=150)
    plt.close(fig)


def validate_stations(pr: xr.DataArray) -> None:
    f = DATA / "ideam" / "ideam_daily.csv"
    if not f.exists():
        print("  (sin IDEAM: se omite la validación con estaciones)")
        return
    obs = pd.read_csv(f, parse_dates=["fecha"])
    obs = obs[obs["etiqueta"].str.startswith("PTPM")]
    if obs.empty:
        print("  (IDEAM sin precipitación PTPM)")
        return
    rows, pairs = [], []
    for (code, name), g in obs.groupby(["estacion_codigo", "estacion_nombre"]):
        lat, lon = g["lat"].iloc[0], g["lon"].iloc[0]
        if pd.isna(lat) or pd.isna(lon):
            print(f"  {name}: sin coordenadas, omitida")
            continue
        cell = pr.sel(lat=lat, lon=lon, method="nearest")
        if cell.isnull().all():  # la celda más cercana es mar: buscar vecina en tierra
            near = pr.sel(lat=slice(lat - 0.1, lat + 0.1), lon=slice(lon - 0.1, lon + 0.1))
            valid = near.notnull().any("time")
            if not bool(valid.any()):
                continue
            dist = (near.lat - lat) ** 2 + (near.lon - lon) ** 2
            idx = dist.where(valid).argmin(dim=["lat", "lon"])
            cell = near.isel(idx)
        s_obs = g.set_index("fecha")["valor"]
        s_ch = cell.to_series()
        j = pd.concat([s_obs.rename("ideam"), s_ch.rename("chirps")], axis=1).dropna()
        if len(j) < 365:
            continue
        mj = j.resample("MS").sum(min_count=20).dropna()
        rows.append({
            "estacion": name, "codigo": code, "lat": lat, "lon": lon,
            "celda_lat": float(cell.lat), "celda_lon": float(cell.lon), "n_dias": len(j),
            "r_diario": j.corr().iloc[0, 1], "r_mensual": mj.corr().iloc[0, 1],
            "sesgo_pct": 100 * (j.chirps.sum() - j.ideam.sum()) / j.ideam.sum(),
            "P95_ideam": j.ideam[j.ideam >= WET].quantile(0.95),
            "P95_chirps": j.chirps[j.chirps >= WET].quantile(0.95),
            "Rx1day_medio_ideam": j.ideam.groupby(j.index.year).max().mean(),
            "Rx1day_medio_chirps": j.chirps.groupby(j.index.year).max().mean(),
        })
        pairs.append((name, mj))
    if not rows:
        print("  (ninguna estación con ≥365 días coincidentes)")
        return
    res = pd.DataFrame(rows)
    res.to_csv(OUT / "validacion_chirps_vs_ideam.csv", index=False, float_format="%.3f")
    print(res[["estacion", "r_diario", "r_mensual", "sesgo_pct", "P95_ideam", "P95_chirps"]]
          .to_string(index=False))
    n = len(pairs)
    fig, axes = plt.subplots(1, n, figsize=(4 * n, 4), squeeze=False)
    for ax, (name, mj) in zip(axes[0], pairs):
        ax.scatter(mj.ideam, mj.chirps, s=10)
        lim = max(mj.max()) * 1.05
        ax.plot([0, lim], [0, lim], "k--", lw=0.7)
        ax.set_xlabel("IDEAM (mm/mes)")
        ax.set_ylabel("CHIRPS (mm/mes)")
        ax.set_title(name, fontsize=9)
    fig.tight_layout()
    fig.savefig(FIG / "validacion_estaciones_mensual.png", dpi=150)
    plt.close(fig)


def main() -> None:
    cfg = load_config()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", type=int)
    ap.add_argument("--end", type=int)
    ap.add_argument("--product", default=cfg["chirps"].get("producto", "sat"))
    a = ap.parse_args()
    pr = open_chirps(a.product, a.start, a.end)
    OUT.mkdir(parents=True, exist_ok=True)
    df = basin_series(pr, cfg["chirps"]["cuencas"])
    df.to_csv(OUT / "lluvia_diaria_cuencas.csv", float_format="%.3f")
    monthly_regime(df).to_csv(OUT / "regimen_mensual_cuencas.csv", float_format="%.1f")
    ext = {k: extremes(df[k]) for k in df}
    with pd.ExcelWriter(OUT / "extremos_anuales_cuencas.xlsx") as xw:
        for k, e in ext.items():
            e.to_excel(xw, sheet_name=k[:31])
    plots(df, pr, ext)
    validate_stations(pr)
    print(f"Listo → {OUT} y {FIG}")


if __name__ == "__main__":
    main()
