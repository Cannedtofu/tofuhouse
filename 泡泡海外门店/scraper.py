import os
import json
import time
import re
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.keys import Keys

def setup_driver():
    """Initializes and returns a Chrome WebDriver instance."""
    options = webdriver.ChromeOptions()
    # Configure options for stability
    options.add_argument('--window-size=1920,1080')
    options.add_argument('--disable-gpu')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    
    # Uncomment the next line to run headlessly (without opening browser window visibly)
    # options.add_argument('--headless')
    
    # Initialize the Chrome driver using webdriver_manager
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    return driver

def handle_popups(driver):
    """
    Attempts to identify and close common popups such as region/location
    confirmations or promotional modals.
    """
    print("Checking for location confirmation or promotional popups...")
    try:
        # Give the page a moment to render any popups
        time.sleep(3)
        
        # XPaths targeting common confirmation/dismissal buttons and close icons
        popup_xpaths = [
            # Popmart specific IP Location popups (Found in HTML dump)
            "//div[@role='dialog']//img[@alt='close']",
            "//div[contains(@class, 'index_ipInConutry')]",
            "//div[contains(@class, 'index_chooseCountry')]",
            # Region selection confirmations
            "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'confirm')]",
            "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'continue')]",
            "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'stay')]",
            # Privacy & Cookie Banners (sometimes implemented as clickable divs)
            "//div[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'accept')]",
            "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'accept')]",
            "//div[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'agree')]",
            "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'agree')]",
            # Generic dialog submit/close buttons
            "//div[contains(@class, 'dialog')]//button",
            "//div[contains(@class, 'modal')]//button",
            # Common close icons (either divs, buttons, or svgs with 'close' in class)
            "//*[contains(@class, 'close')]",
        ]
        
        for xpath in popup_xpaths:
            elements = driver.find_elements(By.XPATH, xpath)
            for el in elements:
                try:
                    if el.is_displayed() and el.is_enabled():
                        text_hint = el.text.strip() if el.text else "close icon"
                        print(f"Found potential popup element ({text_hint}), attempting to click...")
                        # Use JavaScript click to bypass some interactability exceptions
                        driver.execute_script("arguments[0].click();", el)
                        time.sleep(1) # wait a moment after clicking
                except Exception:
                    pass
        print("Finished checking popups.")
    except Exception as e:
        print(f"Warning during popup handling: {e}")

def parse_store_data(driver, el, region, scrape_date):
    text_content = driver.execute_script("return arguments[0].innerText;", el)
    html_content = driver.execute_script("return arguments[0].innerHTML;", el)
    
    text_str = text_content.strip() if text_content else ""
    html_str = html_content.strip() if html_content else ""
    text_lower = text_str.lower()
    
    country = "US" if region == "US" else "Unknown"
    if region == "Europe":
        eu_countries = {
            "uk": "UK", "united kingdom": "UK", "london": "UK",
            "france": "France", "paris": "France",
            "netherlands": "Netherlands", "amsterdam": "Netherlands",
            "italy": "Italy", "milan": "Italy",
            "germany": "Germany", "spain": "Spain"
        }
        for key, val in eu_countries.items():
            if re.search(r'\b' + key + r'\b', text_lower):
                country = val
                break
                
    return {
        "text": text_str,
        "html": html_str,
        "region": region,
        "country": country,
        "date_of_scrap": scrape_date
    }

