from scraper import setup_driver
import time
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys

d=setup_driver()
d.get('https://www.popmart.com/us/store-list')
time.sleep(8)
try:
    # Find the search input
    search_input = d.find_element(By.CSS_SELECTOR, 'input[placeholder*=\"Search\" i], input[type=\"text\"]')
    print("Found search input!")
    search_input.clear()
    search_input.send_keys("Paris")
    search_input.send_keys(Keys.ENTER)
    
    time.sleep(5) # wait for API call and render
    
    stores = d.find_elements(By.CSS_SELECTOR, '.index_listItem__ea7bq')
    print('Total stores after searching Paris:', len(stores))
    texts = [s.text.split('\n')[0].lower() for s in stores]
    print('Paris found?', any('paris' in t for t in texts))
    print(texts[:5])
except Exception as e:
    print('error', e)
d.quit()
