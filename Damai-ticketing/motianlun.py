# This script requires three libraries to be installed:
# 1. selenium: pip install selenium
# 2. webdriver-manager: pip install webdriver-manager
# 3. pandas and openpyxl (for XLSX export): pip install pandas openpyxl
# 4. Email related libraries (often built-in, but requires smtplib): No separate install needed

import time
import csv
import os
import re
from datetime import datetime # datetime class imported
from typing import Dict, Any, List, Tuple

# === Email Specific Imports Added ===
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
# ====================================

# NEW IMPORT for XLSX functionality
import pandas as pd
from openpyxl import load_workbook

# Selenium imports
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException


XLSX_PATH = r"D:\代码项目\scraped_event_data.xlsx"

def safe_extract_text(root: Any, selector: str, default: str = "") -> str:
    """Safely extracts text from an element, returning default if not found."""
    try:
        # Use find_element on the root element
        return root.find_element(By.CSS_SELECTOR, selector).text
    except NoSuchElementException:
        return default

def safe_extract_attr(root: Any, selector: str, attr: str, default: str = "") -> str:
    """Safely extracts an attribute value from an element, returning default if not found."""
    try:
        # Use find_element on the root element
        return root.find_element(By.CSS_SELECTOR, selector).get_attribute(attr)
    except NoSuchElementException:
        return default


def extract_data_from_element(root_element: Any, index: int) -> Dict[str, Any]:
    """
    Extracts specific data fields from a single uni-view item element, 
    handling missing fields gracefully.
    """
    data = {"index": index, "fetch_timestamp": datetime.now().strftime("%Y-%m-%d")}
    
    try:
        # --- CRITICAL EXTRACTION: Event Name ---
        # If this fails, we cannot identify the event, so we raise NoSuchElementException to skip the row.
        data["event_name"] = safe_extract_text(root_element, '.item-name .desc span', default="[NAME_MISSING]")
        if data["event_name"] == "[NAME_MISSING]":
             raise NoSuchElementException("Critical element (event name) missing.")

        # --- NON-CRITICAL EXTRACTIONS (Fail gracefully with helper functions) ---
        
        # Image URL
        data["image_url"] = safe_extract_attr(root_element, '.lazy', 'data-src')

        # Location Label
        data["location_label"] = safe_extract_text(root_element, '.ip-label')

        # Date
        data["date"] = safe_extract_text(root_element, '.date')

        # Venue/Address
        data["venue"] = safe_extract_text(root_element, '.addr')
        
        # Tags (Multiple elements handled in standard way, but failure is contained)
        try:
            tag_elements = root_element.find_elements(By.CSS_SELECTOR, '.tags .tag span')
            data["tags"] = "|".join([tag.text for tag in tag_elements])
        except Exception:
            data["tags"] = ""
        
        # Lowest Price (Needs cleaning)
        price_text = safe_extract_text(root_element, '.lowest-price .price span')
        # Clean price (e.g., remove commas, convert to integer, or set to None if empty)
        data["price_cny"] = int(re.sub(r'\D', '', price_text)) if price_text else None

        # Provider 
        provider_text = safe_extract_text(root_element, '.show-provided-by-moretickets')
        match = re.search(r'(MoreTickets)', provider_text)
        data["provider"] = match.group(1) if match else provider_text if provider_text else ""

        return data

    except NoSuchElementException as e:
        print(f"WARNING: Critical data element not found inside item for index {index}. Skipping extraction. Error: {e}")
        return None
    except Exception as e:
        print(f"ERROR: Could not extract data for index {index}. Reason: {type(e).__name__}: {e}")
        return None


def export_data_to_xlsx(data: List[Dict[str, Any]], filename: str = "scraped_event_data.xlsx"):
    """
    Exports a list of dictionaries to a true XLSX file using pandas, which resolves 
    most UTF-8 encoding issues encountered by CSV in Excel.
    Appends data if the file exists, otherwise creates it with a header.
    """
    if not data:
        print("No data to export.")
        return

    # Convert the list of dictionaries to a pandas DataFrame
    new_df = pd.DataFrame(data)
    
    try:
        if os.path.exists(filename):
            print(f"Appending data to existing XLSX file: '{filename}'.")
            
            # Read the existing file
            existing_df = pd.read_excel(filename)
            
            # Concatenate old and new data
            combined_df = pd.concat([existing_df, new_df], ignore_index=True)
            
            # Write the combined DataFrame back to the XLSX file
            combined_df.to_excel(filename, index=False)
            print(f"Successfully appended {len(data)} records to '{filename}'.")
            
        else:
            print(f"Creating new XLSX file: '{filename}'.")
            # Create a new file, writing the header (column names)
            new_df.to_excel(filename, index=False)
            print(f"Successfully wrote {len(data)} records to '{filename}'.")
            
    except Exception as e:
        print(f"ERROR: Could not write data to XLSX file '{filename}'. Reason: {e}")
        # If openpyxl/pandas error reading or writing, fall back to simple file creation
        try:
            print("Attempting to overwrite the file due to append error.")
            new_df.to_excel(filename, index=False)
        except Exception as fallback_e:
            print(f"FATAL: Fallback export failed. Reason: {fallback_e}")


