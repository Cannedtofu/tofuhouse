# This script requires four libraries to be installed:
# 1. selenium: pip install selenium
# 2. webdriver-manager: pip install webdriver-manager
# 3. pandas and openpyxl (for XLSX export): pip install pandas openpyxl
# 4. Email libraries: often built-in, but requires smtplib

import time
import os
import re
from datetime import datetime
from typing import Dict, Any, List, Tuple

# === Email Specific Imports Re-enabled ===
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
# ==========================================

# Data handling imports
import pandas as pd
from openpyxl import load_workbook

# Selenium imports
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.common.exceptions import NoSuchElementException

# --- GLOBAL SELECTORS FOR DAMAI (大麦) ---
ALL_ITEMS_SELECTOR = ".bui-dm-show-card.log-show-card-item" 
TITLE_SELECTOR = '.show-title'
DATE_SELECTOR = '.show-date'
CITY_SELECTOR = '.show-city'
VENUE_SELECTOR = '.show-theatre'
PRICE_SELECTOR = '.price-count'
TAGS_SELECTOR = '.show-tags .tag-item'
IMAGE_SELECTOR = '.bui-image.poster-img img'
XLSX_PATH = r"D:\代码项目\scraped_damai_data.xlsx"

# CRITICAL FIX: Selector for the internal element that handles the scroll
SCROLL_CONTAINER_SELECTOR = '.bui-scroll.bui-scroll-view-scroll-y' 
# ------------------------------------------

def safe_extract_text(root: Any, selector: str, default: str = "") -> str:
    """Safely extracts text from an element, returning default if not found."""
    try:
        return root.find_element(By.CSS_SELECTOR, selector).text
    except NoSuchElementException:
        return default

def safe_extract_attr(root: Any, selector: str, attr: str, default: str = "") -> str:
    """Safely extracts an attribute value from an element, returning default if not found."""
    try:
        return root.find_element(By.CSS_SELECTOR, selector).get_attribute(attr)
    except NoSuchElementException:
        return default


def extract_data_from_element(root_element: Any, index: int) -> Dict[str, Any]:
    """
    Extracts specific data fields from a single event item element using DAMAI SELECTORS.
    """
    data = {"index": index, "fetch_timestamp": datetime.now().strftime("%Y-%m-%d")}
    
    try:
        # --- CRITICAL EXTRACTION: Event Name ---
        data["event_name"] = safe_extract_text(root_element, TITLE_SELECTOR, default="[NAME_MISSING]")
        if data["event_name"] == "[NAME_MISSING]":
            raise NoSuchElementException("Critical element (event name) missing.")

        # --- NON-CRITICAL EXTRACTIONS ---
        data["image_url"] = safe_extract_attr(root_element, IMAGE_SELECTOR, 'src')
        data["city"] = safe_extract_text(root_element, CITY_SELECTOR)
        data["venue"] = safe_extract_text(root_element, VENUE_SELECTOR)
        data["date"] = safe_extract_text(root_element, DATE_SELECTOR)
        
        try:
            tag_elements = root_element.find_elements(By.CSS_SELECTOR, TAGS_SELECTOR)
            data["tags"] = "|".join([tag.text for tag in tag_elements])
        except Exception:
            data["tags"] = ""
        
        price_text = safe_extract_text(root_element, PRICE_SELECTOR)
        cleaned_price = re.sub(r'[^\d]', '', price_text)
        data["price_cny"] = int(cleaned_price) if cleaned_price else None

        data["provider"] = "Damai"
        data["artist"] = safe_extract_text(root_element, '.show-info-right')
        
        return data

    except NoSuchElementException as e:
        print(f"WARNING: Critical data element not found for index {index}. Skipping. Error: {e}")
        return None
    except Exception as e:
        print(f"ERROR: Could not extract data for index {index}. Reason: {type(e).__name__}: {e}")
        return None


