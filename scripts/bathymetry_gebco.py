#!/usr/bin/env python3
"""Batimetría del golfo con GEBCO_2025 + grilla TID (origen de los datos).

GEBCO no tiene API de recortes: descarga a mano desde https://download.gebco.net
la caja 10.90–11.40 N, 74.95–74.10 W en NetCDF, marcando GEBCO_2025 Grid y TID Grid.
Pon los .nc (o el .zip descomprimido) en data/bathymetry/raw/.

Salidas:
  figures/bathymetry/gebco_golfo_isobatas.png   (mapa + isóbatas 10/20/50/100/200 m)
  figures/bathymetry/gebco_tid_origen.png       (qué celdas son sondeo real vs interpolado)
  data/bathymetry/tid_resumen.csv               (% de celdas marinas por tipo de origen)
  data/bathymetry/isobatas_gebco.csv            (vértices de isóbatas, para comparar)
Si existe data/bathymetry/col407_isobatas.csv (columnas: profundidad,linea,lon,lat),
la superpone (p. ej. isóbatas digitalizadas de la carta COL 407 o de la Fig. 20 del paper).
"""

from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import xarray as xr
from matplotlib.colors import BoundaryNorm, ListedColormap

from common import DATA, FIGS, load_config, std_latlon

RAW = DATA / "bathymetry" / "raw"
OUT = DATA / "bathymetry"
FIG = FIGS / "bathymetry"
ISOBATHS = [10, 20, 50, 100, 200]

# Grupos de la grilla TID de GEBCO
TID_GROUPS = {
    "Tierra": [0],
    "Medición directa (sondeos)": list(range(10, 18)),
    "Indirecta/interpolada": list(range(40, 48)),
    "Grilla pre-generada": list(range(70, 73)),
}


def find(var_hint: str) -> xr.DataArray:
    for f in sorted(RAW.rglob("*.nc")):
        ds = std_latlon(xr.open_dataset(f))
        for v in ds.data_vars:
            if var_hint in v.lower():
                return ds[v]
    raise SystemExit(f"No encuentro variable '{var_hint}' en {RAW}")


def main() -> None:
    cfg = load_config()
    g = cfg["golfo"]
    elev = find("elevation").sel(lat=slice(g["lat_s"] - 0.05, g["lat_n"] + 0.05),
                                 lon=slice(g["lon_w"] - 0.05, g["lon_e"] + 0.05))
    depth = (-elev).where(elev < 0)
    FIG.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(10, 6))
    pc = ax.pcolormesh(depth.lon, depth.lat, depth, cmap="Blues", shading="auto",
                       vmin=0, vmax=min(1000, float(depth.max())))
    ax.contourf(elev.lon, elev.lat, (elev >= 0).astype(int), levels=[0.5, 1.5], colors=["#e8e2d6"])
    cs = ax.contour(depth.lon, depth.lat, depth, levels=ISOBATHS, colors="k", linewidths=0.8)
    ax.clabel(cs, fmt="%d m", fontsize=7)
    ref = OUT / "col407_isobatas.csv"
    if ref.exists():
        r = pd.read_csv(ref)
        for (_, _), seg in r.groupby(["profundidad", "linea"]):
            ax.plot(seg.lon, seg.lat, color="tab:red", lw=1)
        ax.plot([], [], color="tab:red", label="referencia (COL 407 / paper)")
        ax.plot([], [], color="k", label="GEBCO_2025")
        ax.legend(loc="lower left", fontsize=8)
    fig.colorbar(pc, ax=ax, label="Profundidad (m)")
    ax.set_title("Golfo de Salamanca — batimetría GEBCO_2025")
    ax.set_aspect("equal")
    fig.tight_layout()
    fig.savefig(FIG / "gebco_golfo_isobatas.png", dpi=170)
    plt.close(fig)

    rows = []
    for lev, segs in zip(cs.levels, cs.allsegs):
        for i, seg in enumerate(segs):
            rows += [(lev, i, x, y) for x, y in seg]
    pd.DataFrame(rows, columns=["profundidad", "linea", "lon", "lat"]).to_csv(
        OUT / "isobatas_gebco.csv", index=False, float_format="%.5f")

    try:
        tid = find("tid").reindex_like(elev, method="nearest")
    except SystemExit:
        print("  (sin grilla TID: se omite el análisis de origen de datos)")
        return
    group = xr.full_like(tid, np.nan, dtype=float)
    for i, codes in enumerate(TID_GROUPS.values()):
        group = group.where(~tid.isin(codes), i)
    sea = elev < 0
    summary = []
    for i, name in enumerate(TID_GROUPS):
        if name == "Tierra":
            continue
        n = int(((group == i) & sea).sum())
        summary.append({"origen": name, "celdas": n, "pct_mar": 100 * n / int(sea.sum())})
    pd.DataFrame(summary).to_csv(OUT / "tid_resumen.csv", index=False, float_format="%.1f")
    print(pd.DataFrame(summary).to_string(index=False))

    cmap = ListedColormap(["#e8e2d6", "#1b7837", "#f1a340", "#998ec3"])
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.pcolormesh(group.lon, group.lat, group, cmap=cmap,
                  norm=BoundaryNorm(np.arange(-0.5, 4.5), 4), shading="auto")
    ax.contour(depth.lon, depth.lat, depth, levels=ISOBATHS, colors="k", linewidths=0.5)
    for i, name in enumerate(TID_GROUPS):
        ax.plot([], [], "s", color=cmap(i), label=name)
    ax.legend(loc="lower left", fontsize=8)
    ax.set_title("GEBCO_2025 TID — origen del dato en cada celda")
    ax.set_aspect("equal")
    fig.tight_layout()
    fig.savefig(FIG / "gebco_tid_origen.png", dpi=170)
    plt.close(fig)
    print(f"Listo → {OUT} y {FIG}")


if __name__ == "__main__":
    main()
