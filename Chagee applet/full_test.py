import time
import sys
import os

# Add relevant paths
sys.path.append(os.path.join(os.getcwd(), "ui_modules"))

from ui_modules.core_wechat import focus_wechat_and_open_search
from ui_modules.applet_nav import search_and_open_applet
from ui_modules.applet_interact import main_workflow

def full_back_to_back_test():
    print("=== Starting Full Back-to-Back Test ===")
    
    # 1. Focus WeChat and Open Search
    if not focus_wechat_and_open_search():
        print("Failed to focus WeChat or open search bar.")
        return

    # 2. Search for the applet and open it
    applet_name = "霸王茶姬小程序"
    applet_window = search_and_open_applet(applet_name)
    
    if not applet_window:
        print(f"Failed to find or open applet: {applet_name}")
        return
        
    print(f"Successfully opened {applet_name}. Starting scraping workflow...")
    time.sleep(5) # Final buffer for applet to settle
    
    # 3. Main Scraping Workflow (Scrapes initial city + CITY_LIST)
    main_workflow()
    
    print("=== Full Back-to-Back Test Completed ===")

if __name__ == "__main__":
    full_back_to_back_test()
