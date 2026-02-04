import os
import time
from datetime import datetime
import pandas as pd
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

# --- Setup output folder ---
base_folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(base_folder, exist_ok=True)

timestamp = datetime.now().strftime("%Y%m%d")
html_file_path = os.path.join(base_folder, f"popmart_us_html_{timestamp}.txt")
excel_file_path = os.path.join(base_folder, f"popmart_us_stores_{timestamp}.xlsx")

# --- Selenium setup ---
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

# Wait for and click the "United States" button
wait = WebDriverWait(driver, 60)
us_button = wait.until(
    EC.element_to_be_clickable((By.CLASS_NAME, "index_ipInConutry__BoVSZ"))
)
us_button.click()

# Wait for store list to load (can replace with explicit waits for better reliability)
time.sleep(60)
html = driver.page_source

# Save HTML file
with open(html_file_path, "w", encoding="utf-8") as f:
    f.write(html)

# --- Parse HTML with BeautifulSoup ---
soup = BeautifulSoup(html, "html.parser")
store_elements = soup.find_all("div", class_="index_listItem__3o63q")

store_data = []

for el in store_elements:
    try:
        name = el.find("div", class_="index_title__LfKGU").get_text(strip=True)
    except AttributeError:
        name = None

    try:
        address = el.find("div", class_="index_local__Qjnu6").get_text(strip=True)
    except AttributeError:
        address = None

    try:
        # State is the last part of the address after the comma
        state = address.split(",")[-2].strip() if address else None
    except:
        state = None

    try:
        img_tag = el.find("img", class_="ant-image-img")
        img_url = img_tag["src"] if img_tag else None
    except:
        img_url = None

    # Hours
    hours_list = []
    try:
        hours_elements = el.find_all("div", class_="index_timeItem__vEsfc")
        for h in hours_elements:
            day = h.find("span").get_text(strip=True)
            time_range = h.get_text(strip=True).replace(day, "")
            hours_list.append(f"{day}: {time_range}")
        hours_str = "; ".join(hours_list)
    except:
        hours_str = None

    store_data.append({
        "Name": name,
        "Address": address,
        "State": state,
        "Hours": hours_str,
        "Image_URL": img_url
    })

# Convert to DataFrame and save Excel
df = pd.DataFrame(store_data)
df.to_excel(excel_file_path, index=False)

print(f"✅ HTML saved to: {html_file_path}")
print(f"✅ Excel saved to: {excel_file_path}")

driver.quit()
