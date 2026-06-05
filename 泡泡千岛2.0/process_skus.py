import os
import time
import pandas as pd
from datetime import datetime
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, WebDriverException
import sqlite3
import re
import config

def parse_want_info(text: str) -> tuple[int, int, int]:
    if not text or text == "N/A":
        return 0, 0, 0
    
    def extract_metric(keyword: str) -> int:
        pattern = r"([\d\.]+)\s*([kw万]?)\s*" + keyword
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            return 0
            
        val_str = match.group(1)
        multiplier_str = match.group(2).lower()
        
        try:
            val = float(val_str)
            if multiplier_str == 'k':
                val *= 1000
            elif multiplier_str in ['w', '万']:
                val *= 10000
            return int(val)
        except ValueError:
            return 0

    views = extract_metric("浏览")
    wants = extract_metric("想要")
    owns = extract_metric("拥有")
    
    return views, wants, owns

def parse_generic_number(text: str, is_float: bool = False):
    if not text or str(text).strip() in ["N/A", ""]: 
        return 0.0 if is_float else 0
        
    text = str(text).replace('¥', '').replace(',', '').strip()
    match = re.search(r'([\d\.]+)\s*([kw万]?)', text, re.IGNORECASE)
    if not match:
        return 0.0 if is_float else 0
        
    val = float(match.group(1))
    m = match.group(2).lower()
    if m == 'k': val *= 1000
    elif m in ['w', '万']: val *= 10000
    
    return float(val) if is_float else int(val)

def extract_nuxt_stats(soup) -> dict:
    """
    Parse the __NUXT_DATA__ JSON array embedded by Nuxt 3 SSR.

    The Nuxt 3 SSR payload is a flat array where dicts map field names to
    array indices. The page structure is:
      data[N] = {'view': M}  →  data[M] = {'detailInfo': X, 'tradeInfo': Y, ...}
      data[X] = {'spuId': ..., ...}   (the main item)
      data[Y] = {'strikePrice': A, 'orderCount': B, 'demandCount': C, 'saleCount': D}

    We follow this chain so we always get stats for the page's primary item,
    not for related/recommended items elsewhere in the payload.
    """
    defaults = {'strikePrice': 0.0, 'orderCount': 0, 'demandCount': 0, 'saleCount': 0, 'minOnlinePrice': 0.0}
    try:
        import json
        tag = soup.find('script', id='__NUXT_DATA__')
        if not tag:
            return defaults
        data = json.loads(tag.string)

        def deref(idx):
            return data[idx] if isinstance(idx, int) and idx < len(data) else None

        # Step 1: find the page-view entry {'view': M}
        view_idx = None
        for item in data:
            if isinstance(item, dict) and list(item.keys()) == ['view']:
                view_idx = item['view']
                break
        if view_idx is None:
            return defaults

        # Step 2: follow view → page object → tradeInfo
        page_obj = deref(view_idx)
        if not isinstance(page_obj, dict) or 'tradeInfo' not in page_obj:
            return defaults

        trade_map = deref(page_obj['tradeInfo'])
        if not isinstance(trade_map, dict):
            return defaults

        result = {}
        for key in ('strikePrice', 'orderCount', 'demandCount', 'saleCount'):
            val = deref(trade_map.get(key))
            result[key] = val if val is not None else 0

        # Step 3: minOnlinePrice lives in a separate dict (the purchase-button extraData).
        # There is exactly one dict in the payload with both minOnlinePrice and strikePrice,
        # so a linear scan is unambiguous.
        result['minOnlinePrice'] = 0.0
        for item in data:
            if isinstance(item, dict) and 'minOnlinePrice' in item and 'strikePrice' in item:
                val = deref(item['minOnlinePrice'])
                if val is not None:
                    result['minOnlinePrice'] = float(val)
                break

        return result
    except Exception:
        return defaults


def robust_get(driver, url, wait_time=2, max_retries=3):
    """Wraps selenium get with retries to survive spotty network conditions."""
    for attempt in range(max_retries):
        try:
            driver.get(url)
            time.sleep(wait_time)
            return driver.page_source
        except (TimeoutException, WebDriverException) as e:
            print(f"  [WARN] Page load failed (Attempt {attempt+1}/{max_retries}): {e}")
            time.sleep(2)
    print(f"  [ERROR] Failed to load {url} after {max_retries} attempts.")
    return driver.page_source # return whatever we have

