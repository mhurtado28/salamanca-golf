#!/usr/bin/env python3
"""Compatibilidad: delega en daily_to_monthly_sst_chl.py --year 2025."""
import runpy
import sys

sys.argv = [sys.argv[0], "--year", "2025", *sys.argv[1:]]
runpy.run_path(str(__file__).replace("daily_to_monthly_sst_chl_2025.py", "daily_to_monthly_sst_chl.py"), run_name="__main__")
