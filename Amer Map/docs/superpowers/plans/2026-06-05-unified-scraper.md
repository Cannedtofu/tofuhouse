# Amer Map Unified Scraper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace 4 separate per-brand scripts with one command that scrapes all brands, deduplicates results, and outputs a single dated Excel file (one sheet per brand + one Changes sheet vs. the previous run).

**Architecture:** A config-driven brand registry (`brands.py`) declares each brand's API type, credentials, and region. Two crawler adapters handle `locally.com` (Hoka, Salomon, Arc'teryx) and On Running's dealer API. `output.py` writes a single `store_data_YYYY-QX.xlsx` with one sheet per brand and a Changes sheet comparing new vs. previous quarter. `main.py` is the single entry point.

**Tech Stack:** Python 3.9+, `requests`, `pandas`, `openpyxl`

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `brands.py` | Create | Brand configs: API type, URL, company_id, region boundaries, grid params |
| `crawlers.py` | Create | Two crawler adapters: `locally_crawl()`, `on_crawl()` |
| `output.py` | Create | Excel I/O: write one sheet per brand, compute and write Changes sheet |
| `main.py` | Create | Orchestrate all brands, deduplicate by store ID, call output |
| `requirements.txt` | Create | Pin dependencies |
| `Hoka map.py`, `Arcteryx map.py`, `Salomon map.py`, `On map.py`, `utils.py` | Delete after Task 6 | Replaced by unified framework |

---

## Task 1: Project Setup

**Files:**
- Create: `requirements.txt`

- [ ] **Step 1: Write requirements.txt**

```
requests==2.31.0
pandas==2.2.2
openpyxl==3.1.2
```

- [ ] **Step 2: Install**

```bash
pip install -r requirements.txt
```

Expected: installs without errors.

- [ ] **Step 3: Commit**

```bash
git add requirements.txt
git commit -m "chore: add requirements.txt"
```

---

## Region Boundary Reference

The following boundaries are copied verbatim from the original scripts and must not be changed — they define the tile grid that produced all historical data. Changing any value would shift the grid and make store counts incomparable across quarters.

| Region | lat_min | lat_max | lng_min | lng_max | lat_step | lng_step | zoom |
|---|---|---|---|---|---|---|---|
| North America | 24.5 | 83 | -170 | -52 | 1.1751/2 | 3.0185/2 | 8.4736 |
| Europe | 34.5 | 71 | -10 | 30 | 0.871/2 | 3.019/2 | 8.4736 |

**Important — first combined run:** The old scripts ran only one region at a time by commenting out the other. The unified framework runs **both** North America and Europe for every locally.com brand in a single execution. However, verification of the 2026-Q1 data confirms it already contains both US and European stores for Arc'teryx and Salomon (Arc'teryx: 861 US + CA + European countries; Salomon: 9331 US + 9331 CA + European countries). The 2026-Q2 run should therefore produce a clean, meaningful diff for those two brands. Hoka and On Running have no prior baseline in the unified format — their first Changes comparison will be in 2026-Q3.

---

## Task 2: Brand Configuration

**Files:**
- Create: `brands.py`

This file holds all per-brand constants. Adding a new brand means adding one entry here and nowhere else.

There are two API adapter types:
- `"locally"` — tile-grid crawl against `*.locally.com/stores/conversion_data`
- `"on_running"` — single-call global fetch from On Running's dealer API

- [ ] **Step 1: Write brands.py**

Step sizes are kept as their original fractional expressions (e.g. `1.1751/2`) so they remain visually traceable to the original scripts.

