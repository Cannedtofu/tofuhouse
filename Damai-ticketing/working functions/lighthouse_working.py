from appium import webdriver
from appium.options.android import UiAutomator2Options 
from time import sleep
from selenium.common.exceptions import TimeoutException, WebDriverException, SessionNotCreatedException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from appium.webdriver.common.appiumby import AppiumBy
from appium.webdriver import Remote
import xml.etree.ElementTree as ET
import json
from typing import List, Dict, Any, Optional
from datetime import datetime
import re
import io
import pytesseract
import numpy as np
import cv2
from ocr_module import preprocess_and_ocr, parse_ocr_to_image_result
from click import click_by_bounds
import subprocess
import os

IMG_DIR = "scraped_charts"
if not os.path.exists(IMG_DIR):
    os.makedirs(IMG_DIR)

# --- Tesseract Configuration ---
TESSERACT_CMD_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
if TESSERACT_CMD_PATH:
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD_PATH

# --- Pillow check ---
try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    print("Warning: Pillow (PIL) not found. Image cropping skipped.")
    PIL_AVAILABLE = False

# --- Global Config ---
APPIUM_SERVER_URL = 'http://localhost:4723'
PARENT_RESOURCE_ID = "performance-industry-market" 
PARENT_LOCATOR = (AppiumBy.XPATH, f'//android.view.View[@resource-id="{PARENT_RESOURCE_ID}"]')

