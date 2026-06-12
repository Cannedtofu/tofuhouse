import time
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By

c = Options()
c.add_argument('--headless')
c.add_argument('--no-sandbox')
c.add_argument('--disable-gpu')
c.add_argument('--window-size=1920,1080')
c.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=c)
driver.get('https://www.cfmotousa.com/dealer-locator')
time.sleep(10)

soup = driver.page_source
with open('cfmoto_page2.html', 'w', encoding='utf-8') as f:
    f.write(soup)
driver.save_screenshot('cfmoto_debug.png')
print("Screenshot and page source saved.")
try:
    el = driver.find_element(By.XPATH, '//*[contains(@id, "lad__")]')
    print('Found lad__ element:', el.get_attribute('outerHTML')[:200])
except Exception as e:
    print('Not found:', e)
    
driver.quit()
