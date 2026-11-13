#!/usr/bin/env python3
"""Descarga CHIRPS v3 diario (0.05°, solo continente) recortado a la caja del config.

Lee sólo la ventana necesaria de cada GeoTIFF diario global por HTTP (GDAL /vsicurl/),
así no se baja el mundo entero. Guarda un NetCDF por año en data/rain/chirps/.
Es reanudable por día: guarda tras cada tanda de 15 días y en la siguiente
corrida pide solo los días que faltan. Va despacio a propósito (2 hilos y pausas):
con más velocidad el servidor de CHC empieza a rechazar conexiones (403).

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
import time
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
os.environ.setdefault("GDAL_HTTP_TIMEOUT", "60")
os.environ.setdefault("GDAL_HTTP_CONNECTTIMEOUT", "20")
os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "2")
os.environ.setdefault("GDAL_HTTP_RETRY_DELAY", "15")
os.environ.setdefault("GDAL_HTTP_USERAGENT", "salamanca-golf-research/1.0 (CHIRPS v3 subset)")

CHUNK = 15          # días por tanda
PAUSE_S = 1.0       # pausa entre peticiones por hilo (cortesía con el servidor)
MAX_BAD_CHUNKS = 3  # tandas seguidas sin ningún día → el servidor no responde: parar


def url_for(day: pd.Timestamp, prod: str) -> str:
    return f"{BASE}/{prod}/{day.year}/chirps-v3.0.{prod}.{day:%Y.%m.%d}.tif"


def read_window(day: pd.Timestamp, prod: str, box: dict):
    time.sleep(PAUSE_S)
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
    return xr.DataArray(arr, coords={"lat": np.round(lats, 4), "lon": np.round(lons, 4)},
                        dims=("lat", "lon")).sortby("lat")


def load_existing(out) -> xr.DataArray | None:
    if not out.exists():
        return None
    with xr.open_dataset(out) as ds:
        return ds["precip"].load()


def save(da: xr.DataArray, out, prod: str) -> None:
    tmp = out.with_suffix(".part")
    da.to_dataset(name="precip").assign_attrs(
        source=f"CHIRPS v3.0 daily final/{prod}", url_base=BASE
    ).to_netcdf(tmp, encoding={"precip": {"zlib": True, "complevel": 4}})
    tmp.replace(out)


def download_year(year: int, prod: str, box: dict, workers: int) -> bool:
    """Descarga los días que faltan del año. Devuelve False si el servidor dejó de responder."""
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"chirps_v3_{prod}_{year}.nc"
    days = pd.date_range(f"{year}-01-01", f"{year}-12-31", freq="D")
    days = days[days <= pd.Timestamp.today().normalize() - pd.Timedelta(days=45)]
    cube = load_existing(out)
    if cube is not None:
        have = cube.notnull().any(("lat", "lon")).to_series()
        have_days = set(have[have].index)
        cube = cube.reindex(time=days)
    else:
        have_days = set()
    todo = [d for d in days if d not in have_days]
    if not todo:
        print(f"  {year}: completo ({len(days)} días)")
        return True
    print(f"  {year}: faltan {len(todo)} de {len(days)} días…", flush=True)

    failed, bad_chunks, got = {}, 0, 0
    for i in range(0, len(todo), CHUNK):
        chunk = todo[i:i + CHUNK]
        res = {}
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(read_window, d, prod, box): d for d in chunk}
            for fu in as_completed(futs):
                d = futs[fu]
                try:
                    res[d] = fu.result()
                except Exception as exc:  # red, 403, día aún no publicado…
                    failed[d] = str(exc)[:160]
        if res:
            bad_chunks = 0
            got += len(res)
            if cube is None:
                g = next(iter(res.values()))
                cube = xr.DataArray(np.full((len(days), g.lat.size, g.lon.size), np.nan, "float32"),
                                    coords={"time": days, "lat": g.lat, "lon": g.lon},
                                    dims=("time", "lat", "lon"), attrs={"units": "mm/day"})
            for d, a in res.items():
                cube.loc[{"time": d}] = a.reindex_like(cube.isel(time=0), method="nearest",
                                                       tolerance=0.01).values
                failed.pop(d, None)
            save(cube, out, prod)  # guarda el avance tras cada tanda
        else:
            bad_chunks += 1
            print(f"    tanda sin datos ({next(iter(failed.values()), '')[:90]})", flush=True)
            if bad_chunks >= MAX_BAD_CHUNKS:
                break
        print(f"    {year}: {got}/{len(todo)} días nuevos", flush=True)

    fail_csv = OUT / f"fallidos_{prod}_{year}.csv"
    if failed:
        pd.DataFrame(sorted(failed.items()), columns=["fecha", "error"]).to_csv(fail_csv, index=False)
    elif fail_csv.exists():
        fail_csv.unlink()
    print(f"  {year}: +{got} días, {len(failed)} pendientes", flush=True)
    return bad_chunks < MAX_BAD_CHUNKS


def main() -> None:
    cfg = load_config()
    ch = cfg["chirps"]
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", type=int, default=cfg["periodo"]["inicio"])
    ap.add_argument("--end", type=int, default=cfg["periodo"]["fin"])
    ap.add_argument("--product", default=ch.get("producto", "sat"), choices=["sat", "rnl"])
    ap.add_argument("--workers", type=int, default=2)
    a = ap.parse_args()
    box = {k: ch[k] for k in ("lat_s", "lat_n", "lon_w", "lon_e")}
    for y in range(a.start, a.end + 1):
        if not download_year(y, a.product, box, a.workers):
            raise SystemExit("El servidor de CHIRPS no responde. Lo guardado se conserva; "
                             "vuelve a correr más tarde y retoma donde quedó.")


if __name__ == "__main__":
    main()
