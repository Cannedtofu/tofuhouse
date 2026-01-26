import requests
import time
import pandas as pd
import os
from typing import List
import json

def get_session_cookie():
    session = requests.Session()
    url = "https://arcteryx.locally.com/conversion?company_id=31&inline=1&lang=en&dealers_company_id=31&host_domain=arcteryx.com"
    
    # Do a GET to initiate the session
    session.get(url, headers={
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://arcteryx.locally.com/",
    })
    
    cookies = session.cookies.get_dict()
    lg_cookie = cookies.get("lg_session_v1")
    
    if not lg_cookie:
        raise RuntimeError("lg_session_v1 cookie not found in session")

    return session, lg_cookie

session, lg_cookie = get_session_cookie()

# Configuration
# HEADERS = {
#     "Accept": "application/json, text/javascript, */*; q=0.01",
#     "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
#     "X-Requested-With": "XMLHttpRequest",
#     "Referer": "https://arcteryx.locally.com/conversion?company_id=31&inline=1&lang=en&dealers_company_id=31&host_domain=arcteryx.com",
#     "Cookie": f"lg_session_v1={lg_cookie}"
# }
HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://arcteryx.locally.com/conversion?company_id=31&inline=1&lang=en&dealers_company_id=31&host_domain=arcteryx.com",
}

BASE_URL = "https://arcteryx.locally.com/stores/conversion_data"



# US Canada boundaries
LAT_MIN, LAT_MAX = 24.5, 83
LNG_MIN, LNG_MAX = -170, -52
# Grid step: smaller step = more zoomed in
LAT_STEP = 1.1751/2
LNG_STEP = 3.0185/2
ZOOM_LEVEL = 8.4736  # More zoomed-in than the default site (5–6)


# # Europe Boundaries
# LAT_MIN, LAT_MAX = 34.5, 71
# LNG_MIN, LNG_MAX = -10, 30

# # Grid step: smaller step = more zoomed in
# LAT_STEP = 0.871/2
# LNG_STEP = 3.019/2
# ZOOM_LEVEL = 8.4736  # More zoomed-in than the default site (5–6)


# #test boundaries
# LAT_MIN, LAT_MAX = 37, 38
# LNG_MIN, LNG_MAX = -122.5, -121.5

# # Grid step: smaller step = more zoomed in
# LAT_STEP = 1
# LNG_STEP = 1
# ZOOM_LEVEL = 6  # More zoomed-in than the default site (5–6)



def save_raw_json(data, lat, lng, zoom, directory="raw_json_logs"):
    if not os.path.exists(directory):
        os.makedirs(directory)
    filename = f"tile_lat{lat}_lng{lng}_zoom{zoom}.txt"
    filepath = os.path.join(directory, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, indent=2))

