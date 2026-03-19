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
import xml.etree.ElementTree as ET
import re
from typing import Optional, Tuple


IMG_DIR = "scraped_charts_20260106"
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


def get_music_button_bounds(page_source: str) -> Optional[Tuple[int, int, int, int]]:
    """
    Parses the page source to find the '音乐' TextView and returns its bounds.
    Returns: Tuple (x1, y1, x2, y2) or None if not found.
    """
    try:
        root = ET.fromstring(page_source)
        # Search globally for a TextView with text="音乐"
        # Using XPath: .//android.widget.TextView[@text='音乐']
        target = root.find(".//android.widget.TextView[@text='音乐']")
        
        if target is not None:
            bounds_str = target.attrib.get('bounds', "")
            # Extract numbers using regex from "[x1,y1][x2,y2]"
            match = re.search(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', bounds_str)
            if match:
                return tuple(map(int, match.groups()))
        
        print("⚠️ '音乐' button not found in page source.")
        return None
    except Exception as e:
        print(f"❌ Error parsing page source for '音乐' button: {e}")
        return None

def save_image_cv2_unicode(filename, img_array):
    """
    Saves an image using OpenCV but supports Unicode (Chinese) filenames on Windows.
    cv2.imwrite fails silently with non-ASCII paths.
    """
    try:
        # Encode the image to memory buffer first
        is_success, im_buf_arr = cv2.imencode(".png", img_array)
        if is_success:
            # Write buffer to disk using standard IO (which handles Unicode correctly)
            im_buf_arr.tofile(filename)
            return True
        return False
    except Exception as e:
        print(f"Error saving unicode file: {e}")
        return False
        

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
            self.driver.swipe(start_x, int(height * 0.7), start_x, int(height * 0.5), duration=400)
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


    # def extract_chart_sub_data(self) -> Dict[str, Any]:
    #     if not self.driver: 
    #         return {}
        
    #     try:
    #         # Use .encode('utf-8') to handle potential Chinese character parsing issues
    #         root = ET.fromstring(self.driver.page_source.encode('utf-8'))
    #         parent = root.find(f".//android.view.View[@resource-id='{PARENT_RESOURCE_ID}']")
    #         if parent is None:
    #             return {}

    #         result, texts = {}, []
            
    #         # Start at index 13 (the first sub-category name "演唱会") 
    #         current_index = 13
    #         while True:
    #             # Query by index only to avoid breaking on the progress bar (View) 
    #             xpath = f'./android.view.View[@index="11"]/*[@index="{current_index}"]'
    #             el = parent.find(xpath)
                
    #             # If we hit an index that doesn't exist (past the "收起" button), stop [cite: 179]
    #             if el is None:
    #                 break
                
    #             # Extract text only from TextViews; append empty for others to maintain 4-block alignment
    #             if el.tag == 'android.widget.TextView':
    #                 text_val = el.attrib.get('text', '').strip()
    #                 texts.append(text_val)
    #             else:
    #                 # This accounts for index 14, 18, etc. which are progress bars 
    #                 texts.append("IMAGE_BAR") 
                    
    #             current_index += 1

    #         # Process in blocks of 4: [Name, Bar, Percentage, Value] [cite: 170, 171, 173, 174]
    #         for j in range(0, len(texts) - (len(texts) % 4), 4):
    #             key = texts[j] # e.g., "1. 演唱会"
    #             # Skip texts[j+1] as it is the "IMAGE_BAR" placeholder
    #             percentage = texts[j+2] # e.g., "95.4%"
    #             value = texts[j+3]      # e.g., "1.53亿"
                
    #             result[key] = f"{percentage}, {value}"
                
    #         return result

    #     except Exception as e:
    #         print(f"Warning: chart_sub_data extraction failed: {e}")
    #         return {}
    
    def extract_chart_sub_data(self) -> Dict[str, Any]:
        if not self.driver: 
            return {}
        
        try:
            # Load and parse the current page source
            root = ET.fromstring(self.driver.page_source.encode('utf-8'))
            parent = root.find(f".//android.view.View[@resource-id='{PARENT_RESOURCE_ID}']")
            if parent is None:
                return {}

            # The data table container is index 11 of the parent
            container = parent.find('./android.view.View[@index="11"]')
            if container is None:
                return {}

            # --- 1. Locate the "占比" anchor ---
            anchor_index = -1
            for child in container:
                if child.attrib.get('text') == "占比":
                    anchor_index = int(child.attrib.get('index', -1))
                    break
            
            if anchor_index == -1:
                print("⚠️ Anchor '占比' not found in container.")
                return {}

            # --- 2. Extraction starting 2 indices after "占比" ---
            result, texts = {}, []
            current_index = anchor_index + 2 # Skip the header and the gap to reach data
            
            while True:
                # Use wildcard to find the element by index regardless of its class
                el = container.find(f'./*[@index="{current_index}"]')
                
                # Stop if the index doesn't exist or we hit the 'Collapse' (收起) button
                if el is None:
                    break
                
                # Extract text or placeholder based on element type
                if el.tag == 'android.widget.TextView':
                    text_val = el.attrib.get('text', '').strip().replace("\n", "")
                    texts.append(text_val)
                else:
                    # Placeholder for the non-text progress bar (android.view.View)
                    texts.append("UI_VIEW_GAP")
                    
                current_index += 1

            # --- 3. Group by 4s (Name, Gap, %, Value) ---
            # XML pattern: Index 13(Name), 14(Gap), 15(%), 16(Value)
            for j in range(0, len(texts) - (len(texts) % 4), 4):
                category_name = texts[j]
                prop_percent = texts[j+2]
                box_office = texts[j+3]
                result[category_name] = f"{prop_percent}, {box_office}"
                
            return result

        except Exception as e:
            print(f"Warning: chart_sub_data extraction failed: {e}")
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
                date_element = parent_element.find('./android.view.View[@index="1"]/android.widget.TextView[@index="0"]')
                if date_element is not None:
                    date_text = date_element.attrib.get('text', 'Text attribute missing')
                
                total_box_value_element = parent_element.find('./android.widget.TextView[@index="3"]')
                if total_box_value_element is not None:
                    total_box_text = total_box_value_element.attrib.get('text', 'Value attribute missing')
                
                total_box_unit_element = parent_element.find('./android.widget.TextView[@index="4"]')
                if total_box_unit_element is not None:
                    total_box_unit_text = total_box_unit_element.attrib.get('text', 'Unit attribute missing')   

                if total_box_text not in ["N/A (Value Not Found)", "Value attribute missing"] and total_box_unit_text not in ["", "Unit attribute missing"]:
                    total_box_text = total_box_text + total_box_unit_text

                audience_element = parent_element.find('./android.widget.TextView[@index="8"]')
                if audience_element is not None:
                    audience_text = audience_element.attrib.get('text', 'Text attribute missing')

                event_count_element = parent_element.find('./android.widget.TextView[@index="9"]')
                if event_count_element is not None:
                    event_count_text = event_count_element.attrib.get('text', 'Text attribute missing')

                average_price_element = parent_element.find('./android.widget.TextView[@index="10"]')
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

    
    def full_ocr_performance(self, chart_image_bounds, save_path=None):
            if chart_image_bounds in ["N/A"]: return []
            
            # 1. Get the raw crop (BGR Numpy Array)
            img_data = self._crop_and_return_chart_data(chart_image_bounds)
            if img_data is None: 
                print(f"❌ Failed to crop image for {save_path}")
                return []

            # 2. Save immediately to disk
            if save_path:
                # Using the custom unicode saver
                saved = save_image_cv2_unicode(save_path, img_data)
                if not saved:
                    print(f"❌ Failed to save image to: {save_path}")

            # 3. Pass to OCR module
            ocr_raw = preprocess_and_ocr(img_data)
            return parse_ocr_to_image_result(ocr_raw)


    def scrape_single_dataset(self, element_to_click_locator: tuple):
        province_box = ""
        province_event = ""
        province_audience = ""
        ocr_results_boxoffice = ""
        ocr_results_audience = ""
        ocr_results_event = ""
        music_box = ""
        music_audience = ""
        music_event = ""

        if not self.driver: return
        self.perform_swipes(option="settle_and_top")
        sleep(0.3)
        # save_page_source_for_debug(self.driver)
        raw_data = self.extract_selective_data()
        

        # Take first 10 chars, default to "UnknownDate" if empty/NA
        raw_date_str = raw_data.get('date_text', 'UnknownDate')[:10]
        safe_date = re.sub(r'[\\/*?:"<>|]', '_', raw_date_str)
        if not safe_date.strip():
            safe_date = datetime.now().strftime("%Y%m%d_%H%M%S")


        # # find and click 音乐
        bounds = get_music_button_bounds(self.driver.page_source)
        if bounds:
            click_by_bounds(self.driver, bounds, duration=100)
            music_true = True
        sleep(0.2)
        # # click 展开类目
        if music_true == True:
            click_by_bounds(self.driver, (0,922,900,984), duration=100, debug=False)


        # --- 1. Box Office ---
        click_by_bounds(self.driver, (3,405,121,435), duration=100, debug=False)
        sleep(0.5)
        # path_box = os.path.join(IMG_DIR, f"{safe_date}_boxoffice.png")
        # ocr_results_boxoffice = self.full_ocr_performance('[186,540][714,901]', save_path=path_box)
        if music_true == True:
            music_box = self.extract_chart_sub_data()
        

        # --- 2. Audience ---
        click_by_bounds(self.driver, (120,405,237,435), duration=100, debug=False)
        sleep(0.5)
        # path_aud = os.path.join(IMG_DIR, f"{safe_date}_audience.png")
        # ocr_results_audience = self.full_ocr_performance('[186,540][714,901]', save_path=path_aud)
        if music_true == True:
            music_audience = self.extract_chart_sub_data()

        # --- 3. Event ---
        click_by_bounds(self.driver, (234,405,331,435), duration=100, debug=False)
        sleep(0.5)
        # path_evt = os.path.join(IMG_DIR, f"{safe_date}_event.png")
        # ocr_results_event = self.full_ocr_performance('[186,540][714,901]', save_path=path_evt)
        if music_true == True:
            music_event = self.extract_chart_sub_data()

        # save_page_source_for_debug(self.driver)

        # self.perform_swipes(option="bottom")
        # sleep(0.2)
        # #save_page_source_for_debug(self.driver)
        # click_by_bounds(self.driver, (3,322,120,351), duration=100, debug=False)
        # province_box = self.extract_province_data()
        # click_by_bounds(self.driver, (120,322,237,351), duration=100, debug=False)
        # province_audience = self.extract_province_data()
        # click_by_bounds(self.driver, (234,322,331,351), duration=100, debug=False)
        # province_event = self.extract_province_data()
        # self.perform_swipes(option="top")

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
            "province_event": province_event,
            "music_box" : music_box,
            "music_audience": music_audience,
            "music_event": music_event
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

    def save_dataset_incrementally(self, dataset: dict, filename: str = "scraped_data_music_only.jsonl"):
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
        click_bounds = (0, 168, 67, 267)

        # --- Loop: click + scrape until click fails ---
        while count < 90:
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