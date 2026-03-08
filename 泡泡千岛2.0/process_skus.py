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
        return

    try:
        conn = sqlite3.connect(input_file)
        df_input = pd.read_sql("SELECT * FROM feed_results", conn)
        conn.close()
    except Exception as e:
        print(f"Error reading from results.db: {e}")
        return

    if 'id' not in df_input.columns:
        print("Error: Column 'id' not found in results.db")
        return

    # Extract IDs based on config limit
    limit = getattr(config, 'PROCESS_SKUS_LIMIT', 5)
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
        return

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

    for idx, spu_id in enumerate(target_ids, 1):
        series_url = f"{base_url}/spu?id={spu_id}"
        print(f"\n[{idx}/{len(target_ids)}] Processing Series: {series_url}")

        page_source = robust_get(driver, series_url, wait_time=2)
        soup = BeautifulSoup(page_source, 'html.parser')

        series_name = "N/A"
        h1_tag = soup.find('div', class_='single-spu-name')
        if h1_tag:
            series_name = h1_tag.get_text(strip=True)

        a_tags = soup.find_all('a', {'data-v-ba00a76c': "", 'aria-current': "page"})
        print(f"  → Found {len(a_tags)} sub-SKUs in series: '{series_name}'")

        series_records = []

        for tag in a_tags:
            try:
                href = tag.get('href')
                full_url = base_url + href
                
                h3_tag = tag.find('h3', {'data-v-ba00a76c': "", 'class': 'name u-otext2'})
                h3_text = h3_tag.get_text(strip=True) if h3_tag else "N/A"
                
                div_tag = tag.find('div', {'data-v-ba00a76c': "", 'class': 'spu-price text-center font-num text-n6'})
                div_text = div_tag.get_text(strip=True) if div_tag else "N/A"

                print(f"    Fetching SKU: {h3_text}")
                sku_page_source = robust_get(driver, full_url, wait_time=1.5)
                soup_sku = BeautifulSoup(sku_page_source, 'html.parser')
                
                wrapper_div = soup_sku.find('div', {'class': 'wrapper'})
                if wrapper_div:
                    span_elements = wrapper_div.find_all('span', {'class': 'number'})
                    if len(span_elements) >= 4:
                        sku_price = span_elements[0].get_text(strip=True)
                        sku_personpaid = span_elements[1].get_text(strip=True)
                        sku_personselling = span_elements[2].get_text(strip=True)
                        sku_personbuying = span_elements[3].get_text(strip=True)
                    else:
                        sku_price = sku_personpaid = sku_personselling = sku_personbuying = "N/A"
                else:
                    sku_price = sku_personpaid = sku_personselling = sku_personbuying = "N/A"

                p_tag2 = soup_sku.find('div', class_='want_info')
                p_text2 = p_tag2.get_text(strip=True) if p_tag2 else "N/A"
                
                views, wants, owns = parse_want_info(p_text2)
                is_hidden = 1 if '隐藏' in h3_text else 0

                series_records.append({
                    'spu_id': str(spu_id),
                    'series_name': series_name,
                    'sku_name': h3_text,
                    'raw_want_info': p_text2,
                    'views': views,
                    'wants': wants,
                    'owns': owns,
                    'price_listing': parse_generic_number(div_text, is_float=True),
                    'full_url': full_url,
                    'query_date': current_date,
                    'curr_avg_price': parse_generic_number(sku_price, is_float=True),
                    'num_paid': parse_generic_number(sku_personpaid, is_float=False),
                    'num_selling': parse_generic_number(sku_personselling, is_float=False),
                    'num_buying': parse_generic_number(sku_personbuying, is_float=False),
                    'is_hidden': is_hidden
                })
                
            except Exception as e:
                print(f"    [ERROR] Failed to parse SKU '{href}': {e}")
                continue

        # == 3. Incremental Saving (Saves per series) ==
        if series_records:
            df_series = pd.DataFrame(series_records)

            # SQLite Dump
            try:
                conn = sqlite3.connect(db_file)
                df_db = df_series.drop(columns=['is_hidden'])
                df_db.to_sql('sku_raw_records', conn, if_exists='append', index=False)
                conn.close()
            except Exception as e:
                print(f"  [ERROR] Database save failed for series: {e}")

            # Lean Excel processing
            lean_rows = []
            total_skus = len(df_series)
            hidden_skus = df_series['is_hidden'].sum()

            filters = [
                ("全部", df_series),
                ("隐藏款", df_series[df_series['is_hidden'] == 1]),
                ("常规款", df_series[df_series['is_hidden'] == 0])
            ]

            for filter_cat, subset in filters:
                if subset.empty:
                    lean_rows.append([
                        series_name, filter_cat, total_skus, hidden_skus,
                        0, 0, 0, 0, 0, 0, 0.0, 0.0, current_date, f"https://qiandao.com/spu?id={spu_id}"
                    ])
                else:
                    lean_rows.append([
                        series_name, filter_cat, total_skus, hidden_skus,
                        subset['views'].sum() if not subset['views'].isna().all() else 0,
                        subset['wants'].sum() if not subset['wants'].isna().all() else 0,
                        subset['owns'].sum() if not subset['owns'].isna().all() else 0,
                        subset['num_paid'].sum() if not subset['num_paid'].isna().all() else 0,
                        subset['num_selling'].sum() if not subset['num_selling'].isna().all() else 0,
                        subset['num_buying'].sum() if not subset['num_buying'].isna().all() else 0,
                        subset['price_listing'].mean() if not subset['price_listing'].isna().all() else 0.0,
                        subset['curr_avg_price'].mean() if not subset['curr_avg_price'].isna().all() else 0.0,
                        current_date, f"https://qiandao.com/spu?id={spu_id}"
                    ])

            all_lean_data.extend(lean_rows)
            processed_series_count += 1
            print(f"  ✓ Safely secured {total_skus} SKUs to disk.")
        else:
            print(f"  ✓ No SKUs extracted for series.")

    driver.quit()
    print("\n--- Scraping run completed successfully! ---")

    # Final Excel dump for lean tracking
    if all_lean_data:
        df_lean_new = pd.DataFrame(all_lean_data, columns=[
            "系列名", "分类筛选", "总SKU数", "隐藏款SKU数", 
            "浏览量", "想要人数", "拥有人数", "付款人数", "正在出售", "正在求购",
            "交易价格_平均", "成交均价_平均", "查询日期", "系列URL"
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