def fetch_tile(sw_lat, sw_lng, ne_lat, ne_lng, zoom_level=ZOOM_LEVEL):
    params = {
    "has_data": "true",
    "company_id": "31",
    "store_mode": "",
    "style": "",
    "color": "",
    "upc": "",
    "category": "",
    "inline": "1",
    "show_links_in_list": "",
    "parent_domain": "",
    "map_ne_lat": ne_lat,
    "map_ne_lng": ne_lng,
    "map_sw_lat": sw_lat,
    "map_sw_lng": sw_lng,
    "map_center_lat": (sw_lat + ne_lat) / 2,
    "map_center_lng": (sw_lng + ne_lng) / 2,
    "map_distance_diag": 170,  # constant ok unless you're dynamically adjusting
    "sort_by": "proximity",  # not "sort"
    "no_variants": "0",
    "only_retailer_id": "",
    "dealers_company_id": "31",
    "only_store_id": "false",
    "uses_alt_coords": "false",
    "q": "san francisco",  # optional, may influence behavior
    "zoom_level": zoom_level,
    "lang": "en",
    "forced_coords": "1",
    }

    try:
        resp = session.get(BASE_URL, headers=HEADERS, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[ERROR] Tile ({sw_lat}, {sw_lng}) - {e}")
        log_failed_tile((sw_lat, sw_lng, ne_lat, ne_lng))
        return None

def extract_store_info(data):
    stores = []
    for key in data:
        if key.startswith("markers"):
            marker_list = data[key]
            for s in marker_list:
                stores.append({
                    "id": s.get("id"),
                    "name": s.get("name"),
                    "lat": s.get("lat"),
                    "lng": s.get("lng"),
                    "city": s.get("city"),
                    "state": s.get("state"),
                    "address": s.get("address"),
                    "phone": s.get("phone"),
                    "country": s.get("country"),
                    "is_claimed": s.get("is_claimed"),
                    "company_id": s.get("company_id"),
                    "vendor_id": s.get("vendor_id"),
                    "sort_level": s.get("sort_level"),
                    "stock_status": s.get("stock_status"),
                    "stock_class": s.get("stock_class"),
                    "low_quantity_label": s.get("low_quantity_label"),
                    "low_quantity_threshold": s.get("low_quantity_threshold"),
                    "is_hosted": s.get("is_hosted"),
                    "only_dropship": s.get("only_dropship"),
                    "dropship_disclaimer": s.get("dropship_disclaimer"),
                    "features": s.get("features"),
                    "enhanced_categories": s.get("enhanced_categories"),
                    "disclaimer": s.get("disclaimer"),
                })
    return stores

def frange(start, stop, step):
    while start < stop:
        yield round(start, 6)
        start += step

def crawl_one_tile(sw_lat, sw_lng, ne_lat, ne_lng, zoom_level):
    all_stores = []
    try:
        print(f" Crawling tile {sw_lat, sw_lng, ne_lat, ne_lng, zoom_level}")
        tile_data = fetch_tile(sw_lat, sw_lng, ne_lat, ne_lng, zoom_level)
        # print(tile_data)
        if tile_data and "markers" in tile_data:
            markers = tile_data["markers"]
            stores = extract_store_info(tile_data)
            print(f"✅ Found {len(stores)} stores.")
            if len(stores) > 0:
                save_to_excel(stores)
    except Exception as e:
        print("⚠️ No data or bad response")
        log_failed_tile((sw_lat, sw_lng, ne_lat, ne_lng, zoom_level))   
    
    time.sleep(REQUEST_DELAY)  # Be nice to the server

    return all_stores    

def crawl_all_tiles():
    all_stores = []
    tmp_all_stores = []
    current_step = 0

    for lat in frange(LAT_MIN, LAT_MAX, LAT_STEP):
        for lng in frange(LNG_MIN, LNG_MAX, LNG_STEP):
            print(f" Crawling tile {lat, lng, lat + LAT_STEP, lng + LNG_STEP}")
            current_step = current_step + 1
            remaining_steps = total_steps - current_step
            print(f"Progress: {current_step}/{total_steps} ({(current_step / total_steps) * 100:.2f}%) - Remaining: {remaining_steps} steps")

            try:
                tile_data = fetch_tile(lat, lng, lat + LAT_STEP, lng + LNG_STEP)
                
                # #debug
                # save_raw_json(tile_data, lat, lng, ZOOM_LEVEL)
                # #debug

                # print(tile_data)
                if tile_data and "markers" in tile_data:
                    markers = tile_data["markers"]
                    stores = extract_store_info(tile_data)
                    print(f"✅ Found {len(stores)} stores.")
                    print(stores)
                    tmp_all_stores.extend(stores)
                    if len(tmp_all_stores) > 100:
                        save_to_excel(tmp_all_stores)
                        all_stores.extend(tmp_all_stores)
                        tmp_all_stores = []  # Reset after saving
                    if remaining_steps <= 0:
                        save_to_excel(tmp_all_stores)
                    print(f"current store stack count: {len(tmp_all_stores)}")
            except Exception as e:
                print("⚠️ No data or bad response")
                log_failed_tile((lat, lng, lat + LAT_STEP, lng + LNG_STEP))
            
            time.sleep(REQUEST_DELAY)  # Be nice to the server

    return all_stores

def log_failed_tile(tile):
    with open(FAILED_TILES_LOG, "a") as f:
        f.write(f"{tile}\n")

def save_to_excel(data: List[dict]):
    df = pd.DataFrame(data)
    if os.path.exists(OUTPUT_FILE):
        existing_df = pd.read_excel(OUTPUT_FILE)
        df = pd.concat([existing_df, df], ignore_index=True)
    df.to_excel(OUTPUT_FILE, index=False)

def retry_failed_tiles(subdivision_factor=2, zoom_increment=1):
    if not os.path.exists(FAILED_TILES_LOG):
        print("No failed tiles to retry.")
        return
    
    with open(FAILED_TILES_LOG, "r") as f:
        failed_tiles = [eval(line.strip()) for line in f.readlines()]
    
    if not failed_tiles:
        print("No failed tiles found.")
        return

    print(f"Retrying {len(failed_tiles)} failed tiles with subdivision factor {subdivision_factor}...")

    new_failed_tiles = []

    for tile in failed_tiles:
        sw_lat, sw_lng, ne_lat, ne_lng = tile
        lat_step = (ne_lat - sw_lat) / subdivision_factor
        lng_step = (ne_lng - sw_lng) / subdivision_factor
        print(f"🔍 Subdividing tile: {tile}")

        for i in range(subdivision_factor):
            for j in range(subdivision_factor):
                sub_sw_lat = sw_lat + i * lat_step
                sub_sw_lng = sw_lng + j * lng_step
                sub_ne_lat = sub_sw_lat + lat_step
                sub_ne_lng = sub_sw_lng + lng_step

                print(f"  ↳ Trying sub-tile: {(sub_sw_lat, sub_sw_lng, sub_ne_lat, sub_ne_lng)}")
                try:
                    tile_data = fetch_tile(
                        sub_sw_lat, sub_sw_lng,
                        sub_ne_lat, sub_ne_lng,
                        zoom_level=ZOOM_LEVEL + zoom_increment
                    )

                    if tile_data and "markers" in tile_data:
                        stores = extract_store_info(tile_data)
                        print(f"    ✅ Found {len(stores)} stores")
                        if len(stores) > 0:
                            save_to_excel(stores)
                    else:
                        print("    ⚠️ No data returned")
                        new_failed_tiles.append((sub_sw_lat, sub_sw_lng, sub_ne_lat, sub_ne_lng))
                except Exception as e:
                    print(f"    ❌ Exception: {e}")
                    new_failed_tiles.append((sub_sw_lat, sub_sw_lng, sub_ne_lat, sub_ne_lng))

                time.sleep(REQUEST_DELAY)

    # Overwrite the failed tiles log with only new failures
    with open(FAILED_TILES_LOG, "w") as f:
        for t in new_failed_tiles:
            f.write(f"{t}\n")

    print(f"🔁 Retry complete. Remaining failed sub-tiles: {len(new_failed_tiles)}")





OUTPUT_FILE = "store_data_arcteryx.xlsx"
FAILED_TILES_LOG = "failed_arc_tiles.txt"
REQUEST_DELAY = 0.1 # seconds between requests
lat_steps = list(frange(LAT_MIN, LAT_MAX, LAT_STEP))
lng_steps = list(frange(LNG_MIN, LNG_MAX, LNG_STEP))

total_steps = len(lat_steps) * len(lng_steps)


if __name__ == "__main__":
    results = crawl_all_tiles()
    # results = crawl_one_tile(66, -145, 47, -95, 4)
    retry_failed_tiles()
    print(f"✅ Total stores found: {len(results)}")
