# Chagee Applet Scraper: Multi-City Vision Scraping

This project automates the extraction of store queue data (Store Name, Order Status, and Cup Count) from the "霸王茶姬" (Chagee) WeChat applet using UI automation and OCR.

## 📁 Project Structure

*   `full_test.py`: **Main Entry Point**. Executes the full workflow from opening WeChat to exporting results.
*   `config.py`: **Central Configuration**. All UI coordinates, OCR regions, and scraping targets are defined here.
*   `city_switching.py`: Logic for navigating between different cities.
*   `ocr_extractor.py`: The vision engine utilizing PaddleOCR to detect and parse store data.
*   `ui_modules/`:
    *   `applet_interact.py`: Orchestrates the scrolling/scraping loop within a city.
    *   `core_wechat.py`: Handles WeChat window focus and search bar interaction.
    *   `applet_nav.py`: Navigates to and opens the specific applet from search results.
*   `verify_ocr.py`: Utility to test OCR accuracy against samples in `OCR_sample/`.

## 🛠 Workflows & Step-by-Step Logic

### 1. Applet Initialization
**Script**: `full_test.py` -> `core_wechat.py` / `applet_nav.py`
1.  **Focus WeChat**: Finds the WeChat window by class `Qt51514QWindowIcon`.
2.  **Search**: Types "霸王茶姬小程序" and presses Enter.
3.  **Open**: Clicks the applet in the search result window at coordinates managed in `applet_nav.py`.

### 2. Multi-City Scraping Loop
**Script**: `applet_interact.py` (`main_workflow`)
1.  **Initial Scape**: Scrapes the current city (e.g., Shanghai) with 200 target stores.
2.  **City Selection**: For each city in `CITY_LIST` (`config.py`):
    *   **Trigger**: Calls `switch_city` which locates "搜索门店" and clicks the city selector.
    *   **Index**: Finds the Pinyin initial of the city (A-Z) and clicks it on the sidebar.
    *   **Locate**: Scrolls the city list until the target name is found and clicked.
3.  **Data Extraction**: Calls `scrape_city_stores` for the new region.

### 3. Scroll-Capture-OCR Cycle
**Script**: `applet_interact.py` (`scrape_city_stores`)
1.  **Capture**: Takes a screenshot of the applet view (`temp_scrape.png`).
2.  **Detect**: `ocr_extractor.py` uses Canny edge detection to find store boxes.
3.  **Parse**:
    *   **Store Name**: Cropped from the top-left section of each box.
    *   **Order Status**: Cropped from below the name. Pattern-matched for "前方x杯制作中".
4.  **Feedback**: If no new stores are found after `MAX_NO_NEW_SCROLLS` (default 4), the script moves to the next city.

## ⚙️ Configuration Guide (`config.py`)

| Parameter | Purpose | Step/Workflow |
| :--- | :--- | :--- |
| `CITY_LIST` | Target cities and quotas. | Multi-City Loop |
| `CITY_TRIGGER_OFFSET_X` | Relative shift from "搜索门店" to City Button. | City Switching |
| `STORE_LIST_ENTRY_REL_COORD` | Click to enter store list from Home. | Initial Scrape |
| `MAX_NO_NEW_SCROLLS` | Safety abort for small cities. | Scraping Logic |
| `BOX_WIDTH_CUTOFF_PERCENT` | Ignores right-side UI elements (e.g., Distance). | OCR Extraction |
| `SN_CROP_Y_START` | Vertical offset for Store Name within box. | OCR Extraction |

## 🚀 How to Run
1.  Ensure WeChat is open and logged in.
2.  Run the terminal as **Administrator**.
3.  Execute:
    ```bash
    python full_test.py
    ```
4.  Results will be saved to `multi_city_stores.xlsx`.

## 📦 Dependencies
*   `uiautomation`: UI control.
*   `paddleocr`: OCR engine.
*   `opencv-python` & `numpy`: Image processing.
*   `pandas` & `openpyxl`: Data export.
*   `pypinyin`: Chinese to Pinyin conversion.