def scrape_stores():
    driver = setup_driver()
    wait = WebDriverWait(driver, 15)
    
    try:
        url = "https://www.popmart.com/us/store-list"
        print(f"Navigating to {url} ...")
        driver.get(url)
        
        # Handle popups that might block the content
        handle_popups(driver)
        
        target_class = "index_listItem__ea7bq"
        print(f"Waiting for store items (class: {target_class}) to appear...")
        
        try:
            # Wait until at least one store listing is present
            wait.until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, f"div.{target_class}"))
            )
            
            # Zoom out 1 level to get a broader US store list
            print("Zooming out 1 level before scraping US stores to include regional stores...")
            try:
                map_canvas = wait.until(EC.presence_of_element_located((By.XPATH, "//div[@aria-label='地图' or @aria-label='Map']")))
                driver.execute_script("arguments[0].scrollIntoView();", map_canvas)
                zoom_out = driver.find_element(By.XPATH, "//button[@title='Zoom out' or @aria-label='Zoom out' or @title='缩小' or @aria-label='缩小']")
                driver.execute_script('arguments[0].click();', zoom_out)
                print("Zoomed out 1 level.")
                time.sleep(4) # Allow list to update
            except Exception as e:
                print("Could not find zoom out button, proceeding without zoom:", e)

            # Since elements might load lazily, scroll to bottom to ensure all load
            print("Scrolling to load US stores...")
            last_height = driver.execute_script("return document.body.scrollHeight")
            while True:
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(2)
                new_height = driver.execute_script("return document.body.scrollHeight")
                if new_height == last_height:
                    break
                last_height = new_height
                
            scrape_date = datetime.now().strftime('%Y-%m-%d')

            # Parse US stores
            print("Extracting US store data...")
            store_elements_us = driver.find_elements(By.CSS_SELECTOR, f"div.{target_class}")
            us_stores_data = []
            for el in store_elements_us:
                us_stores_data.append(parse_store_data(driver, el, "US", scrape_date))
            print(f"Scraped {len(us_stores_data)} US stores.")

            # Pan Map to Europe
            print("Panning map to Europe to load EU stores...")
            eu_stores_data = []
            try:
                map_canvas = wait.until(EC.presence_of_element_located((By.XPATH, "//div[@aria-label='地图' or @aria-label='Map']")))
                driver.execute_script("arguments[0].scrollIntoView();", map_canvas)
                
                # Pan Eastwards to reach Europe
                actions = webdriver.ActionChains(driver)
                for i in range(8):
                    print(f"Panning eastwards ({i+1}/8)...")
                    actions.move_to_element_with_offset(map_canvas, 600, 0) \
                           .click_and_hold() \
                           .move_by_offset(-600, 0) \
                           .release() \
                           .perform()
                    time.sleep(1.5) # Allow network requests
                
                # Pan North to center Europe
                print("Panning north to center Europe...")
                actions.move_to_element(map_canvas) \
                       .click_and_hold() \
                       .move_by_offset(0, 200) \
                       .release() \
                       .perform()
                time.sleep(2)

                print("Waiting for EU list to settle after panning...")
                time.sleep(5) # Wait for final load
                
                # Take a screenshot for testing purposes
                screenshot_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eu_map_view.png")
                driver.save_screenshot(screenshot_path)
                print(f"Saved EU map view screenshot to {screenshot_path}")

                # Since list is updated in-place, grab new elements
                store_elements_eu = driver.find_elements(By.CSS_SELECTOR, f"div.{target_class}")
                for el in store_elements_eu:
                    eu_stores_data.append(parse_store_data(driver, el, "Europe", scrape_date))
                    
                print(f"Scraped {len(eu_stores_data)} potential EU stores.")
            except Exception as e:
                print(f"Failed to pan and scrape EU stores: {e}")

            # Combine lists and assign generic incrementing IDs, avoiding duplicates
            stores_data = []
            seen_texts = set()
            idx = 1
            for store in us_stores_data + eu_stores_data:
                # Deduplicate based on the store name (first line of text)
                store_key = store["text"].split("\n")[0].strip() if store["text"] else str(idx)
                if store_key not in seen_texts:
                    seen_texts.add(store_key)
                    store["id"] = idx
                    stores_data.append(store)
                    idx += 1
                
            # Output the scraped data 
            output_dir = os.path.dirname(os.path.abspath(__file__))
            output_file = os.path.join(output_dir, "stores_data.json")
            
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(stores_data, f, ensure_ascii=False, indent=4)
                
            print(f"Total scraping completed! Found {len(stores_data)} stores. Data saved to: {output_file}")
            return stores_data
            
        except TimeoutException:
            print("Timeout waiting for store listings. A popup might still be blocking, or the class name may be incorrect/changed.")
            
            # Create a debug dump
            debug_dir = os.path.dirname(os.path.abspath(__file__))
            screenshot_path = os.path.join(debug_dir, "error_screenshot.png")
            source_path = os.path.join(debug_dir, "error_source.html")
            
            driver.save_screenshot(screenshot_path)
            with open(source_path, "w", encoding='utf-8') as f:
                f.write(driver.page_source)
                
            print(f"Saved debug info to {screenshot_path} and {source_path}")
            
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
    finally:
        print("Closing browser...")
        driver.quit()

if __name__ == "__main__":
    scrape_stores()