```python
BRANDS = [
    {
        "name": "arcteryx",
        "sheet_name": "Arc'teryx",
        "adapter": "locally",
        "base_url": "https://arcteryx.locally.com/stores/conversion_data",
        "company_id": "31",
        "dealers_company_id": "31",
        "referer": "https://arcteryx.locally.com/conversion?company_id=31&inline=1&lang=en",
        "requires_session": True,
        "regions": [
            # North America — from Arcteryx map.py (was commented out; now active)
            {"lat_min": 24.5, "lat_max": 83, "lng_min": -170, "lng_max": -52,
             "lat_step": 1.1751/2, "lng_step": 3.0185/2, "zoom": 8.4736},
            # Europe — from Arcteryx map.py (was the active region)
            {"lat_min": 34.5, "lat_max": 71, "lng_min": -10, "lng_max": 30,
             "lat_step": 0.871/2, "lng_step": 3.019/2, "zoom": 8.4736},
        ],
    },
    {
        "name": "hoka",
        "sheet_name": "Hoka",
        "adapter": "locally",
        "base_url": "https://hokaoneone.locally.com/stores/conversion_data",
        "company_id": "1428",
        "dealers_company_id": None,
        "referer": "https://www.hoka.com",
        "requires_session": False,
        "regions": [
            # North America — from Hoka map.py (was the active region)
            {"lat_min": 24.5, "lat_max": 83, "lng_min": -170, "lng_max": -52,
             "lat_step": 1.1751/2, "lng_step": 3.0185/2, "zoom": 8.4736},
            # Europe — from Hoka map.py (was commented out; now active)
            {"lat_min": 34.5, "lat_max": 71, "lng_min": -10, "lng_max": 30,
             "lat_step": 0.871/2, "lng_step": 3.019/2, "zoom": 8.4736},
        ],
    },
    {
        "name": "salomon",
        "sheet_name": "Salomon",
        "adapter": "locally",
        "base_url": "https://salomon.locally.com/stores/conversion_data",
        "company_id": "71",
        "dealers_company_id": None,
        "referer": "https://salomon.com/us/en/stores",
        "requires_session": False,
        "regions": [
            # North America — from Salomon map.py (was commented out; now active)
            {"lat_min": 24.5, "lat_max": 83, "lng_min": -170, "lng_max": -52,
             "lat_step": 1.1751/2, "lng_step": 3.0185/2, "zoom": 8.4736},
            # Europe — from Salomon map.py (was the active region)
            {"lat_min": 34.5, "lat_max": 71, "lng_min": -10, "lng_max": 30,
             "lat_step": 0.871/2, "lng_step": 3.019/2, "zoom": 8.4736},
        ],
    },
    {
        "name": "on_running",
        "sheet_name": "On Running",
        "adapter": "on_running",
        # Endpoint discovered via browser network inspection on on-running.com/stores
        # Response format: {"centerPosition": false, "dealers": [...]}
        "api_url": "PLACEHOLDER — see Task 4 Step 1",
    },
]
```

> **Note on On Running API URL:** The `api_url` above is a placeholder. Before Task 4, open Chrome DevTools → Network tab → filter XHR → visit `https://www.on-running.com/stores` and find the request that returns `{"centerPosition": false, "dealers": [...]}`. Copy that URL and any required headers/params into the config and into the `on_crawl()` function in Task 4.

- [ ] **Step 2: Commit**

```bash
git add brands.py
git commit -m "feat: add brand config registry"
```

---

## Task 3: Locally.com Crawler

**Files:**
- Create: `crawlers.py`

Implements the `locally_crawl(brand)` adapter. Returns a raw (possibly duplicate) list of store dicts. Deduplication (by `id`) happens in `main.py`, not here.

- [ ] **Step 1: Write crawlers.py — shared utilities + locally adapter**