def main():
    input_file = os.path.join("output", "results.db")
    db_file = os.path.join("output", "sku_database.db")
    lean_output_file = os.path.join("output", "sku_lean_tracking.xlsx")

    if not os.path.exists(input_file):
        print(f"Error: Could not find input database at {input_file}")
        return 0, 0

    try:
        conn = sqlite3.connect(input_file)
        df_input = pd.read_sql(
            "SELECT * FROM feed_results WHERE query_date = (SELECT MAX(query_date) FROM feed_results)",
            conn
        )
        conn.close()
    except Exception as e:
        print(f"Error reading from results.db: {e}")
        return 0, 0

    if 'id' not in df_input.columns:
        print("Error: Column 'id' not found in results.db")
        return 0, 0

    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=getattr(config, 'PROCESS_SKUS_LIMIT', 5))
    args, unknown = parser.parse_known_args()
    limit = args.limit
    
    all_target_ids = df_input['id'].head(limit).tolist()
    print(f"Loaded {len(all_target_ids)} total IDs from Database (Limit: {limit}).")

    current_date = datetime.now().strftime("%Y-%m-%d")

    # 1. State Tracking: Check Database for already processed items today to resume seamlessly if crashed
    already_processed = set()
    if os.path.exists(db_file):
        try:
            conn = sqlite3.connect(db_file)
            # Query IDs already scanned today
            query = f"SELECT DISTINCT spu_id FROM sku_raw_records WHERE query_date = '{current_date}'"
            processed_df = pd.read_sql(query, conn)
            already_processed = set(processed_df['spu_id'].astype(str))
            conn.close()
            print(f"Resume Checker: Found {len(already_processed)} series already safely recorded for today.")
        except Exception as e:
            print(f"Resume Checker info (safe to ignore if first run): {e}")

    # Remove duplicates and already processed
    target_ids = []
    for spu_id in all_target_ids:
        if str(spu_id) not in already_processed:
            target_ids.append(spu_id)
            already_processed.add(str(spu_id))

    print(f"Queueing {len(target_ids)} Series URLs for processing...")
    if not target_ids:
        print("All targets have already been processed today. Exiting.")
        return 0, 0

    # 2. Setup Selenium
    chrome_options = Options()
    chrome_options.add_argument("--ignore-certificate-errors")
    chrome_options.add_argument("--ignore-ssl-errors")
    chrome_options.add_argument("--disable-features=NetworkService")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    # chrome_options.add_argument("--headless") # uncomment for background running

    try:
        service = Service('C:\\Program Files\\python chrome\\chrome-win\\chromedriver.exe')
        driver = webdriver.Chrome(service=service, options=chrome_options)
        driver.set_page_load_timeout(15) 
    except Exception as e:
        print("Falling back to default chromedriver PATH...")
        driver = webdriver.Chrome(options=chrome_options)
        driver.set_page_load_timeout(15)

    base_url = "https://qiandao.com"
    all_lean_data = []
    processed_series_count = 0

    print(f"\n--- [DEBUG] Starting Loop over {len(target_ids)} Target Series ---")
    for idx, spu_id in enumerate(target_ids, 1):
        try:
            series_url = f"{base_url}/spu?id={spu_id}"
            print(f"[{idx}/{len(target_ids)}] Phase 4 -> Processing SPU ID: {spu_id}")

            page_source = robust_get(driver, series_url, wait_time=2)
            soup = BeautifulSoup(page_source, 'html.parser')

            series_name = "N/A"
            h1_tag = soup.find('div', class_='single-spu-name')
            if h1_tag:
                series_name = h1_tag.get_text(strip=True)
            print(f"  → Found series name: '{series_name}'")

            # Get mainTagDisplayName from input data for this spu_id
            main_tag_display_name = "N/A"
            matching_row = df_input[df_input['id'].astype(str) == str(spu_id)]
            if not matching_row.empty:
                main_tag_display_name = matching_row.iloc[0].get('mainTagDisplayName', 'N/A')

            # 2. Extract SPU-level metrics (formerly sub-SKU logic)
            # Find listing price on series page
            div_tag = soup.find('div', {'class': 'spu-price'})
            div_text = div_tag.get_text(strip=True) if div_tag else "N/A"
            listing_price = parse_generic_number(div_text, is_float=True)

            # Find Stats (Views, Wants, Owns)
            p_tag = soup.find('div', class_='want_info')
            p_text = p_tag.get_text(strip=True) if p_tag else "N/A"
            views, wants, owns = parse_want_info(p_text)
            print(f"  → Want info: '{p_text}' (Views: {views}, Wants: {wants}, Owns: {owns})")

            # Extract transaction stats from embedded __NUXT_DATA__ JSON
            nuxt = extract_nuxt_stats(soup)
            sku_price = nuxt['strikePrice']
            sku_personpaid = nuxt['orderCount']
            sku_personselling = nuxt['saleCount']
            sku_personbuying = nuxt['demandCount']
            listing_price = nuxt['minOnlinePrice'] or listing_price

            # Create a single record for this series
            record = {
                'spu_id': str(spu_id),
                'series_name': series_name,
                'sku_name': series_name, # Treating SPU as the name
                'raw_want_info': p_text,
                'views': views,
                'wants': wants,
                'owns': owns,
                'price_listing': listing_price,
                'full_url': series_url,
                'query_date': current_date,
                'curr_avg_price': float(sku_price),
                'num_paid': int(sku_personpaid),
                'num_selling': int(sku_personselling),
                'num_buying': int(sku_personbuying),
                'mainTagDisplayName': main_tag_display_name
            }

            series_records = [record]

            # == 3. Incremental Saving (Saves per record) ==
            if series_records:
                df_series = pd.DataFrame(series_records)

                # SQLite Dump
                try:
                    conn = sqlite3.connect(db_file)
                    # We'll keep the same table name 'sku_raw_records' for compatibility
                    df_series.to_sql('sku_raw_records', conn, if_exists='append', index=False)
                    conn.close()
                except Exception as e:
                    print(f"  [ERROR] Database save failed for series: {e}")

                # Lean Excel processing (Single row per series)
                lean_rows = [[
                    series_name, "全部", 1, 0,
                    views, wants, owns,
                    int(sku_personpaid),
                    int(sku_personselling),
                    int(sku_personbuying),
                    listing_price,
                    float(sku_price),
                    current_date, series_url,
                    main_tag_display_name
                ]]

                all_lean_data.extend(lean_rows)
                processed_series_count += 1
                print(f"  [OK] Series {idx} (ID: {spu_id}) recorded successfully.")
            else:
                print(f"  [WARN] No SKU data extracted for series ID: {spu_id}")
        
        except Exception as outer_e:
            print(f"  [FATAL CRASH] Error in outer loop for series {idx}: {outer_e}")
            import traceback
            traceback.print_exc()
            continue

    driver.quit()
    print("\n--- Scraping run completed successfully! ---")

    # Final Excel dump for lean tracking
    if all_lean_data:
        df_lean_new = pd.DataFrame(all_lean_data, columns=[
            "系列名", "分类筛选", "总SKU数", "隐藏款SKU数", 
            "浏览量", "想要人数", "拥有人数", "付款人数", "正在出售", "正在求购",
            "交易价格_平均", "成交均价_平均", "查询日期", "系列URL", "主标签"
        ])

        excel_file = lean_output_file
        if os.path.exists(excel_file):
            try:
                # Read existing data
                df_existing = pd.read_excel(excel_file, sheet_name='lean_tracking')
                # Append new data
                df_combined = pd.concat([df_existing, df_lean_new], ignore_index=True)
                # Remove duplicates based on key columns (e.g., series_name, filter_cat, query_date)
                df_combined.drop_duplicates(subset=["系列名", "分类筛选", "查询日期"], keep='last', inplace=True)
                df_combined.to_excel(excel_file, sheet_name='lean_tracking', index=False, engine='openpyxl')
            except Exception as e:
                print(f"  [ERROR] Failed to update existing lean excel file: {e}")
                df_lean_new.to_excel(excel_file, sheet_name='lean_tracking', index=False, engine='openpyxl') # Fallback to overwrite
        else:
            df_lean_new.to_excel(excel_file, sheet_name='lean_tracking', index=False, engine='openpyxl')
        print(f"\n[LEAN DB] Excel spreadsheet updated gracefully! ({len(df_lean_new)} new rows added)")
        
    return len(all_target_ids), processed_series_count

if __name__ == '__main__':
    total, success = main()
    print(f"\n[SUMMARY] Processed {success} of {total} scheduled items.")