def export_data_to_xlsx(data: List[Dict[str, Any]], filename: str = r"D:\代码项目\scraped_damai_data.xlsx"):
    """
    Exports a list of dictionaries to a true XLSX file using pandas.
    Appends data if the file exists, otherwise creates it with a header.
    """
    if not data:
        print("No data to export.")
        return

    column_order = [
        "index", "fetch_timestamp", "event_name", "artist", "date", 
        "city", "venue", "price_cny", "tags", "provider", "image_url"
    ]

    new_df = pd.DataFrame(data, columns=column_order)
    
    try:
        if os.path.exists(filename):
            print(f"Appending data to existing XLSX file: '{filename}'.")
            existing_df = pd.read_excel(filename)
            combined_df = pd.concat([existing_df, new_df], ignore_index=True)
            combined_df.to_excel(filename, index=False)
            print(f"Successfully appended {len(data)} records to '{filename}'.")
        else:
            print(f"Creating new XLSX file: '{filename}'.")
            new_df.to_excel(filename, index=False)
            print(f"Successfully wrote {len(data)} records to '{filename}'.")
            
    except Exception as e:
        print(f"ERROR: Could not write data to XLSX file '{filename}'. Reason: {e}")


def scroll_and_count(driver: WebDriver, target_count: int, timeout: int = 60) -> int:
    """
    Continuously scrolls the internal list element until the desired number of 
    items is present or the timeout is reached.
    """
    print(f"Attempting to load and find {target_count} items...")
    start_time = time.time()
    last_count = 0
    
    scroll_container = None
    try:
        # Find the scrollable container once
        scroll_container = driver.find_element(By.CSS_SELECTOR, SCROLL_CONTAINER_SELECTOR)
        time.sleep(1) # Small wait after finding to ensure stability
    except NoSuchElementException:
        print(f"FATAL SCROLL ERROR: Could not find the internal scroll container ({SCROLL_CONTAINER_SELECTOR}).")
        return 0

    while time.time() - start_time < timeout:
        item_elements = driver.find_elements(By.CSS_SELECTOR, ALL_ITEMS_SELECTOR)
        current_count = len(item_elements)
        
        if current_count >= target_count:
            print(f"Target count of {target_count} reached. Stopping scroll.")
            return current_count
        
        if current_count > last_count:
            last_count = current_count
        
        # Scroll the specific element's internal scrollTop property
        driver.execute_script("arguments[0].scrollTop += 800;", scroll_container)
        time.sleep(0.5) # Reduced wait time for production efficiency
            
    print(f"WARNING: Timeout reached ({timeout}s). Only {last_count} items loaded.")
    return last_count


def generate_email_content(data_list: List[Dict[str, Any]], target_count: int) -> str:
    """Generates the full text content for the email, including a summary and sample data list."""
    
    total_count = len(data_list)
    
    # 1. Summary
    content = f"Scraping Report - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    content += f"----------------------------------------------------\n"
    content += f"Targeted: {target_count} records.\n"
    content += f"Successfully fetched {total_count} event records from Damai.\n\n"
    
    # 2. Sample Data
    content += "SAMPLE DATA RESULTS (First 5 records):\n"
    content += "====================================================\n\n"
    
    for item in data_list[:5]:
        content += f"--- Event Index {item.get('index', 'N/A')} ---\n"
        content += f"Name: {item.get('event_name', 'N/A')}\n"
        content += f"Artist: {item.get('artist', 'N/A')}\n"
        content += f"Date: {item.get('date', 'N/A')}\n"
        content += f"Location: {item.get('city', 'N/A')} / {item.get('venue', 'N/A')}\n"
        content += f"Price (CNY): {item.get('price_cny', 'N/A')}\n"
        content += "----------------------------------------------------\n\n"
        
    if total_count > 5:
        content += f"... and {total_count - 5} more records (see attached XLSX file: scraped_damai_data.xlsx).\n"
        
    return content


