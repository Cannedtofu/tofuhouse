from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
import time

chrome_options = Options()

driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)

url = "https://www.popmart.com/us/store-list"
driver.get(url)

time.sleep(60)

html = driver.page_source
print(html)

target = "ROBO SHOP Stonestown 2F"
if target in html:
    print(f"✅ Found '{target}' in the rendered HTML.")
else:
    print(f"❌ '{target}' not found in the rendered HTML.")

driver.quit()
