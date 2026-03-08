import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from scraper import setup_driver, handle_popups

def test_eu_map_pan():
    print("Testing map manipulation to load EU stores...")
    # Open the browser
    driver = setup_driver()
    driver.get("https://www.popmart.com/us/store-list")
    
    # Handle the privacy/region popups
    handle_popups(driver)
    
    wait = WebDriverWait(driver, 15)
    
    try:
        # Wait for the map canvas to appear
        map_canvas = wait.until(EC.presence_of_element_located((By.XPATH, "//div[@aria-label='地图' or @aria-label='Map']")))
        print("Map canvas located.")
        
        # At this point, the map is focused on the US. To move to Europe securely without API 
        # forging, we use ActionChains to click and drag the map multiple times to the East.
        
        actions = ActionChains(driver)
        
        # We need to drag from the right side of the map to the left side multiple times
        # to pan across the Atlantic.
        for i in range(5):
            print(f"Panning map eastwards (Iteration {i+1})...")
            # Move to the right side of the canvas, click, hold, move left, release
            actions.move_to_element_with_offset(map_canvas, 600, 0) \
                   .click_and_hold() \
                   .move_by_offset(-500, 0) \
                   .release() \
                   .perform()
            time.sleep(2) # Allow network request to fire and render new markers
            
        print("Finished panning. Waiting for the list to update...")
        time.sleep(5)
        
        target_class = "index_listItem__ea7bq"
        store_elements = driver.find_elements(By.CSS_SELECTOR, f"div.{target_class}")
        print(f"Found {len(store_elements)} store items in the list viewport.")
        
        if len(store_elements) > 0:
            print(f"Sample resulting store: {store_elements[0].text.strip().split(char(10))[0]}")
            
    except Exception as e:
        print(f"Map test failed: {e}")
    finally:
        driver.quit()

if __name__ == "__main__":
    test_eu_map_pan()