def scroll_to_end_index(driver: WebDriver, end_index: int, timeout: int = 30) -> bool:
    """
    Continuously scrolls the page until the element corresponding to end_index is 
    present in the DOM, or the timeout is reached.
    """
    TARGET_SELECTOR = f"uni-view[mark\\:index='{end_index}']"
    
    print(f"Attempting to scroll until target element (index {end_index}) is visible...")
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        # Check if the target element exists
        try:
            target_element = driver.find_element(By.CSS_SELECTOR, TARGET_SELECTOR)
            
            # Scroll it into view one last time
            driver.execute_script("arguments[0].scrollIntoView(true);", target_element)
            time.sleep(1) # Give a moment for the scroll to finalize
            print(f"Target index {end_index} loaded and scrolled into view.")
            return True
        except NoSuchElementException:
            # Scroll down the main viewport by 800 pixels
            driver.execute_script("window.scrollBy(0, 800);")
            # Give the network time to fetch and render new content
            time.sleep(0.1) # SCROLL SPEED INCREASED
            
    print(f"WARNING: Target index {end_index} not loaded within {timeout} seconds.")
    return False


def generate_email_content(data_list: List[Dict[str, Any]], start_index: int, end_index: int) -> str:
    """Generates the full text content for the email, including a summary and full data list."""
    
    total_count = len(data_list)
    
    # 1. Summary
    content = f"Scraping Report - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    content += f"----------------------------------------------------\n"
    content += f"Successfully fetched {total_count} event records (Target Index Range: {start_index} to {end_index}).\n\n"
    
    # 2. Full Result (formatted as easy-to-read text records)
    content += "FULL DATA RESULTS:\n"
    content += "====================================================\n\n"
    
    for item in data_list:
        content += f"--- Event Index {item.get('index', 'N/A')} ---\n"
        content += f"Name: {item.get('event_name', 'N/A')}\n"
        content += f"Location: {item.get('location_label', 'N/A')}\n"
        content += f"Date: {item.get('date', 'N/A')}\n"
        content += f"Venue: {item.get('venue', 'N/A')}\n"
        content += f"Price (CNY): {item.get('price_cny', 'N/A')}\n"
        content += f"Tags: {item.get('tags', 'N/A')}\n"
        content += f"Provider: {item.get('provider', 'N/A')}\n"
        content += f"Image URL: {item.get('image_url', 'N/A')}\n"
        content += "----------------------------------------------------\n\n"
        
    return content