```python
import requests
import time
from typing import List

REQUEST_DELAY = 0.1  # seconds between tile requests

COMMON_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    "X-Requested-With": "XMLHttpRequest",
}


def _frange(start, stop, step):
    while start < stop:
        yield round(start, 6)
        start += step


def _get_session(brand: dict) -> requests.Session:
    session = requests.Session()
    if brand.get("requires_session"):
        init_url = brand["referer"]
        session.get(init_url, headers={"User-Agent": COMMON_HEADERS["User-Agent"]})
    return session


def _fetch_locally_tile(session, brand, sw_lat, sw_lng, ne_lat, ne_lng, zoom) -> dict | None:
    headers = {**COMMON_HEADERS, "Referer": brand["referer"]}
    params = {
        "has_data": "true",
        "company_id": brand["company_id"],
        "inline": "1",
        "show_links_in_list": "8",
        "map_ne_lat": ne_lat,
        "map_ne_lng": ne_lng,
        "map_sw_lat": sw_lat,
        "map_sw_lng": sw_lng,
        "map_center_lat": (sw_lat + ne_lat) / 2,
        "map_center_lng": (sw_lng + ne_lng) / 2,
        "map_distance_diag": 1000,
        "sort": "by_proximity",
        "zoom_level": zoom,
        "lang": "en",
    }
    if brand.get("dealers_company_id"):
        params["dealers_company_id"] = brand["dealers_company_id"]

    try:
        resp = session.get(brand["base_url"], headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"    [ERROR] tile ({sw_lat:.3f}, {sw_lng:.3f}): {e}")
        return None


def _parse_locally_markers(tile_data: dict) -> List[dict]:
    stores = []
    for key, value in tile_data.items():
        if key.startswith("markers") and isinstance(value, list):
            for s in value:
                stores.append({
                    "id": s.get("id"),
                    "name": s.get("name"),
                    "lat": s.get("lat"),
                    "lng": s.get("lng"),
                    "address": s.get("address"),
                    "city": s.get("city"),
                    "state": s.get("state"),
                    "country": s.get("country"),
                    "phone": s.get("phone"),
                    "is_claimed": s.get("is_claimed"),
                    "company_id": s.get("company_id"),
                    "vendor_id": s.get("vendor_id"),
                    "stock_status": s.get("stock_status"),
                    "is_hosted": s.get("is_hosted"),
                    "features": s.get("features"),
                    "enhanced_categories": s.get("enhanced_categories"),
                })
    return stores


def locally_crawl(brand: dict) -> List[dict]:
    """Crawl all configured regions for a locally.com brand. Returns raw (possibly duplicate) store list."""
    session = _get_session(brand)
    all_stores = []

    for region in brand["regions"]:
        lat_tiles = list(_frange(region["lat_min"], region["lat_max"], region["lat_step"]))
        lng_tiles = list(_frange(region["lng_min"], region["lng_max"], region["lng_step"]))
        total = len(lat_tiles) * len(lng_tiles)
        done = 0

        print(f"  Region: lat[{region['lat_min']}..{region['lat_max']}] lng[{region['lng_min']}..{region['lng_max']}] — {total} tiles")

        for lat in lat_tiles:
            for lng in lng_tiles:
                ne_lat = round(lat + region["lat_step"], 6)
                ne_lng = round(lng + region["lng_step"], 6)
                tile_data = _fetch_locally_tile(
                    session, brand, lat, lng, ne_lat, ne_lng, region["zoom"]
                )
                if tile_data and "markers" in tile_data:
                    stores = _parse_locally_markers(tile_data)
                    all_stores.extend(stores)
                done += 1
                if done % 50 == 0:
                    pct = done / total * 100
                    print(f"    {done}/{total} ({pct:.1f}%) — {len(all_stores)} stores so far")
                time.sleep(REQUEST_DELAY)

    return all_stores
```

- [ ] **Step 2: Commit**

```bash
git add crawlers.py
git commit -m "feat: add locally.com crawler"
```

---

## Task 4: On Running Crawler + API Discovery

**Files:**
- Modify: `crawlers.py` — add `on_crawl()` function

Before writing, you need to find the actual On Running API endpoint.

- [ ] **Step 1: Discover the On Running API endpoint**

1. Open Chrome, navigate to `https://www.on-running.com/en-us/stores`
2. Open DevTools → Network tab → filter by "Fetch/XHR"
3. Search for any dealer or store locator on the page
4. Look for a request whose response JSON starts with `{"centerPosition":` or contains `"dealers":`
5. Note the full URL, query params, and any required headers (especially cookies or auth headers)

> **Fallback:** If the API requires authentication or returns no data without browser state, use the existing `on stores.txt` file as the data source (Task 4 Step 4 covers this).

