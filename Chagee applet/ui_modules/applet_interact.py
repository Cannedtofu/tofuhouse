import uiautomation as auto
import time
import os
import sys
import pandas as pd
from datetime import datetime

# Add parent directory to sys.path to import ocr_extractor and config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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


def scrape_city_stores(applet_window, extractor, target_count=15, city_name="Default", click_entry=True):
    print(f"\n--- Scraping City: {city_name} (Target: {target_count}) ---")
    applet_window.SetActive()
    time.sleep(1)
    rect = applet_window.BoundingRectangle
    
    if click_entry:
        # 1. Click (120, 600) leads to the scrolling page
        click1_x = rect.left + 120
        click1_y = rect.top + 600
        print(f"Clicking at relative (120, 600) -> Global ({click1_x}, {click1_y})")
        auto.Click(click1_x, click1_y)
        time.sleep(5)
    else:
        print("Skipping entry click (120, 600) as requested.")
    
    # 2. Initial scroll
    scroll_x = rect.left + 200
    scroll_y = rect.top + 566
    auto.MoveTo(scroll_x, scroll_y)
    auto.WheelDown(wheelTimes=3, interval=0.1)
    time.sleep(2)

    city_results = {}
    consecutive_no_new = 0
    
    while len(city_results) < target_count and consecutive_no_new < 10:
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
            
        if len(city_results) >= target_count: break
            
        auto.MoveTo(scroll_x, scroll_y)
        auto.WheelDown(wheelTimes=5, interval=0.1)
        time.sleep(2)

    print(f"Scraped {len(city_results)} stores in {city_name}.")
    return list(city_results.values())

def main_workflow():
    applet_window = get_applet_window()
    if not applet_window:
        print("Applet window not found.")
        return

    extractor = ChageeOCRExtractor()
    all_results = []

    # 1. Scrape the first city (implicitly current)
    all_results.extend(scrape_city_stores(applet_window, extractor, 15, "Initial City"))

    # 2. Move on to rest of cities
    for city_name, target_count, _ in CITY_LIST:
        # Switch City directly (this begins with a click at 50, 395 now via Search Store)
        if switch_city(city_name, target_count, None):
            # After switching, we are already on the store list page
            all_results.extend(scrape_city_stores(applet_window, extractor, target_count, city_name, click_entry=False))
        else:
            print(f"Failed to switch to {city_name}. Skipping.")

    # Final Export
    if all_results:
        now = datetime.now()
        export_data = []
        for r in all_results:
            export_data.append({
                "City": r.get('City', 'Unknown'),
                "Store Name": r['store_name'],
                "Order Status": r['order_status'],
                "Cup Count": r['cup_count'],
                "Date": now.strftime("%Y-%m-%d"),
                "Time": now.strftime("%H:%M"),
                "Day": now.strftime("%A")
            })
            
        df = pd.DataFrame(export_data)
        output_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "multi_city_stores.xlsx")
        df.to_excel(output_file, index=False)
        print(f"\nAll cities scraped. Total: {len(all_results)} results. Saved to {output_file}")

if __name__ == "__main__":
    main_workflow()
