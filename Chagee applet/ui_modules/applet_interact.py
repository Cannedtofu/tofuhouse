import uiautomation as auto
import time
import os
import sys
import pandas as pd
from datetime import datetime

# Add parent directory to sys.path to import ocr_extractor and config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from ocr_extractor import ChageeOCRExtractor
from config import CITY_LIST
from city_switching import switch_city

def get_applet_window():
    for i in range(5):
        for window in auto.GetRootControl().GetChildren():
            if window.ClassName == "Chrome_WidgetWin_0" and window.Name != "微信" and window.Name != "":
                return window
        time.sleep(1)
    return None


def scrape_city_stores(applet_window, extractor, target_count=None, city_name="Default", click_entry=True):
    if target_count is None:
        target_count = config.DEFAULT_TARGET_COUNT
        
    print(f"\n--- Scraping City: {city_name} (Target: {target_count}) ---")
    applet_window.SetActive()
    time.sleep(1)
    rect = applet_window.BoundingRectangle
    
    if click_entry:
        # 1. Click entry coordinate leads to the scrolling page
        entry_x = rect.left + config.STORE_LIST_ENTRY_REL_COORD[0]
        entry_y = rect.top + config.STORE_LIST_ENTRY_REL_COORD[1]
        print(f"Clicking at relative {config.STORE_LIST_ENTRY_REL_COORD} -> Global ({entry_x}, {entry_y})")
        auto.Click(entry_x, entry_y)
        time.sleep(5)
    else:
        print(f"Skipping entry click {config.STORE_LIST_ENTRY_REL_COORD} as requested.")
    
    # 2. Initial scroll / Reset to Top
    scroll_x = rect.left + config.SCROLL_REL_COORD[0]
    scroll_y = rect.top + config.SCROLL_REL_COORD[1]
    auto.MoveTo(scroll_x, scroll_y)
    
    # User Rule: Force reset to top to avoid missing entries
    print("Resetting scroll to top of list...")
    auto.WheelUp(wheelTimes=10, interval=0.1)
    time.sleep(1)
    
    auto.WheelDown(wheelTimes=3, interval=0.1) # Initial settling
    time.sleep(2)

    city_results = {}
    consecutive_no_new = 0
    max_no_new_scrolls = config.MAX_NO_NEW_SCROLLS
    
    while len(city_results) < target_count and consecutive_no_new < max_no_new_scrolls:
        screenshot_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "temp_scrape.png")
        applet_window.CaptureToImage(screenshot_path)
        
        results = extractor.extract_data(screenshot_path)
        new_found = 0
        for res in results:
            name = res['store_name']
            if name not in city_results:
                res['City'] = city_name
                city_results[name] = res
                new_found += 1
                print(f"  [+] {city_name}: {name}")
        
        if new_found > 0:
            consecutive_no_new = 0
        else:
            consecutive_no_new += 1
            print(f"  [!] No new stores found ({consecutive_no_new}/{max_no_new_scrolls}).")
            
        if len(city_results) >= target_count: break
            
        auto.MoveTo(scroll_x, scroll_y)
        auto.WheelDown(wheelTimes=config.SCROLL_WHEEL_TIMES, interval=0.1)
        time.sleep(2)

    if consecutive_no_new >= max_no_new_scrolls:
        print(f"  [!] Aborted {city_name}: Reached limit of {max_no_new_scrolls} scrolls without new data.")

    print(f"Scraped {len(city_results)} stores in {city_name}.")
    return list(city_results.values())

def close_windows():
    print("\n--- Wrapping up: Closing windows using robust method ---")
    try:
        # Import the robust method from cleanup.py in the root directory
        root_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if root_path not in sys.path:
            sys.path.append(root_path)
            
        from cleanup import close_chagee_windows
        close_chagee_windows()
    except Exception as e:
        print(f"Error calling robust cleanup: {e}")
        # Fallback to simple Alt+F4 if import fails
        for window in auto.GetRootControl().GetChildren():
            if window.ClassName == "Chrome_WidgetWin_0" and window.Name != "微信" and window.Name != "":
                window.SetActive()
                auto.SendKeys('{Alt}{F4}')


def main_workflow():
    applet_window = get_applet_window()
    if not applet_window:
        print("Applet window not found.")
        return

    extractor = ChageeOCRExtractor()
    all_results = []

    # 1. Scrape the first city (implicitly current)
    initial_res = scrape_city_stores(applet_window, extractor, config.DEFAULT_TARGET_COUNT, "上海")
    now = datetime.now()
    for r in initial_res:
        r['Date'] = now.strftime("%Y-%m-%d")
        r['Time'] = now.strftime("%H:%M")
        r['Day'] = now.strftime("%A")
    all_results.extend(initial_res)

    # 2. Move on to rest of cities
    for city_name, target_count, _ in CITY_LIST:
        # Switch City directly 
        if switch_city(city_name, target_count, None):
            # After switching, we are already on the store list page
            city_res = scrape_city_stores(applet_window, extractor, target_count, city_name, click_entry=False)
            now = datetime.now()
            for r in city_res:
                r['Date'] = now.strftime("%Y-%m-%d")
                r['Time'] = now.strftime("%H:%M")
                r['Day'] = now.strftime("%A")
            all_results.extend(city_res)
        else:
            print(f"Failed to switch to {city_name}. Skipping.")

    # Final Export
    if all_results:
        export_data = []
        for r in all_results:
            export_data.append({
                "City": r.get('City', 'Unknown'),
                "Store Name": r['store_name'],
                "Order Status": r['order_status'],
                "Cup Count": r['cup_count'],
                "Date": r.get('Date', ''),
                "Time": r.get('Time', ''),
                "Day": r.get('Day', '')
            })
            
        df_new = pd.DataFrame(export_data)
        output_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "multi_city_stores.xlsx")
        
        if os.path.exists(output_file):
            try:
                df_old = pd.read_excel(output_file)
                # Append new data to old data
                df_final = pd.concat([df_old, df_new], ignore_index=True)
                print(f"\nAppended {len(df_new)} new results to existing {len(df_old)} records.")
            except Exception as e:
                print(f"Error reading existing file ({e}). Saving new data only.")
                df_final = df_new
        else:
            df_final = df_new
            print(f"\nCreated new output file with {len(df_new)} results.")

        df_final.to_excel(output_file, index=False)
        print(f"Total records in historical file: {len(df_final)}. Saved to {output_file}")
    
    # User Rule: Wrap up and close windows
    close_windows()

if __name__ == "__main__":
    main_workflow()