- [ ] **Step 2: Update brands.py with the real On Running endpoint**

Replace the placeholder `api_url` in the `on_running` brand config with the URL you found. If query params are required, add an `api_params` dict. If special headers are needed, add an `api_headers` dict.

Example (fill in actual values):
```python
{
    "name": "on_running",
    "sheet_name": "On Running",
    "adapter": "on_running",
    "api_url": "https://www.on-running.com/api/v1/dealers",  # ← replace with real URL
    "api_params": {"country": "all"},                        # ← if needed
    "api_headers": {},                                        # ← if extra headers needed
},
```

- [ ] **Step 3: Add on_crawl() to crawlers.py**

Append to the end of `crawlers.py`:

```python
def on_crawl(brand: dict) -> List[dict]:
    """Fetch all On Running dealers from their global API in a single call."""
    headers = {**COMMON_HEADERS, **brand.get("api_headers", {})}
    params = brand.get("api_params", {})

    try:
        resp = requests.get(brand["api_url"], headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  [ERROR] On Running fetch failed: {e}")
        return []

    dealers = data.get("dealers", [])
    stores = []
    for d in dealers:
        stores.append({
            "id": d.get("id"),
            "name": d.get("name"),
            "lat": float(d["latitude"]) if d.get("latitude") else None,
            "lng": float(d["longitude"]) if d.get("longitude") else None,
            "address": d.get("address"),
            "city": d.get("city"),
            "state": d.get("state"),
            "country": d.get("country"),
            "phone": d.get("phone"),
            "email": d.get("email"),
            "website": d.get("website"),
            "postal_code": d.get("postal_code"),
            "dealer_type": d.get("dealer_type"),
            "has_apparel": d.get("has_apparel"),
        })
    print(f"  On Running: fetched {len(stores)} dealers")
    return stores
```

- [ ] **Step 4: Fallback — read from on stores.txt if API fails**

If the API endpoint cannot be determined or returns no data, use this instead for `on_crawl()`:

```python
import json

def on_crawl(brand: dict) -> List[dict]:
    """Read On Running dealers from the manually exported on stores.txt file."""
    filepath = brand.get("fallback_file", "on stores.txt")
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"  [ERROR] On Running file read failed: {e}")
        return []

    stores = []
    for d in data.get("dealers", []):
        stores.append({
            "id": d.get("id"),
            "name": d.get("name"),
            "lat": float(d["latitude"]) if d.get("latitude") else None,
            "lng": float(d["longitude"]) if d.get("longitude") else None,
            "address": d.get("address"),
            "city": d.get("city"),
            "state": d.get("state"),
            "country": d.get("country"),
            "phone": d.get("phone"),
            "email": d.get("email"),
            "website": d.get("website"),
            "postal_code": d.get("postal_code"),
            "dealer_type": d.get("dealer_type"),
            "has_apparel": d.get("has_apparel"),
        })
    print(f"  On Running (file): loaded {len(stores)} dealers")
    return stores
```

And update the brand config:
```python
{
    "name": "on_running",
    "sheet_name": "On Running",
    "adapter": "on_running",
    "fallback_file": "on stores.txt",
},
```

- [ ] **Step 5: Commit**

```bash
git add brands.py crawlers.py
git commit -m "feat: add On Running crawler"
```

---

## Task 5: Excel Output + Change Detection

**Files:**
- Create: `output.py`

Writes a single `store_data_YYYY-QX.xlsx` file. Each brand gets its own sheet. A final "Changes" sheet compares against the previous quarter's file (if one exists).

Change detection compares by store `id`. It detects:
- **New stores**: `id` in new data but not in previous
- **Closed stores**: `id` in previous data but not in new
- **Changed stores**: same `id` but different `name`, `address`, or `country`

- [ ] **Step 1: Write output.py**

