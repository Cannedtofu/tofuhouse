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

# Zoom out 1 level
try:
    zoom_out = d.find_element(By.XPATH, "//button[@title='Zoom out' or @aria-label='Zoom out' or @title='缩小' or @aria-label='缩小']")
    d.execute_script('arguments[0].click();', zoom_out)
    print("Zoomed out 1 level.")
    time.sleep(2)
except Exception as e:
    print("Could not find zoom out:", e)

actions = ActionChains(d)
# We need to pan enough to reach Europe
for i in range(8):
    print(f"Panning eastwards ({i+1}/8)...")
    actions.move_to_element_with_offset(map_canvas, 600, 0) \
           .click_and_hold() \
           .move_by_offset(-600, 0) \
           .release() \
           .perform()
    time.sleep(1.5)

stores = d.find_elements(By.CSS_SELECTOR, ".index_listItem__ea7bq")
texts = [s.text.split('\n')[0].lower() for s in stores]
print("Total stores visible globally after panning:", len(stores))
print("EU found?", any('uk' in t or 'paris' in t or 'france' in t for t in texts))
if len(stores) > 0:
    print("First store:", texts[0])
    print("Last store:", texts[-1])

d.quit()
