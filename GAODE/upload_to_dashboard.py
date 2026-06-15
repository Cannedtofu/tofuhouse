# -*- coding: utf-8 -*-
"""
One-time upload script — reads GaoDe.xlsx and pushes to the news_agent dashboard.
Does NOT scrape, does NOT modify the Excel file. Safe to run multiple times.

Usage:
    python upload_to_dashboard.py
"""

import os
import openpyxl
import requests
from datetime import datetime

os.chdir(os.path.dirname(os.path.abspath(__file__)))

API_BASE = "http://47.239.66.248"
API_KEY  = "b1445fd803c77c5bff4b0eeced29f5b84c752d0bbd6642f89bd44c732a1646fa"
EXCEL_PATH = "GaoDe.xlsx"

# ---------------------------------------------------------------------------
# Parse Excel — group 路网高延时运行时间占比 by year
# ---------------------------------------------------------------------------
wb = openpyxl.load_workbook(EXCEL_PATH, data_only=True)
ws = wb.active

annual = {}
for row in ws.iter_rows(min_row=2, values_only=True):
    if not row[0]:
        continue
    d   = datetime.fromisoformat(str(row[0])[:19])
    yr  = str(d.year)
    val = float(str(row[2]).rstrip('%'))
    annual.setdefault(yr, []).append({"x": d.strftime("%m-%d"), "y": val})

print(f"Parsed {sum(len(v) for v in annual.values())} data points across years: {sorted(annual.keys())}")

panels = [{
    "type":      "line",
    "title":     "路网高延时运行时间占比 — 年度对比",
    "x_type":    "day_of_year",
    "span_gaps": True,
    "datasets":  [{"label": yr, "data": pts} for yr, pts in sorted(annual.items())],
}]

# ---------------------------------------------------------------------------
# Push to dashboard
# ---------------------------------------------------------------------------
sess = requests.Session()
sess.verify = False
sess.trust_env = False

print("Pushing report...")
resp = sess.post(
    f"{API_BASE}/api/report",
    headers={"X-API-Key": API_KEY},
    json={
        "script":                   "gaode",
        "status":                   "ok",
        "expected_interval_hours":  24,
        "panels":                   panels,
    },
    timeout=30,
)
resp.raise_for_status()
print(f"  Report: {resp.status_code} OK")

print("Uploading Excel file...")
with open(EXCEL_PATH, "rb") as f:
    resp2 = sess.post(
        f"{API_BASE}/api/report/gaode/excel",
        headers={"X-API-Key": API_KEY},
        files={"file": ("GaoDe.xlsx", f)},
        timeout=60,
    )
resp2.raise_for_status()
print(f"  Excel:  {resp2.status_code} OK")

print("Done — refresh the dashboard to see the gaode panel.")