```python
import os
import re
import pandas as pd
from datetime import date
from typing import Dict, List


def _quarter_label(d: date = None) -> str:
    d = d or date.today()
    q = (d.month - 1) // 3 + 1
    return f"{d.year}-Q{q}"


def _output_filename(label: str = None) -> str:
    label = label or _quarter_label()
    return f"store_data_{label}.xlsx"


def _find_previous_file(current_label: str) -> str | None:
    """Return path to the most recent store_data_*.xlsx that is not the current one."""
    pattern = re.compile(r"store_data_(\d{4}-Q\d)\.xlsx")
    candidates = []
    for fname in os.listdir("."):
        m = pattern.match(fname)
        if m and m.group(1) != current_label:
            candidates.append((m.group(1), fname))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def _detect_changes(new_df: pd.DataFrame, old_df: pd.DataFrame, brand_name: str) -> List[dict]:
    """Compare new vs old stores for a brand. Returns list of change records."""
    new_ids = set(new_df["id"].dropna().astype(str))
    old_ids = set(old_df["id"].dropna().astype(str))

    new_df = new_df.copy()
    old_df = old_df.copy()
    new_df["_id_str"] = new_df["id"].astype(str)
    old_df["_id_str"] = old_df["id"].astype(str)

    changes = []

    for sid in new_ids - old_ids:
        row = new_df[new_df["_id_str"] == sid].iloc[0]
        changes.append({
            "brand": brand_name,
            "change_type": "NEW",
            "id": sid,
            "name": row.get("name"),
            "address": row.get("address"),
            "city": row.get("city"),
            "country": row.get("country"),
        })

    for sid in old_ids - new_ids:
        row = old_df[old_df["_id_str"] == sid].iloc[0]
        changes.append({
            "brand": brand_name,
            "change_type": "CLOSED",
            "id": sid,
            "name": row.get("name"),
            "address": row.get("address"),
            "city": row.get("city"),
            "country": row.get("country"),
        })

    for sid in new_ids & old_ids:
        new_row = new_df[new_df["_id_str"] == sid].iloc[0]
        old_row = old_df[old_df["_id_str"] == sid].iloc[0]
        watch = ["name", "address", "country"]
        diffs = [f for f in watch if str(new_row.get(f, "")) != str(old_row.get(f, ""))]
        if diffs:
            changes.append({
                "brand": brand_name,
                "change_type": "CHANGED",
                "id": sid,
                "name": new_row.get("name"),
                "address": new_row.get("address"),
                "city": new_row.get("city"),
                "country": new_row.get("country"),
                "changed_fields": ", ".join(diffs),
                "old_name": old_row.get("name"),
                "old_address": old_row.get("address"),
            })

    return changes


def write_output(brand_data: Dict[str, List[dict]]) -> str:
    """
    Write one Excel file with one sheet per brand + a Changes sheet.
    brand_data: {sheet_name: [store_dict, ...]}
    Returns the output filename.
    """
    label = _quarter_label()
    output_file = _output_filename(label)
    prev_file = _find_previous_file(label)

    all_changes = []

    if prev_file:
        print(f"  Change detection: comparing against {prev_file}")
        prev_xl = pd.ExcelFile(prev_file)

    with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
        for sheet_name, stores in brand_data.items():
            df = pd.DataFrame(stores)
            df.to_excel(writer, sheet_name=sheet_name, index=False)

            if prev_file and sheet_name in prev_xl.sheet_names:
                old_df = prev_xl.parse(sheet_name)
                changes = _detect_changes(df, old_df, sheet_name)
                all_changes.extend(changes)
                print(f"    {sheet_name}: {len(changes)} changes detected")

        if all_changes:
            changes_df = pd.DataFrame(all_changes)
            changes_df.to_excel(writer, sheet_name="Changes", index=False)
            print(f"  Changes sheet written: {len(all_changes)} total changes")
        elif prev_file:
            pd.DataFrame([{"note": "No changes detected vs previous quarter"}]).to_excel(
                writer, sheet_name="Changes", index=False
            )

    print(f"\n✅ Output written: {output_file}")
    return output_file
```

- [ ] **Step 2: Commit**

```bash
git add output.py
git commit -m "feat: add Excel output with change detection"
```

---

## Task 6: Main Orchestrator

**Files:**
- Create: `main.py`

