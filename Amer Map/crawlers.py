import json
import requests
import time
from typing import List, Optional

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


def _fetch_locally_tile(session, brand, sw_lat, sw_lng, ne_lat, ne_lng, zoom) -> Optional[dict]:
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


def on_crawl(brand: dict) -> List[dict]:
    """Fetch all On Running dealers from their global API in a single call."""
    headers = {
        **COMMON_HEADERS,
        "Referer": "https://customer-service.on-running.com/en-us/dealers/",
        **brand.get("api_headers", {}),
    }
    params = brand.get("api_params", {"all": "true"})

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
