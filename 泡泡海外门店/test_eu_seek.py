from scraper import setup_driver, handle_popups
import time
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains

d = setup_driver()
d.get("https://www.popmart.com/us/store-list")
handle_popups(d)
wait = WebDriverWait(d, 15)
map_canvas = wait.until(EC.presence_of_element_located((By.XPATH, "//div[@aria-label='地图' or @aria-label='Map']")))
d.execute_script("arguments[0].scrollIntoView();", map_canvas)
actions = ActionChains(d)

for i in range(15):
    print(f"Iteration {i}")
    actions.move_to_element_with_offset(map_canvas, 600, 0).click_and_hold().move_by_offset(-600, 0).release().perform()
    time.sleep(2)
    stores = d.find_elements(By.CSS_SELECTOR, ".index_listItem__ea7bq")
    
    # Let's dump all stores that show up in the current list
    first_store = stores[0].text.split('\n')[0] if stores else "none"
    last_store  = stores[-1].text.split('\n')[0] if stores else "none"
    print(f"Viewport stores: {len(stores)}. First [{first_store}], Last [{last_store}]")
    
    # Are we in Europe yet? 
    # Let's check for 'uk', 'france', 'london', 'paris', etc. in the raw text of the first or any store.
    all_text = " ".join([s.text for s in stores]).lower()
    if 'uk ' in all_text or 'london' in all_text or 'paris' in all_text or 'france' in all_text:
        print("Found Europe at iteration", i)
        break
d.quit()
