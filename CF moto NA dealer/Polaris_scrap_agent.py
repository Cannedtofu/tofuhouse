from bs4 import BeautifulSoup
import pandas as pd
import time
import logging
import os
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def setup_driver():
    """Initialize and return a Chrome WebDriver with optimized settings"""
    chrome_options = Options()
    
    # Essential options for stability
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--start-maximized")
    
    # Anti-detection measures
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    
    # SSL and certificate handling
    chrome_options.add_argument("--ignore-certificate-errors")
    chrome_options.add_argument("--ignore-ssl-errors")
    chrome_options.add_argument("--allow-running-insecure-content")
    
    # User agent
    chrome_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    driver = None
    try:
        # Try using webdriver-manager first (recommended)
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        logger.info("Driver initialized with webdriver-manager")
    except Exception as e:
        logger.warning(f"webdriver-manager failed: {e}")
        try:
            # Fallback to local driver path
            service = Service(r'C:\Program Files\python chrome\chrome-win\chromedriver.exe')
            driver = webdriver.Chrome(service=service, options=chrome_options)
            logger.info("Driver initialized with local chromedriver")
        except Exception as e:
            logger.error(f"Failed to initialize driver: {e}")
            raise
    
    # Configure timeouts
    driver.set_page_load_timeout(30)
    driver.implicitly_wait(10)
    
    # Execute script to hide automation indicators
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    
    return driver

def safe_get_page(driver, url):
    """Safely get a page with retry logic"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            driver.get(url)
            # Wait for body to ensure page load
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            return True
        except TimeoutException:
            logger.warning(f"Timeout loading {url} on attempt {attempt + 1}")
        except Exception as e:
            logger.error(f"Error loading {url}: {e}")
        
        if attempt < max_retries - 1:
            time.sleep(2)
            
    return False

def scrape_polaris_dealers():
    driver = setup_driver()
    all_dealers = []
    
    try:
        base_url = "https://www.polaris.com"
        start_url = "https://www.polaris.com/en-us/off-road/dealers/"
        
        logger.info(f"Starting scrape at {start_url}")
        if not safe_get_page(driver, start_url):
            logger.error("Failed to load main page. Exiting.")
            return

        # Parse main page for states
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        # Find state links - looking for 'state' class
        state_links = soup.find_all('a', class_="state")
        
        state_urls = []
        for link in state_links:
            href = link.get('href')
            name = link.get_text(strip=True)
            if href:
                full_url = base_url + href if href.startswith('/') else href
                state_urls.append((name, full_url))
        
        # Remove duplicates
        state_urls = list(set(state_urls))
        logger.info(f"Found {len(state_urls)} states.")
        
        # Iterate over states
        # SAMPLE TEST: Limit to first 3 states
        for state_name, state_url in state_urls[:3]:
            logger.info(f"Scraping state: {state_name}")
            
            if not safe_get_page(driver, state_url):
                logger.warning(f"Skipping state {state_name} due to load failure.")
                continue
            
            # Parse dealers in state
            state_soup = BeautifulSoup(driver.page_source, 'html.parser')
            dealer_items = state_soup.find_all('div', class_="dealer-item")
            
            if not dealer_items:
                logger.info(f"No dealers found in {state_name}")
                continue
                
            for dealer in dealer_items:
                try:
                    # Extract details
                    name_tag = dealer.find('div', class_='dealer-name')
                    name = name_tag.get_text(strip=True) if name_tag else "N/A"
                    
                    link_tag = dealer.find('a')
                    dealer_href = link_tag['href'] if link_tag else ""
                    dealer_url = base_url + dealer_href if dealer_href.startswith('/') else dealer_href
                    
                    city_tag = dealer.find('a', class_='dealer-city')
                    city = city_tag.get_text(strip=True) if city_tag else "N/A"
                    
                    phone_tag = dealer.find('div', class_='pull-left margin-right-xs')
                    phone = phone_tag.get_text(strip=True) if phone_tag else "N/A"
                    
                    # Visit dealer page for details
                    address = "N/A"
                    brands = "N/A"
                    
                    if dealer_url:
                        if safe_get_page(driver, dealer_url):
                            detail_soup = BeautifulSoup(driver.page_source, 'html.parser')
                            
                            # Extract detailed address
                            addr_tag = detail_soup.find('address')
                            if addr_tag:
                                address = " ".join(addr_tag.stripped_strings)
                            
                            # Extract brands
                            brand_tags = detail_soup.find_all(class_="dealer-listing_brand")
                            if brand_tags:
                                brands = ", ".join([b.get_text(strip=True) for b in brand_tags])
                    
                    all_dealers.append({
                        "State": state_name,
                        "Dealer Name": name,
                        "Address": address,
                        "City": city,
                        "Phone": phone,
                        "URL": dealer_url,
                        "Brands": brands
                    })
                    
                except Exception as e:
                    logger.error(f"Error parsing dealer item: {e}")
            
            # Small delay to be polite
            time.sleep(1)
            
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        
    finally:
        driver.quit()
        
        # Save results
        if all_dealers:
            output_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Polaris_Dealers_Output.xlsx")
            try:
                df = pd.DataFrame(all_dealers)
                df.to_excel(output_file, index=False)
                logger.info(f"Saved {len(all_dealers)} dealers to {output_file}")
            except Exception as e:
                logger.error(f"Failed to save Excel: {e}")
                print(all_dealers)
        else:
            logger.warning("No dealers scraped.")

if __name__ == "__main__":
    scrape_polaris_dealers()