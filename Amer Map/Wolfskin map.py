import requests
import time
import pandas as pd
import os
from typing import List
import json
    


# # test boundaries
# LAT_MIN, LAT_MAX = 53.5488282, 54.5488282
# LNG_MIN, LNG_MAX = 9.987170299999999, 10.987170299999999

# # Grid step: smaller step = more zoomed in
# LAT_STEP = 1
# LNG_STEP = 1


# Germany Boundaries
# LAT_MIN, LAT_MAX = 47.27, 55.06
# LNG_MIN, LNG_MAX = 5.87, 15.04

# # Grid step: smaller step = more zoomed in
# LAT_STEP = 0.708
# LNG_STEP = 3.057
# ZOOM_LEVEL = 8.4736  # More zoomed-in than the default site (5–6)
# BASE_URL = "https://www.jack-wolfskin.de/on/demandware.store/Sites-JackWolfskin_DE-Site/default/Store-FindStores"


# Switzerland Boundaries
LAT_MIN, LAT_MAX = 45.82, 47.81
LNG_MIN, LNG_MAX = 5.96, 10.49

# Grid step: smaller step = more zoomed in
LAT_STEP = 0.663
LNG_STEP = 2.265
ZOOM_LEVEL = 8.4736  # More zoomed-in than the default site (5–6)
BASE_URL = "https://www.jack-wolfskin.ch/on/demandware.store/Sites-JackWolfskin_CH-Site/fr_CH/Store-FindStores"


# Configuration
HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://arcteryx.locally.com/conversion?company_id=31&inline=1&lang=en&dealers_company_id=31&host_domain=arcteryx.com",
}



def log_failed_tile(tile):
    with open(FAILED_TILES_LOG, "a") as f:
        f.write(f"{tile}\n")

def frange(start, stop, step):
    while start < stop:
        yield round(start, 6)
        start += step

def save_to_excel(data: List[dict]):
    df = pd.DataFrame(data)
    if os.path.exists(OUTPUT_FILE):
        existing_df = pd.read_excel(OUTPUT_FILE)
        df = pd.concat([existing_df, df], ignore_index=True)
    df.to_excel(OUTPUT_FILE, index=False)


def fetch_tile(lat, lng):
    params = {
    "lat": lat,
    "lng": lng,
    }

    try:
        resp = requests.get(BASE_URL, headers=HEADERS, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[ERROR] Tile ({lat}, {lng}) - {e}")
        log_failed_tile((lat, lng, ))
        return None

def extract_store_info(store_data):  
    stores = []
    for store in store_data.get("stores", []):
        stores.append({
            "id": store.get("id"),
            "name": store.get("name"),
            "city": store.get("city"),
            "countryName": store.get("countryName"),
            "address": store.get("address1"),
            "address2": store.get("address2"),
            "postalCode": store.get("postalCode"),
            "lat": float(store.get("lat")) if store.get("lat") else None,
            "lng": float(store.get("lng")) if store.get("lng") else None,
            "email": store.get("email"),
            "phone": store.get("phone"),
            "url": store.get("url"),
            "type": store.get("storeType"),
            "services": [s.get("name") for s in store.get("services", []) if isinstance(s, dict)],
            "promotions": [p.get("name") for p in store.get("promotions", []) if isinstance(p, dict)],
            "storeHours": store.get("storeHours")
        })
    return stores


def crawl_all_tiles():
    store_count=0
    all_stores = []
    current_step = 0

    for lat in frange(LAT_MIN, LAT_MAX, LAT_STEP):
        for lng in frange(LNG_MIN, LNG_MAX, LNG_STEP):
            print(f" Crawling tile {lat, lng}")
            current_step = current_step + 1
            remaining_steps = total_steps - current_step
            print(f"Progress: {current_step}/{total_steps} ({(current_step / total_steps) * 100:.2f}%) - Remaining: {remaining_steps} steps")
            try:
                tile_data = fetch_tile(lat, lng)
                print(tile_data)

                stores = extract_store_info(tile_data)
                print(f"✅ Found {len(stores)} stores.")
                store_count = store_count + len(stores)
                all_stores.extend(stores)

            except Exception as e:
                print("⚠️ No data or bad response")
                log_failed_tile((lat, lng, lat + LAT_STEP, lng + LNG_STEP))
            
            time.sleep(REQUEST_DELAY)  # Be nice to the server

    save_to_excel(all_stores)
        
    return store_count




OUTPUT_FILE = "store_data_Wolfskin.xlsx"
FAILED_TILES_LOG = "failed_tiles.txt"
REQUEST_DELAY = 0.1 # seconds between requests
total_steps = int((LAT_MAX - LAT_MIN) / LAT_STEP) * int((LNG_MAX - LNG_MIN) / LNG_STEP)


if __name__ == "__main__":
    # lat= 53.5488282
    # lng= 9.987170299999999
    
    # tile_data = fetch_tile(lat, lng)
    # print(type(tile_data))
    
    
    results = crawl_all_tiles()
    # # results = crawl_one_tile(66, -145, 47, -95, 4)
    # print(f"✅ Total stores found: {len(results)}")