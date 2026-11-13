#!/usr/bin/env python3
"""Ordena las descargas de DHIME (IDEAM) en una tabla diaria única.

DHIME no tiene API abierta: descarga a mano desde
http://dhime.ideam.gov.co/atencionciudadano/ los CSV de:
  - Precipitación diaria (etiqueta PTPM_CON) de estaciones cercanas:
    aeropuertos Simón Bolívar (Santa Marta) y Ernesto Cortissoz (Barranquilla),
    y las que encuentres en la vertiente de la Sierra / Ciénaga.
  - Caudal medio diario (etiqueta Q_MEDIA_D) del Magdalena en Calamar,
    y de Toribio / Córdoba si existen estaciones.
Ponlos (sin renombrar) en data/ideam/raw/ y corre este script. Puedes subirlos
al Codespace arrastrándolos al explorador de archivos de VS Code.

Salidas:
  data/ideam/ideam_daily.csv      (fecha, estación, etiqueta, valor, lat, lon)
  data/ideam/ideam_estaciones.csv (inventario y cobertura de cada serie)
"""

from __future__ import annotations

import pandas as pd

from common import DATA

RAW = DATA / "ideam" / "raw"
OUT = DATA / "ideam"

ALIASES = {
    "estacion_codigo": ["codigoestacion", "codigo", "codestacion", "station_code"],
    "estacion_nombre": ["nombreestacion", "nombre", "estacion"],
    "lat": ["latitud", "lat"],
    "lon": ["longitud", "lon"],
    "etiqueta": ["etiqueta", "idparametro", "parametro", "descripcionserie"],
    "fecha": ["fecha", "date", "fechahora"],
    "valor": ["valor", "value"],
}


def _norm(c: str) -> str:
    return "".join(ch for ch in c.lower() if ch.isalnum())


def read_any(path) -> pd.DataFrame:
    for enc in ("utf-8-sig", "latin-1"):
        for sep in (",", ";", "|", "\t"):
            try:
                df = pd.read_csv(path, sep=sep, encoding=enc, dtype=str)
            except Exception:
                continue
            if df.shape[1] >= 3:
                return df
    raise ValueError(f"No pude leer {path.name}")


def standardize(df: pd.DataFrame, fname: str) -> pd.DataFrame:
    cols = {_norm(c): c for c in df.columns}
    out = {}
    for std, opts in ALIASES.items():
        for o in opts:
            if o in cols:
                out[std] = df[cols[o]]
                break
    if "fecha" not in out or "valor" not in out:
        raise ValueError(f"{fname}: faltan columnas Fecha/Valor (columnas: {list(df.columns)})")
    s = pd.DataFrame(out)
    if "etiqueta" not in s:
        s["etiqueta"] = "DESCONOCIDA"
    if "estacion_nombre" not in s:
        s["estacion_nombre"] = fname
    if "estacion_codigo" not in s:
        s["estacion_codigo"] = s["estacion_nombre"]
    s["fecha"] = pd.to_datetime(s["fecha"], errors="coerce").dt.normalize()
    s["valor"] = pd.to_numeric(s["valor"].str.replace(",", ".", regex=False), errors="coerce")
    for c in ("lat", "lon"):
        if c in s:
            s[c] = pd.to_numeric(s[c].str.replace(",", ".", regex=False), errors="coerce")
        else:
            s[c] = float("nan")
    s["etiqueta"] = s["etiqueta"].str.strip().str.upper()
    s["archivo"] = fname
    return s.dropna(subset=["fecha", "valor"])


def main() -> None:
    files = sorted([*RAW.glob("*.csv"), *RAW.glob("*.CSV"), *RAW.glob("*.txt")])
    if not files:
        raise SystemExit(f"Pon los CSV de DHIME en {RAW}")
    parts = []
    for f in files:
        try:
            parts.append(standardize(read_any(f), f.name))
            print(f"  leído {f.name}")
        except ValueError as exc:
            print(f"  OMITIDO {exc}")
    df = pd.concat(parts, ignore_index=True)
    # Si hay varias lecturas el mismo día (series subdiarias), resumir:
    # precipitación → suma, caudal/nivel → media.
    is_rain = df["etiqueta"].str.startswith("PTPM")
    keys = ["estacion_codigo", "estacion_nombre", "etiqueta", "fecha"]
    rain = df[is_rain].groupby(keys, as_index=False).agg(valor=("valor", "sum"), lat=("lat", "first"), lon=("lon", "first"))
    other = df[~is_rain].groupby(keys, as_index=False).agg(valor=("valor", "mean"), lat=("lat", "first"), lon=("lon", "first"))
    tidy = pd.concat([rain, other]).sort_values(keys)
    OUT.mkdir(parents=True, exist_ok=True)
    tidy.to_csv(OUT / "ideam_daily.csv", index=False, float_format="%.3f")

    inv = (tidy.groupby(["estacion_codigo", "estacion_nombre", "etiqueta"])
           .agg(lat=("lat", "first"), lon=("lon", "first"),
                inicio=("fecha", "min"), fin=("fecha", "max"), n_dias=("valor", "size"))
           .reset_index())
    span = (inv["fin"] - inv["inicio"]).dt.days + 1
    inv["completitud_pct"] = (100 * inv["n_dias"] / span).round(1)
    inv.to_csv(OUT / "ideam_estaciones.csv", index=False)
    print(inv.to_string(index=False))
    print(f"Listo → {OUT / 'ideam_daily.csv'}")


if __name__ == "__main__":
    main()
