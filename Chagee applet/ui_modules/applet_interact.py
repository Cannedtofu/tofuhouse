import uiautomation as auto
import time
import os
import sys
import pandas as pd
from datetime import datetime

# Add parent directory to sys.path to import ocr_extractor
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ocr_extractor import ChageeOCRExtractor

def interact_with_applet(target_count=15):
    print(f"Starting applet interaction sequence. Target: {target_count} stores.")
    
    extractor = ChageeOCRExtractor()
    
    # Locate the applet window
    applet_window = None
    for i in range(5):
        for window in auto.GetRootControl().GetChildren():
            # Applet windows usually have class 'Chrome_WidgetWin_0' and are NOT named '微信'
            if window.ClassName == "Chrome_WidgetWin_0" and window.Name != "微信" and window.Name != "":
                applet_window = window
                break
        if applet_window: break
        time.sleep(1)
        
    if not applet_window:
        print("Cannot find applet window for interaction.")
        return False
        
    applet_window.SetActive()
    rect = applet_window.BoundingRectangle
    print(f"Interacting with applet window '{applet_window.Name}' at {rect}")

    # 1. Click (120, 600) leads to the scrolling page
    click1_x = rect.left + 120
    click1_y = rect.top + 600
    print(f"Clicking at relative (120, 600) -> Global ({click1_x}, {click1_y})")
    auto.Click(click1_x, click1_y)
    
    # Wait for page transition
    time.sleep(5)
    
    # 2. Initial scroll at (200, 566) to set position
    scroll_x = rect.left + 200
    scroll_y = rect.top + 566
    print(f"Initial scroll at ({scroll_x}, {scroll_y})")
    auto.MoveTo(scroll_x, scroll_y)
    auto.WheelDown(wheelTimes=3, interval=0.1)
    time.sleep(2)
    print("Initial scroll complete. Starting Scrape Loop...")

    all_scraped_stores = {} # Store Name -> Full Record
    consecutive_no_new = 0
    max_retries = 10
    
    while len(all_scraped_stores) < target_count and consecutive_no_new < max_retries:
        # Capture current screen
        screenshot_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "temp_scrape.png")
        applet_window.CaptureToImage(screenshot_path)
        
        # Run OCR
        results = extractor.extract_data(screenshot_path)
        
        new_found_this_iter = 0
        for res in results:
            name = res['store_name']
            if name not in all_scraped_stores:
                all_scraped_stores[name] = res
                new_found_this_iter += 1
                print(f"  [+] Scraped: {name} | {res['order_status']}")
        
        if new_found_this_iter > 0:
            consecutive_no_new = 0
            print(f"Progress: {len(all_scraped_stores)}/{target_count}")
        else:
            consecutive_no_new += 1
            print(f"No new stores found ({consecutive_no_new}/{max_retries}). Scrolling to find more...")

        if len(all_scraped_stores) >= target_count:
            break
            
        # Scroll down - Increased distance by 3x (from 2 to 6)
        auto.MoveTo(scroll_x, scroll_y)
        auto.WheelDown(wheelTimes=5, interval=0.1)
        time.sleep(2) # Wait for UI to settle

    print(f"\nScraping complete. Total stores found: {len(all_scraped_stores)}")
    return list(all_scraped_stores.values())

if __name__ == "__main__":
    results = interact_with_applet(15)
    if results:
        print("\nSummary of Scraped Data:")
        for r in results:
            print(f"- {r['store_name']}: {r['order_status']}")
            
        # Export logic
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M")
        day_str = now.strftime("%A")
        
        export_data = []
        for r in results:
            export_data.append({
                "Store Name": r['store_name'],
                "Order Status": r['order_status'],
                "Cup Count": r['cup_count'],
                "Date": date_str,
                "Time": time_str,
                "Day": day_str
            })
            
        df = pd.DataFrame(export_data)
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        output_file = os.path.join(base_dir, "scraped_stores.xlsx")
        df.to_excel(output_file, index=False)
        print(f"\nResults successfully exported to {output_file}")
