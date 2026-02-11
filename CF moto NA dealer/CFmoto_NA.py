from lxml import html
import pandas as pd
import os
import time
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager

def scrape_cfmoto_dealers():
    """
    Scrapes dealer information from the CFMoto USA website.
    """
    url = "https://www.cfmotousa.com/dealer-locator"
    xpath = "//ul[@id='lad__location-list']"

    data = []

    # Setup Chrome options
    chrome_options = Options()
    chrome_options.add_argument("--start-maximized")
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

    # Initialize WebDriver
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)

    try:
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"})
        driver.get(url)
        
        # Check for Cloudflare and wait for manual solution if needed
        if "Cloudflare" in driver.title or "Attention Required" in driver.title:
            print("Cloudflare challenge detected. Waiting 30s for manual resolution or auto-reload...")
            time.sleep(30)
        else:
            time.sleep(10)  # Wait for page and dynamic content to load
        
        print(f"Page Title: {driver.title}")
        print(f"Page Source Length: {len(driver.page_source)}")

        # Debug: Save page source to inspect structure
        with open("debug_page_source.html", "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        print("Debug: Page source saved to 'debug_page_source.html'. Please check this file to verify HTML structure.")

        tree = html.fromstring(driver.page_source)
        
        dealer_list = tree.xpath(xpath)
        print(f"XPath content: {dealer_list}")

        if not dealer_list:
            print("Could not find the dealer list element using the provided XPath.")
            print("The website structure might have changed.")
            return

        ul_element = dealer_list[0]
        
        for li in ul_element.xpath('./li'):
            name = li.xpath('.//h4/text()')
            name = name[0].strip() if name else 'N/A'

            if name == 'N/A':
                continue

            # Updated address XPath: ./div/span
            address_parts = li.xpath('./div/span//text()')
            address = ' '.join([part.strip() for part in address_parts if part.strip()])
            
            # Scrape everything under ./div/ul[1]
            details_parts = li.xpath('./div/ul[1]//text()')
            details = ', '.join([part.strip() for part in details_parts if part.strip()])

            print(f"Dealer: {name}")
            print(f"Address: {address}")
            print(f"Details: {details}")
            print("-" * 20)
            
            data.append({
                "Dealer Name": name,
                "Address": address,
                "Details": details
            })

        if data:
            df = pd.DataFrame(data)
            current_dir = os.path.dirname(os.path.abspath(__file__))
            output_path = os.path.join(current_dir, "CFMoto_Dealers.xlsx")
            df.to_excel(output_path, index=False)
            print(f"Data successfully saved to: {output_path}")

    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        driver.quit()

if __name__ == "__main__":
    scrape_cfmoto_dealers()
