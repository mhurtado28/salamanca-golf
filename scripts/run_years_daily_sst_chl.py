#!/usr/bin/env python3
"""Descarga diarios SST+CHL para un rango de años (reanuda si ya existen)."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "daily_to_monthly_sst_chl.py"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start", type=int, default=2015)
    p.add_argument("--end", type=int, default=2025)
    args = p.parse_args()
    if args.start > args.end:
        raise SystemExit("--start debe ser <= --end")

    for year in range(args.start, args.end + 1):
        print("=" * 60)
        print(f"AÑO {year}")
        print("=" * 60)
        cmd = [sys.executable, "-u", str(SCRIPT), "--year", str(year)]
        r = subprocess.run(cmd, cwd=str(ROOT))
        if r.returncode != 0:
            print(f"AVISO: año {year} terminó con código {r.returncode}; continúa.")
    print("Descarga multi-año finalizada.")


if __name__ == "__main__":
    main()