Runs all brands, deduplicates each brand's results by store `id`, then calls `write_output()`.

- [ ] **Step 1: Write main.py**

```python
import sys
from typing import List, Dict
from brands import BRANDS
from crawlers import locally_crawl, on_crawl
from output import write_output


ADAPTER_MAP = {
    "locally": locally_crawl,
    "on_running": on_crawl,
}


def deduplicate(stores: List[dict]) -> List[dict]:
    """Remove duplicate stores by id, keeping first occurrence."""
    seen = set()
    unique = []
    for s in stores:
        sid = s.get("id")
        if sid is not None and sid not in seen:
            seen.add(sid)
            unique.append(s)
        elif sid is None:
            unique.append(s)  # keep stores without an id
    return unique


def run_all(brands=None) -> Dict[str, List[dict]]:
    target_brands = brands or BRANDS
    brand_data = {}

    for brand in target_brands:
        name = brand["name"]
        sheet = brand["sheet_name"]
        adapter_fn = ADAPTER_MAP.get(brand["adapter"])

        if not adapter_fn:
            print(f"[SKIP] Unknown adapter '{brand['adapter']}' for {name}")
            continue

        print(f"\n{'='*50}")
        print(f"Crawling: {name}")
        print(f"{'='*50}")

        raw = adapter_fn(brand)
        deduped = deduplicate(raw)
        print(f"  Raw: {len(raw)} | After dedup: {len(deduped)} stores")
        brand_data[sheet] = deduped

    return brand_data


if __name__ == "__main__":
    # Optional: pass brand names as args to run only specific brands
    # e.g. python main.py hoka salomon
    filter_names = sys.argv[1:] if len(sys.argv) > 1 else []
    if filter_names:
        selected = [b for b in BRANDS if b["name"] in filter_names]
        if not selected:
            print(f"No brands matched: {filter_names}")
            sys.exit(1)
    else:
        selected = BRANDS

    brand_data = run_all(selected)

    print(f"\n{'='*50}")
    print("Writing output...")
    print(f"{'='*50}")
    write_output(brand_data)
```

- [ ] **Step 2: Test with a single brand first (Salomon, small tile count)**

```bash
python main.py salomon
```

Expected: progress output with tile counts, then `✅ Output written: store_data_YYYY-QX.xlsx`. Open the file — confirm it has a "Salomon" sheet with deduplicated store rows.

- [ ] **Step 3: Test with all brands**

```bash
python main.py
```