def save_page_source_for_debug(driver: Remote):
    """Saves full page source XML for debugging."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"page_source_debug_{timestamp}.txt"
    try:
        with open(filename, "w", encoding="utf-8") as f:
            f.write(driver.page_source)
    except WebDriverException as e:
        print(f"Warning: Could not save page source: {e}")

class AppScraper:
    def __init__(self, desired_caps: Dict[str, Any]):
        self.driver: Optional[Remote] = None
        self.desired_caps = desired_caps
        self.data_store: List[Dict[str, Any]] = []

    def connect(self) -> bool:
        """Start Appium session."""
        options = UiAutomator2Options()
        options.load_capabilities(self.desired_caps)
        try:
            self.driver = webdriver.Remote(command_executor=APPIUM_SERVER_URL, options=options)
            print(f"✅ Connected. Session ID: {self.driver.session_id}")
            return True
        except (SessionNotCreatedException, WebDriverException) as e:
            print(f"CRITICAL: Could not start Appium session: {e}")
            return False

    def sync_to_page(self, timeout: int = 30) -> bool:
        """Wait for parent element to ensure page loaded."""
        if not self.driver: return False
        try:
            WebDriverWait(self.driver, timeout).until(
                EC.presence_of_element_located(PARENT_LOCATOR)
            )
            # save_page_source_for_debug(self.driver)
            return True
        except TimeoutException:
            print(f"CRITICAL: Parent element '{PARENT_RESOURCE_ID}' not found after {timeout}s.")
            # save_page_source_for_debug(self.driver)
            self.quit()
            return False

    def perform_swipes(self, option: str = "settle_and_top"):
        """Swipes the page based on the selected option, refresh-safe for top swipes."""
        if not self.driver:
            return

        size = self.driver.get_window_size()
        width, height = size['width'], size['height']
        start_x = width // 2

        if option == "top":
            # Refresh-safe top swipe: start mid-screen and swipe gently upwards
            for _ in range(3):
                start_y = int(height * 0.3)
                end_y = int(height * 0.5)
                self.driver.swipe(start_x, start_y, start_x, end_y, duration=400)
                sleep(0.8)

        elif option == "bottom":
            # Swipe downwards to bottom
            for _ in range(5):
                start_y = int(height * 0.8)
                end_y = int(height * 0.2)
                self.driver.swipe(start_x, start_y, start_x, end_y, duration=600)
                sleep(0.5)

        elif option == "settle_and_top":
            # Gentle settle swipe to stabilize view
            self.driver.swipe(start_x, int(height * 0.7), start_x, int(height * 0.6), duration=400)
            sleep(0.1)
            self.driver.swipe(start_x, int(height * 0.6), start_x, int(height * 0.7), duration=400)
            sleep(0.3)
            # Refresh-safe swipe to top
            for _ in range(3):
                start_y = int(height * 0.35)
                end_y = int(height * 0.5)
                self.driver.swipe(start_x, start_y, start_x, end_y, duration=400)
                sleep(0.1)
  

        else:
            print(f"⚠️ Unknown swipe option: {option}")

    def _parse_bounds(self, bounds_str: str) -> Optional[List[int]]:
        match = re.search(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', bounds_str)
        if match: return [int(match.group(i)) for i in range(1,5)]
        print(f"Warning: Could not parse bounds: {bounds_str}")
        return None

    def _crop_and_return_chart_data(self, bounds_str: str) -> Optional[np.ndarray]:
        if not self.driver or not PIL_AVAILABLE: return None
        bounds = self._parse_bounds(bounds_str)
        if not bounds: return None
        try:
            img = Image.open(io.BytesIO(self.driver.get_screenshot_as_png()))
            cropped_img = img.crop(bounds)
            img_np_bgr = cv2.cvtColor(np.array(cropped_img.convert('RGB')), cv2.COLOR_RGB2BGR)
            return img_np_bgr
        except Exception as e:
            print(f"Warning: Image processing failed: {e}")
            return None

    def extract_province_data(self) -> Dict[str, Any]:
        if not self.driver: return {}
        try:
            root = ET.fromstring(self.driver.page_source)
            parent = root.find(f".//android.view.View[@resource-id='{PARENT_RESOURCE_ID}']")
            result, texts = {}, []
            for i in range(8, 48):
                el = parent.find(f'./android.view.View[@index="13"]/android.widget.TextView[@index="{i}"]')
                texts.append(el.attrib.get('text','') if el is not None else '')
            for j in range(0, len(texts), 4):
                result[texts[j]] = ','.join(texts[j+1:j+4])
            return result
        except Exception as e:
            print(f"Warning: Province extraction failed: {e}")
            return {}

    def extract_selective_data(self) -> Dict[str, Any]:
        """Extracts text and image bounds by parsing the static page source XML."""
        if not self.driver: return {}

        date_text = "N/A (Date Not Found)"
        total_box_text = "N/A (Value Not Found)"
        total_box_unit_text = ""
        audience_text = "N/A (Audience Not Found)"
        event_count_text = "N/A (Event Count Not Found)"
        average_price_text = "N/A (Average Price Not Found)"

        try:
            xml_source = self.driver.page_source
            root = ET.fromstring(xml_source)
            parent_element = root.find(f".//android.view.View[@resource-id='{PARENT_RESOURCE_ID}']")

            if parent_element is not None:
                
                # --- Text Extraction ---
                date_element = parent_element.find('./android.view.View[@index="2"]/android.widget.TextView[@index="0"]')
                if date_element is not None:
                    date_text = date_element.attrib.get('text', 'Text attribute missing')
                
                total_box_value_element = parent_element.find('./android.widget.TextView[@index="4"]')
                if total_box_value_element is not None:
                    total_box_text = total_box_value_element.attrib.get('text', 'Value attribute missing')
                
                total_box_unit_element = parent_element.find('./android.widget.TextView[@index="5"]')
                if total_box_unit_element is not None:
                    total_box_unit_text = total_box_unit_element.attrib.get('text', 'Unit attribute missing')   

                if total_box_text not in ["N/A (Value Not Found)", "Value attribute missing"] and total_box_unit_text not in ["", "Unit attribute missing"]:
                    total_box_text = total_box_text + total_box_unit_text

                audience_element = parent_element.find('./android.widget.TextView[@index="9"]')
                if audience_element is not None:
                    audience_text = audience_element.attrib.get('text', 'Text attribute missing')

                event_count_element = parent_element.find('./android.widget.TextView[@index="10"]')
                if event_count_element is not None:
                    event_count_text = event_count_element.attrib.get('text', 'Text attribute missing')

                average_price_element = parent_element.find('./android.widget.TextView[@index="11"]')
                if average_price_element is not None:
                    average_price_text = average_price_element.attrib.get('text', 'Text attribute missing')
                                    
        except Exception as e:
            error_msg = f"Unexpected Error during extraction: {e}"
            date_text = total_box_text = audience_text = event_count_text = average_price_text = error_msg
        
        return {
            "date_text": date_text,
            "total_box_office": total_box_text,
            "audience_count": audience_text,
            "event_count_text": event_count_text,
            "average_price_text": average_price_text,
        }

    def full_ocr_performance(self, chart_image_bounds):
        if chart_image_bounds in ["N/A"]: return []
        img_data = self._crop_and_return_chart_data(chart_image_bounds)
        if img_data is None: return []
        ocr_raw = preprocess_and_ocr(img_data)
        return parse_ocr_to_image_result(ocr_raw)

    def scrape_single_dataset(self, element_to_click_locator: tuple):
        if not self.driver: return
        self.perform_swipes(option="settle_and_top")
        raw_data = self.extract_selective_data()
        #save_page_source_for_debug(self.driver)



        click_by_bounds(self.driver, (3,498,121,526), duration=100, debug=False)
        sleep(0.5)        
        ocr_results_boxoffice = self.full_ocr_performance('[186,540][714,901]')
        click_by_bounds(self.driver, (120,498,237,526), duration=100, debug=False)
        sleep(0.5)
        ocr_results_audience = self.full_ocr_performance('[186,540][714,901]')
        click_by_bounds(self.driver, (234,498,331,526), duration=100, debug=False)
        sleep(0.5)
        ocr_results_event = self.full_ocr_performance('[186,540][714,901]')

        self.perform_swipes(option="bottom")
        sleep(0.2)
        #save_page_source_for_debug(self.driver)
        click_by_bounds(self.driver, (3,322,120,351), duration=100, debug=False)
        province_box = self.extract_province_data()
        click_by_bounds(self.driver, (120,322,237,351), duration=100, debug=False)
        province_audience = self.extract_province_data()
        click_by_bounds(self.driver, (234,322,331,351), duration=100, debug=False)
        province_event = self.extract_province_data()
        self.perform_swipes(option="top")

        combined_data = {
            "page_identifier": self.driver.current_activity,
            "date": raw_data['date_text'],
            "total_box_office": raw_data['total_box_office'], 
            "audience_count": raw_data['audience_count'], 
            "event_count": raw_data['event_count_text'],
            "average_price": raw_data['average_price_text'],
            "ocr_chart_boxoffice": ocr_results_boxoffice,
            "ocr_chart_audience": ocr_results_audience,
            "ocr_chart_event": ocr_results_event,
            "province_box": province_box,
            "province_audience": province_audience,
            "province_event": province_event
        }
        self.data_store.append(combined_data)
        self.save_dataset_incrementally(combined_data)

    def force_android_gc(self, package_name):
        """Forces Android to trim memory for the specific package."""
        try:
            # 1. Force GC on the package (Requires root usually, but trim-memory works on unrooted)
            cmd = f"adb shell am send-trim-memory {package_name} RUNNING_CRITICAL"
            subprocess.run(cmd, shell=True, check=False)

            # 2. Clear local Python garbage (less important but good practice)
            import gc
            gc.collect()

            print("🧹 Android GC triggered.")
        except Exception as e:
            print(f"⚠️ GC Failed: {e}")

    def save_dataset_incrementally(self, dataset: dict, filename: str = "scraped_data.jsonl"):
        try:
            with open(filename, "a", encoding="utf-8") as f:
                f.write(json.dumps(dataset, ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"Error saving dataset: {e}")

    def quit(self):
        if self.driver:
            self.driver.quit()

# --- Desired Capabilities ---
desired_caps = {
    'platformName': 'Android',
    'appium:automationName': 'UiAutomator2',
    'appium:deviceName': 'emulator-5554',
    'appium:appPackage': 'com.alipictures.moviepro',
    'appium:appActivity': 'com.alipictures.moviepro.biz.main.ui.MainActivity',
    'appium:noReset': True,
    'appium:newCommandTimeout': 3600,
    'appium:disableIdlingResource': True, 
}

# --- MAIN ---
if __name__ == "__main__":
    scraper = AppScraper(desired_caps)
    count = 0

    if scraper.connect() and scraper.sync_to_page():
        # --- First scrape (no click) ---
        print("\n--- SCRAPING FIRST PAGE ---")
        dummy_locator = (AppiumBy.XPATH, "//*[dummy-placeholder='true']")
        # scraper.scrape_single_dataset(dummy_locator)

        # Convert bounds to a tuple for click_by_bounds
        click_bounds = (0, 261, 67, 360)

        # --- Loop: click + scrape until click fails ---
        while count < 1100:
            try:
                # Attempt to click the bounds
                clicked = click_by_bounds(scraper.driver, click_bounds, duration=100, debug=False, debug_prefix="auto_click")

                if not clicked:
                    print("\n--- Element no longer clickable. Stopping scraping loop. ---")
                    break  # Stop loop if click fails

                sleep(0.5)  # Wait for page to settle

                # Scrape the current dataset
                scraper.scrape_single_dataset(dummy_locator)
                
                count = count+1

                if count % 10 == 0:  # Run every 10 items
                    scraper.force_android_gc("com.alipictures.moviepro")
                    sleep(1) # Give the device a moment to recover
    
            except Exception as e:
                print(f"Unexpected error during loop: {e}")
                break  # Exit loop on unexpected error

        print(f"\nScraping complete. Total datasets collected: {len(scraper.data_store)}")

    scraper.quit()