from scraper import setup_driver
import time
from selenium.webdriver.common.by import By

d=setup_driver()
d.get('https://www.popmart.com/us/store-list')
time.sleep(8)
try:
    zoom_out = d.find_element(By.XPATH, "//button[@title='Zoom out' or @aria-label='Zoom out' or @title='缩小' or @aria-label='缩小']")
    print("Found zoom out button!")
    for i in range(8):
        d.execute_script('arguments[0].click();', zoom_out)
        time.sleep(1)
        print("Zoomed out via JS.")
    
    time.sleep(5) # wait for API call and render
    
    stores = d.find_elements(By.CSS_SELECTOR, '.index_listItem__ea7bq')
    print('Total stores after JS zoom out:', len(stores))
    texts = [s.text.split('\n')[0].lower() for s in stores]
    print('EU found?', any('uk' in t or 'paris' in t or 'france' in t for t in texts))
    if len(stores) > 0:
        print("First store:", texts[0])
        print("Last store:", texts[-1])
except Exception as e:
    print('error', e)
d.quit()