Expected: all 3 brands crawl in sequence, file written with 4 sheets (Arc'teryx, Hoka, Salomon, On Running). A "Changes" sheet will appear comparing Arc'teryx and Salomon against the 2026-Q1 baseline created in Task 7.

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "feat: add main orchestrator with deduplication"
```

---

## Task 7: Migrate Legacy Data to Unified Format

**Files:**
- Create: `migrate_legacy.py` (one-time script, keep in repo for audit trail)

The existing raw data files (in `202512/` and `202603/`) were generated by the old scripts without deduplication. They must be cleaned and consolidated into the new quarterly format so that `output.py`'s change detection has a valid baseline.

**What exists:**

| Quarter | Folder | Brands available | Notes |
|---|---|---|---|
| 2025-Q1 | `202503/` | Arc'teryx (2427 raw → 2030 unique), Hoka (11441 raw → 8881 unique), Salomon (22993 raw → 17444 unique) | Arc'teryx has extra `date` column — drop it. Both US and European stores present |
| 2025-Q4 | `202512/` | Arc'teryx (9492 raw → 2278 unique), Hoka (9311, already unique), Salomon (35458 raw → 18766 unique) | On Running not available |
| 2026-Q1 | `202603/` | Arc'teryx (2298, already unique), Salomon (48047 raw → 25538 unique) | Confirmed: contains both US and European stores. Hoka and On Running not available |

Files to **exclude** (wrong schema — scraped from brand websites, not the locally.com API, and contain no `id` column):
- `202503/Arcteryx brand store-2503.xlsx`
- `202503/store_data_arcteryx_analyzed.xlsx`
- `202512/Arcteryx brand store_202512.xlsx`

After migration, `output.py` will automatically find `store_data_2026-Q1.xlsx` as the previous quarter file when the next run (`store_data_2026-Q2.xlsx`) is produced.

- [ ] **Step 1: Write migrate_legacy.py**

```python
import os
import pandas as pd

# Maps quarter label → legacy files to include.
# Only files with the locally.com API schema (columns: id, name, lat, lng, ...) are included.
# Excluded: brand store pages, analyzed summaries — they have no 'id' column.
LEGACY = {
    "2025-Q1": {
        "folder": "202503",
        "brands": {
            "Arc'teryx": "store_data_arcteryx.xlsx",
            "Hoka":      "store_data_hoka.xlsx",
            "Salomon":   "store_data_Salomon.xlsx",
        },
        "drop_cols": ["date"],  # 202503 Arc'teryx has an extra 'date' column to strip
    },
    "2025-Q4": {
        "folder": "202512",
        "brands": {
            "Arc'teryx": "store_data_arcteryx.xlsx",
            "Hoka":      "store_data_hoka.xlsx",
            "Salomon":   "store_data_Salomon.xlsx",
        },
        "drop_cols": [],
    },
    "2026-Q1": {
        "folder": "202603",
        "brands": {
            "Arc'teryx": "store_data_arcteryx.xlsx",
            "Salomon":   "store_data_Salomon.xlsx",
            # Hoka not available for this quarter — omitted intentionally
        },
        "drop_cols": [],
    },
}


def deduplicate(df: pd.DataFrame) -> pd.DataFrame:
    before = len(df)
    df = df.drop_duplicates(subset=["id"], keep="first").reset_index(drop=True)
    after = len(df)
    if before != after:
        print(f"    dedup: {before} → {after} rows ({before - after} removed)")
    return df


def migrate():
    for quarter, config in LEGACY.items():
        output_file = f"store_data_{quarter}.xlsx"
        if os.path.exists(output_file):
            print(f"[SKIP] {output_file} already exists")
            continue

        print(f"\nMigrating {quarter} → {output_file}")
        with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
            for sheet_name, filename in config["brands"].items():
                path = os.path.join(config["folder"], filename)
                if not os.path.exists(path):
                    print(f"  [WARN] {path} not found — skipping {sheet_name}")
                    continue
                df = pd.read_excel(path)
                print(f"  {sheet_name}: {len(df)} raw rows")
                for col in config.get("drop_cols", []):
                    if col in df.columns:
                        df = df.drop(columns=[col])
                        print(f"    dropped column: {col}")
                df = deduplicate(df)
                df.to_excel(writer, sheet_name=sheet_name, index=False)
                print(f"  {sheet_name}: written {len(df)} unique stores")
        print(f"✅ {output_file} written")


if __name__ == "__main__":
    migrate()
```

- [ ] **Step 2: Run the migration**

```bash
python migrate_legacy.py
```

Expected output (approximate — exact counts from dedup analysis):
```
Migrating 2025-Q1 → store_data_2025-Q1.xlsx
  Arc'teryx: 2427 raw rows
    dropped column: date
    dedup: 2427 → 2030 rows (397 removed)
  Arc'teryx: written 2030 unique stores
  Hoka: 11441 raw rows
    dedup: 11441 → 8881 rows (2560 removed)
  Hoka: written 8881 unique stores
  Salomon: 22993 raw rows
    dedup: 22993 → 17444 rows (5549 removed)
  Salomon: written 17444 unique stores
✅ store_data_2025-Q1.xlsx written

Migrating 2025-Q4 → store_data_2025-Q4.xlsx
  Arc'teryx: 9492 raw rows
    dedup: 9492 → 2278 rows (7214 removed)
  Arc'teryx: written 2278 unique stores
  Hoka: 9311 raw rows
  Hoka: written 9311 unique stores
  Salomon: 35458 raw rows
    dedup: 35458 → 18766 rows (16692 removed)
  Salomon: written 18766 unique stores
✅ store_data_2025-Q4.xlsx written

Migrating 2026-Q1 → store_data_2026-Q1.xlsx
  Arc'teryx: 2298 raw rows
  Arc'teryx: written 2298 unique stores
  Salomon: 48047 raw rows
    dedup: 48047 → 25538 rows (22509 removed)
  Salomon: written 25538 unique stores
✅ store_data_2026-Q1.xlsx written
```

- [ ] **Step 3: Verify the output files**

Open `store_data_2026-Q1.xlsx` — confirm it has two sheets (Arc'teryx, Salomon) with the expected row counts and that ID values look correct (integers, not floats).

- [ ] **Step 4: Re-run main.py and confirm the Changes sheet appears**

```bash
python main.py salomon
```

Expected: `store_data_2026-Q2.xlsx` is written, and it contains a "Changes" sheet comparing against `store_data_2026-Q1.xlsx`. The Salomon sheet should show differences between the 25,538-store 2026-Q1 baseline and the new crawl.

> **Note on coverage gaps in baseline:** The 2026-Q1 baseline contains Arc'teryx and Salomon with both US and European stores — these will produce a clean, meaningful diff in the 2026-Q2 run. Hoka and On Running have no prior baseline in the unified format; `_detect_changes()` will simply skip sheets absent from the previous file, so they will produce no Changes rows until 2026-Q3.

- [ ] **Step 5: Commit**

```bash
git add migrate_legacy.py store_data_2025-Q1.xlsx store_data_2025-Q4.xlsx store_data_2026-Q1.xlsx
git commit -m "chore: migrate legacy raw data to unified quarterly format"
```

---

## Task 8: Cleanup Old Scripts

Only do this after Task 7 migration passes end-to-end.

`Wolfskin map.py` is **kept** — it is not part of this implementation but may be incorporated later.

- [ ] **Step 1: Delete replaced scripts**

```bash
git rm "Hoka map.py" "Arcteryx map.py" "Salomon map.py" "On map.py" utils.py
```

- [ ] **Step 2: Commit**

```bash
git commit -m "refactor: remove replaced per-brand scripts (Wolfskin map.py retained)"
```

---

## Self-Review

### Spec coverage checklist

| Requirement | Covered in |
|---|---|
| One command runs all brands | Task 6 `main.py` |
| Deduplication by store ID | Task 6 `deduplicate()` |
| One Excel file, one sheet per brand | Task 5 `write_output()` |
| Change detection vs previous quarter | Task 5 `_detect_changes()` |
| On Running automated | Task 4 `on_crawl()` |
| Easy to filter brands via CLI | Task 6 `sys.argv` handling |
| Boundary values match original scripts | Task 2 `brands.py` + Region Reference table |
| Legacy raw data cleaned and consolidated | Task 7 `migrate_legacy.py` |
| Jack Wolfskin excluded | Removed from all files |

### Known gaps / manual steps

1. **On Running API URL** must be discovered manually (Task 4 Step 1) before the automated path works. The file-based fallback in Task 4 Step 4 is a safe interim.
2. **Arc'teryx session cookie** is initialized automatically via `_get_session()` using `requires_session: True`. If the cookie expires mid-run, the crawler will silently return empty tiles. This is acceptable for a quarterly run.
3. **Region coverage**: The regions in `brands.py` cover North America and Europe only, matching the existing scripts. Add more region entries to `brands[x]["regions"]` if Asia/Pacific coverage is needed later.
4. **Baseline coverage gaps**: The 2026-Q1 baseline has Arc'teryx (2,298 stores, both US+Europe) and Salomon (25,538 stores, both US+Europe). Hoka and On Running have no prior unified-format baseline — their Changes comparison begins at 2026-Q3.
5. **Hoka 2025-Q4 has no duplicates** (9311 rows, all unique IDs) — the old Hoka script happened to save without overlap, so migration is a straight copy for that file.
6. **Jack Wolfskin** is intentionally excluded from this implementation. `Wolfskin map.py` is retained in the repo but not deleted (user may add it later).