def run_scraper(url: str, target_count: int = 251) -> Tuple[List[Dict[str, Any]], str, str]:
    """
    Initializes the WebDriver, navigates the URL, scrolls to load content, extracts 
    data, exports it to XLSX, and returns email components.
    """
    driver = None
    all_extracted_data: List[Dict[str, Any]] = []
    email_content = ""
    xlsx_filename = r"D:\代码项目\scraped_damai_data.xlsx"

    try:
        print("--- 1. WebDriver Setup ---")
        service = ChromeService(ChromeDriverManager().install())
        
        options = webdriver.ChromeOptions()
        options.add_argument("--start-maximized")
        # ⭐ HEADLESS MODE ENABLED FOR PRODUCTION ⭐
        # options.add_argument("--headless") 
        
        driver = webdriver.Chrome(service=service, options=options)
        print("WebDriver initialized successfully (Headless Mode).")
        
        # --- 2. Navigation ---
        print(f"Navigating to: {url}")
        driver.get(url)
        time.sleep(3) 
        
        # --- 3. Dynamic Scrolling (Load All Content) ---
        print(f"\n--- 3. Loading content up to {target_count} items ---")
        final_count_found = scroll_and_count(driver, target_count)
        print(f"Finished scrolling. Found {final_count_found} elements.")

        time.sleep(2) 

        # --- 4. Bulk Data Processing (Extract All) ---
        print(f"\n--- 4. Starting Bulk Data Extraction (Targeting {target_count} items) ---")
        all_item_elements = driver.find_elements(By.CSS_SELECTOR, ALL_ITEMS_SELECTOR)
        
        valid_extraction_count = 0
        
        for index, item_element in enumerate(all_item_elements[:target_count]):
            data = extract_data_from_element(item_element, index)
            
            if data:
                all_extracted_data.append(data)
                valid_extraction_count += 1

        print(f"\n=======================================================")
        print(f"Scraping complete. Processed {valid_extraction_count} valid items.")
        
        # --- 5. Export Data to XLSX ---
        export_data_to_xlsx(all_extracted_data, xlsx_filename)
        
        # --- 6. Generate Email Content ---
        email_content = generate_email_content(all_extracted_data, target_count)
        
    except Exception as e:
        print(f"\nFATAL ERROR DURING SCRAPING: {type(e).__name__}: {e}")
    
    finally:
        # --- 7. Cleanup ---
        if driver:
            print("Closing browser.")
            driver.quit()
        print("=======================================================")

    return all_extracted_data, email_content, xlsx_filename


def send_email(sender_email, sender_password, receiver_email, subject, message, attachment_path):
    """Sends an email with a plain text message and an attachment."""
    
    msg = MIMEMultipart()
    msg['From'] = sender_email
    msg['To'] = receiver_email
    msg['Subject'] = subject

    msg.attach(MIMEText(message, 'plain'))

    try:
        attachment = open(attachment_path, 'rb')
        filename = os.path.basename(attachment_path) 
        part = MIMEBase('application', 'octet-stream')
        part.set_payload(attachment.read())
        encoders.encode_base64(part)
        part.add_header('Content-Disposition', f'attachment; filename= {filename}')
        msg.attach(part)
        attachment.close()
    except FileNotFoundError:
        print(f"WARNING: Attachment file not found at {attachment_path}. Sending email without attachment.")
        
    try:
        with smtplib.SMTP('smtp.qq.com', 587) as smtp:
            smtp.starttls()
            smtp.login(sender_email, sender_password)
            smtp.sendmail(sender_email, receiver_email.split(','), msg.as_string())
            print('\nEmail sent successfully!')
            
    except smtplib.SMTPAuthenticationError:
        print("\nERROR: SMTP Authentication Failed. Check sender email and password (or authorization code).")
    except Exception as e:
        print(f"\nERROR sending email: {e}")


if __name__ == "__main__":
    TARGET_URL = "https://m.damai.cn/shows/category.html?categoryId=2394&clicktitle=%E6%BC%94%E5%94%B1%E4%BC%9A&spm=a2o71.home.icon.ditem_0&sqm=dianying.h5.unknown.value" 
    TARGET_COUNT = 251 
    
    # Execute the full scraping process
    extracted_data, report_content, excel_file_path = run_scraper(TARGET_URL, TARGET_COUNT)
    
    print("\n--- FINAL REPORT ---")
    print(report_content)
    print(f"Data saved to: {excel_file_path}")

    # --- EMAIL SENDING RE-ENABLED ---
    current_date = datetime.now().date()
    formatted_date = current_date.strftime("%Y-%m-%d")

    sender_email = '396481139@qq.com'
    sender_password = 'mocjzkhznmudbghf'
    receiver_email = 'cuiyuan@maisoncapital.com,wangziyuan@maisoncapital.com,396481139@qq.com'
    subject = f'大麦网 (Damai) 演唱会数据 {formatted_date}'
    attachment_path = excel_file_path

    send_email(sender_email, sender_password, receiver_email, subject, report_content, attachment_path)