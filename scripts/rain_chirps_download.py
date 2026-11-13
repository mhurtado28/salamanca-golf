#!/usr/bin/env python3
"""Descarga CHIRPS v3 diario (0.05°, solo continente) recortado a la caja del config.

Lee sólo la ventana necesaria de cada GeoTIFF diario global por HTTP (GDAL /vsicurl/),
así no se baja el mundo entero. Guarda un NetCDF por año en data/rain/chirps/.
Es reanudable: si un año ya está completo lo salta.

Producto diario (config chirps.producto):
  sat → pentadas CHIRPS repartidas en días con IMERG (satélite)
  rnl → pentadas CHIRPS repartidas en días con ERA5

Ejemplos:
  python scripts/rain_chirps_download.py --start 2015 --end 2025
  python scripts/rain_chirps_download.py --start 1995 --end 1998
"""

from __future__ import annotations

import argparse
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
import rasterio
from rasterio.windows import from_bounds
import xarray as xr

from common import DATA, load_config

OUT = DATA / "rain" / "chirps"
BASE = "https://data.chc.ucsb.edu/products/CHIRPS/v3.0/daily/final"

os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif")
os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "5")
os.environ.setdefault("GDAL_HTTP_RETRY_DELAY", "3")


def url_for(day: pd.Timestamp, prod: str) -> str:
    return f"{BASE}/{prod}/{day.year}/chirps-v3.0.{prod}.{day:%Y.%m.%d}.tif"


def read_window(day: pd.Timestamp, prod: str, box: dict):
    with rasterio.open("/vsicurl/" + url_for(day, prod)) as src:
        win = from_bounds(box["lon_w"], box["lat_s"], box["lon_e"], box["lat_n"], src.transform)
        win = win.round_offsets().round_lengths()
        arr = src.read(1, window=win).astype("float32")
        tr = src.window_transform(win)
        nodata = src.nodata
    if nodata is not None:
        arr[arr == nodata] = np.nan
    arr[arr < 0] = np.nan
    ny, nx = arr.shape
    lons = tr.c + tr.a * (np.arange(nx) + 0.5)
    lats = tr.f + tr.e * (np.arange(ny) + 0.5)
    return arr, lats, lons


def download_year(year: int, prod: str, box: dict, workers: int) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"chirps_v3_{prod}_{year}.nc"
    days = pd.date_range(f"{year}-01-01", f"{year}-12-31", freq="D")
    days = days[days <= pd.Timestamp.today().normalize()]
    if out.exists():
        with xr.open_dataset(out) as ds:
            if ds.sizes["time"] == len(days):
                print(f"  {year}: completo")
                return
    print(f"  {year}: {len(days)} días…")
    results, failed = {}, []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(read_window, d, prod, box): d for d in days}
        for fu in as_completed(futs):
            d = futs[fu]
            try:
                results[d] = fu.result()
            except Exception as exc:  # día faltante en el servidor, red, etc.
                failed.append((d, str(exc)[:120]))
    if not results:
        print(f"  {year}: sin datos ({failed[:1]})")
        return
    first = next(iter(results.values()))
    lats, lons = first[1], first[2]
    cube = np.full((len(days), len(lats), len(lons)), np.nan, dtype="float32")
    for i, d in enumerate(days):
        if d in results:
            cube[i] = results[d][0]
    ds = xr.Dataset(
        {"precip": (("time", "lat", "lon"), cube, {"units": "mm/day"})},
        coords={"time": days, "lat": lats, "lon": lons},
        attrs={"source": f"CHIRPS v3.0 daily final/{prod}", "url_base": BASE},
    ).sortby("lat")
    ds.to_netcdf(out, encoding={"precip": {"zlib": True, "complevel": 4}})
    print(f"  {year}: guardado ({len(results)} días, {len(failed)} fallidos)")
    if failed:
        pd.DataFrame(failed, columns=["fecha", "error"]).to_csv(
            OUT / f"fallidos_{prod}_{year}.csv", index=False)


def main() -> None:
    cfg = load_config()
    ch = cfg["chirps"]
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", type=int, default=cfg["periodo"]["inicio"])
    ap.add_argument("--end", type=int, default=cfg["periodo"]["fin"])
    ap.add_argument("--product", default=ch.get("producto", "sat"), choices=["sat", "rnl"])
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    box = {k: ch[k] for k in ("lat_s", "lat_n", "lon_w", "lon_e")}
    for y in range(a.start, a.end + 1):
        download_year(y, a.product, box, a.workers)


if __name__ == "__main__":
    main()
