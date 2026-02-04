from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
import time

# Chrome options
chrome_options = Options()
chrome_options.add_argument("--window-size=1920,1080")
chrome_options.add_argument(
    "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
)
chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
chrome_options.add_experimental_option("useAutomationExtension", False)

driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)

url = "https://www.popmart.com/us/store-list"
driver.get(url)

# Wait for the country selection button to appear
wait = WebDriverWait(driver, 20)
us_button = wait.until(
    EC.element_to_be_clickable((By.CLASS_NAME, "index_ipInConutry__BoVSZ"))
)

# Click the "United States" button
us_button.click()

# Wait for the store list to load after clicking
time.sleep(60)  # or use WebDriverWait for a specific element in the store list

html = driver.page_source
print(html)

target = "ROBO SHOP Stonestown 2F"
if target in html:
    print(f"✅ Found '{target}' in the rendered HTML.")
else:
    print(f"❌ '{target}' not found in the rendered HTML.")

driver.quit()