# Chagee Applet Scraper: Multi-City Vision Scraping

![Chagee Applet Scraper](https://img.shields.io/badge/Status-Active-brightgreen) ![Python](https://img.shields.io/badge/Python-3.x-blue) ![PaddleOCR](https://img.shields.io/badge/OCR-PaddleOCR-orange)

This project automates the extraction of store queue data (Store Name, Order Status, and Cup Count) from the "霸王茶姬" (Chagee) WeChat applet using UI automation and OCR. It's designed to run on a daily schedule, scrape data across multiple specified cities, and orchestrate reporting and cleanup.

## 📁 Modular Project Structure

The codebase is organized into functional modules orchestrating different phases of the scraping pipeline:

*   **`main.py`**: **Master Orchestrator**. The single entry point script. Runs the initialization, scraping, data calculation, email reporting, and cleanup workflows sequentially.
*   **`config.py`**: **Central Configuration**. All UI coordinates, OCR regions, target cities (`CITY_LIST`), quotas, and scraping thresholds are defined here.
*   **`wechat_interaction.py`**: **Applet Initialization**. Handles bringing WeChat to the foreground, searching for "霸王茶姬小程序" in the search bar, clearing out old searches, and opening the applet.
*   **`scraping_logic.py`**: **Core Vision & Navigation Engine**.
    *   Finds and interacts with the applet UI (scrolling, clicking).
    *   *City Switching*: Navigates the city list using pinyin initialization (`switch_city`).
    *   *Vision Processing*: Uses `PaddleOCR` (`ChageeOCRExtractor`) with edge detection and smart bounding box selection to parse store names and order statuses from screenshots.
*   **`data_manager.py`**: **Data Operations**. Calculates daily metrics (stores scrapped, total cups, city breakdowns) and appends current run data to the historical Excel tracking file (`multi_city_stores.xlsx`).
*   **`email_sender.py`**: **Notification Protocol**. Uses SMTP to send out daily statistical summary emails along with the comprehensive Excel attachment to stakeholders.
*   **`cleanup_manager.py`**: **Fail-Safe Cleanup**. Hardened logic to ensure WeChat search and applet windows (`Chrome_WidgetWin_0`) are closed safely using UI buttons, Alt+F4, or taskkill after script execution.
*   **`check_trigger.py`**: **UI Diagnostic Tool**. A helper script to verify if the applet UI elements (like the city search bar or text controls) can be found. It captures a screenshot (`debug_trigger.png`) highlighting the clicking offsets and specific target coordinates (like relative `(49, 126)`) to help debug UI changes dynamically.

## 🛠 Workflows & Step-by-Step Logic

### 1. Applet Initialization
**Script**: `main.py` -> `wechat_interaction.py`
1.  **Focus WeChat**: Finds the WeChat window by class `Qt51514QWindowIcon`.
2.  **Search**: Clears search text, types "霸王茶姬小程序", and presses Enter.
3.  **Open**: Explicitly clicks the applet in the detached search result window and verifies its launch.

### 2. Multi-City Scraping Loop & Data Extraction
**Script**: `main.py` -> `scraping_logic.py`
1.  **Initial Scrape**: Scrapes the initial assumed city (e.g., Shanghai) until it finishes or hits `DEFAULT_TARGET_COUNT`.
2.  **City Selection**: For each city outlined in `config.py` (`CITY_LIST`):
    *   Triggers the "搜索门店" city selector.
    *   Clicks the pinyin initial index (A-Z).
    *   Scrolls down and clicks the explicit text target of the city.
3.  **Deep OCR Loop (`ChageeOCRExtractor`)**:
    *   **Capture**: Snaps `temp_scrape.png`.
    *   **Detect**: Runs OpenCV logic (Canny Edges & Contours) to slice up horizontal store cards.
    *   **Parse**: Chops individual bounding boxes into Store Name ranges and Order Status ranges, pushing them through PaddleOCR. Cleans up garbled OCR text dynamically.
    *   **High-Volume Triggers**: Automatically saves permanent screenshots of stores exceeding the `CUP_COUNT_THRESHOLD`.

### 3. Reporting & Cleanup
**Script**: `main.py` -> `data_manager.py` / `email_sender.py` / `cleanup_manager.py`
1.  **Excel Logging**: Aggregates the multi-city run into pandas DataFrames and appends to `multi_city_stores.xlsx`.
2.  **Statistic Calculation**: Parses the appended data and filters for the current day's insertions to evaluate run success.
3.  **Email Output**: Attaches the spreadsheet and inserts the analytics directly into the body of an SMTP email.
4.  **Window Pruning**: Kills background applet window processes so subsequent runs don't encounter locked or hung UI fragments.

## ⚙️ Key Configurations (`config.py`)

| Parameter | Purpose | Area |
| :--- | :--- | :--- |
| `CITY_LIST` | Target cities and store processing quotas. | Multi-City Loop |
| `MAX_NO_NEW_SCROLLS` | Safety abort to move to the next city early on small lists. | Scraping Logic |
| `BOX_WIDTH_CUTOFF_PERCENT` | Ignores right-side UI elements (e.g., Distance) when cropping OCR inputs. | OCR Extraction |
| `SN_CROP_Y_START`& `OS_`... | Vertical offsets to accurately split Store Name & Order text inside bounding boxes. | OCR Extraction |
| `SCREENSHOT_ON_THRESHOLD` | Boolean toggle for keeping raw screenshots of busy stores. | OCR Extraction |
| `CUP_COUNT_THRESHOLD` | Volume integer limit (e.g., 80) triggering the screenshot save feature. | OCR Extraction |

## 🚀 How to Run

1.  Ensure WeChat is open, logged in, and scaled appropriately.
2.  Run your terminal/command prompt as **Administrator** (Required by `uiautomation`).
3.  Execute the orchestrator:
    ```bash
    python main.py
    ```
4.  Optionally set up `run_daily_chagee.bat` with Windows Task Scheduler to run step #3.

## 📦 Dependencies
*   `uiautomation`: Desktop UI control.
*   `paddleocr` / `opencv-python` / `numpy`: Vision engine & Image preprocessing.
*   `pandas` / `openpyxl`: Analytical tracking and Excel data export.
*   `pypinyin`: Dynamic Chinese-to-Pinyin alphabet indexing inside the applet.