def run_scraper(url: str, start_index: int = 0, end_index: int = 50) -> Tuple[List[Dict[str, Any]], str, str]:
    """
    Initializes the WebDriver, navigates the URL, scrolls to load content up to 
    end_index, extracts all loaded data, exports it to XLSX, and returns the email content 
    and the file path of the XLSX.
    """
    driver = None
    all_extracted_data: List[Dict[str, Any]] = []
    email_content = ""
    xlsx_filename = XLSX_PATH # Defined here for return value

    try:
        print("--- 1. WebDriver Setup ---")
        # Setup the Service using WebDriverManager
        service = ChromeService(ChromeDriverManager().install())
        
        options = webdriver.ChromeOptions()
        options.add_argument("--start-maximized")
        options.add_argument("--headless") # Running in background mode now
        
        driver = webdriver.Chrome(service=service, options=options)
        print("WebDriver initialized successfully (Headless Mode).")
        
        # --- 2. Navigation ---
        print(f"Navigating to: {url}")
        driver.get(url)
        time.sleep(3) # Give the initial page a moment to load
        
        # --- 3. Dynamic Scrolling (Load All Content) ---
        print(f"\n--- 3. Loading all content up to index {end_index} ---")
        
        if not scroll_to_end_index(driver, end_index):
            print("Content loading failed or timed out. Proceeding with partially loaded data.")

        # Give a final stability wait after scrolling is complete
        time.sleep(2) 

        # --- 4. Bulk Data Processing (Extract All) ---
        ALL_ITEMS_SELECTOR = "uni-view[mark\\:index]"
        print(f"\n--- 4. Starting Bulk Data Extraction (Indices {start_index} to {end_index}) ---")
        
        # Find all item elements loaded on the page
        all_item_elements = driver.find_elements(By.CSS_SELECTOR, ALL_ITEMS_SELECTOR)
        print(f"Found {len(all_item_elements)} total item elements with 'mark:index' attribute on the page.")

        valid_extraction_count = 0
        
        for item_element in all_item_elements:
            try:
                # Get the index attribute from the loaded element
                index_str = item_element.get_attribute("mark:index")
                index = int(index_str)
                
                # Process only the items within the desired range
                if start_index <= index <= end_index:
                    data = extract_data_from_element(item_element, index)
                    
                    if data:
                        all_extracted_data.append(data)
                        valid_extraction_count += 1
                        print(f"Successfully extracted index {index}: {data.get('event_name', 'N/A')}")

            except Exception as e:
                 # This captures errors during attribute fetching/conversion or extraction
                 print(f"ERROR processing element during bulk extraction: {e}")


        print(f"\n=======================================================")
        print(f"Scraping complete. Processed {valid_extraction_count} valid items.")
        
        # --- 5. Export Data to XLSX ---
        export_data_to_xlsx(all_extracted_data, xlsx_filename)
        
        # --- 6. Generate Email Content ---
        email_content = generate_email_content(all_extracted_data, start_index, end_index)
        
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
    # Compose the email
    msg = MIMEMultipart()
    msg['From'] = sender_email
    # Note: receiver_email might be a comma-separated string, use split() for 'To' field
    msg['To'] = receiver_email
    msg['Subject'] = subject

    # Attach the message body
    msg.attach(MIMEText(message, 'plain'))

    # Attach the file
    try:
        attachment = open(attachment_path, 'rb')
        # Use os.path.basename to reliably get the filename
        filename = os.path.basename(attachment_path) 
        part = MIMEBase('application', 'octet-stream')
        part.set_payload(attachment.read())
        encoders.encode_base64(part)
        part.add_header('Content-Disposition', f'attachment; filename= {filename}')
        msg.attach(part)
        attachment.close()
    except FileNotFoundError:
        print(f"WARNING: Attachment file not found at {attachment_path}. Sending email without attachment.")
        
    # Connect to the email server and send the email
    # Using 'smtp.qq.com' and port 587 (TLS) based on user's configuration
    try:
        with smtplib.SMTP('smtp.qq.com', 587) as smtp:
            smtp.starttls()
            smtp.login(sender_email, sender_password)
            
            # Send the email. receiver_email is typically a list for sendmail
            smtp.sendmail(sender_email, receiver_email.split(','), msg.as_string())
            print('Email sent successfully!')
            
    except smtplib.SMTPAuthenticationError:
        print("ERROR: SMTP Authentication Failed. Check sender email and password (or authorization code).")
    except Exception as e:
        print(f"ERROR sending email: {e}")


if __name__ == "__main__":
    TARGET_URL = "https://www.motianlun.cn/uni/pages/list/list?showType=1" 
    
    START_INDEX = 0
    # Current index limit set to 250
    END_INDEX = 250 
    
    # Execute the full scraping process
    extracted_data, email_report_content, excel_file_path = run_scraper(TARGET_URL, START_INDEX, END_INDEX)
    
    print("\n--- EMAIL CONTENT VARIABLE ---")
    print("Use this variable in your email sending function's body:")
    print("--------------------------------------------------")
    print(email_report_content)
    print("--------------------------------------------------")
    print("\n--- ATTACHMENT FILE PATH ---")
    print("Use this path to attach the XLSX file:")
    print("--------------------------------------------------")
    print(excel_file_path)
    print("--------------------------------------------------")
 
    # --- FIX APPLIED HERE: Use datetime.now().date() to access the date object ---
    current_date = datetime.now().date()
    formatted_date = current_date.strftime("%Y-%m-%d")

    sender_email = '396481139@qq.com'
    sender_password = 'mocjzkhznmudbghf'
    receiver_email = 'cuiyuan@maisoncapital.com,wangziyuan@maisoncapital.com,396481139@qq.com'
    subject = '摩天轮票务数据'+ formatted_date
    attachment_path = excel_file_path

    send_email(sender_email, sender_password, receiver_email, subject, email_report_content, attachment_path)