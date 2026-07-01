# Guming Applet Scraper: Multi-City Vision Scraping

![Guming Applet Scraper](https://img.shields.io/badge/Status-Active-brightgreen) ![Python](https://img.shields.io/badge/Python-3.x-blue) ![PaddleOCR](https://img.shields.io/badge/OCR-PaddleOCR-orange)

This project automates the extraction of store queue data (Store Name, Order Status, and Cup Count) from the "古茗" (Guming) WeChat applet using UI automation and OCR on Windows PC. It is structured inside `d:\代码项目\Guming applet`.

## 📁 Modular Project Structure

The codebase is organized into functional modules orchestrating different phases of the scraping pipeline:

*   **`main.py`**: **Master Orchestrator**. The entry point script. Runs initialization, scraping, data calculation, email reporting, and cleanup workflows sequentially.
*   **`config.py`**: **Central Configuration**. Stores coordinates, map collapse settings, target city lists (`CITY_LIST`), SMTP parameters, and cup count thresholds.
*   **`wechat_interaction.py`**: **Applet Initialization**. Handles bringing WeChat to the foreground, searching for "古茗", clicking the suggestions dropdown, and opening the applet.
*   **`scraping_logic.py`**: **Core Navigation & Vision Engine**.
    *   Finds and interacts with Guming UI (navigation, clicking "点单", and collapsing the Tencent Map).
    *   *City Switching*: Searches for cities directly in the region selector input box and clicks the matches.
    *   *Vision Processing*: Uses `PaddleOCR` (`GumingOCRExtractor`) to parse store names and order statuses from horizontal store card regions.
*   **`data_manager.py`**: **Data Operations**. Calculates daily run statistics (stores scraped, total cups, city breakdowns) and appends to the Excel spreadsheet (`guming_city_stores.xlsx`).
*   **`analyze_stores.py`**: **Trend Charting & WoW Comparison**. Generates a matplotlib trend chart of average cups per store and builds Week-on-Week same-store comparison tables.
*   **`email_sender.py`**: **SMTP Notifications**. Emails the generated HTML tables, base64 trend chart, and spreadsheet attachment to stakeholders.
*   **`cleanup_manager.py`**: **Fail-Safe Cleanup**. Safe closing mechanisms to Alt+F4 or kill Guming applet windows and search result windows after run completion.

## ⚙️ Key Configurations (`config.py`)

| Parameter | Purpose |
| :--- | :--- |
| `CITY_LIST` | Target cities and quotas (e.g., Hangzhou, Shenzhen, Chengdu, Chongqing, Guangzhou, Beijing). |
| `CITY_SEARCH_INPUT_COORD` | Local coordinates to click the city selector search bar `(208, 92)`. |
| `MAP_COLLAPSE_COORD` | Local coordinates to click "收起地图" `(200, 433)` to maximize visible store list space. |
| `STORE_LIST_ENTRY_REL_COORD` | Tab bar entry click for "点单" `(163, 748)`. |
| `SCROLL_REL_COORD` | Scroll hovering coordinates `(200, 500)`. |
| `CUP_COUNT_THRESHOLD` | Volume integer limit (e.g., 100) triggering the screenshot save feature. |

## 🚀 How to Run

1.  Ensure WeChat is open, logged in, and scaled appropriately.
2.  Run your terminal/command prompt as **Administrator** (Required by `uiautomation`).
3.  Execute the orchestrator:
    ```bash
    python main.py
    ```
4.  Optionally set up a Windows Task Scheduler to run the script daily.

## 📦 Dependencies
*   `uiautomation`: Desktop UI control.
*   `paddleocr` / `opencv-python` / `numpy`: Vision engine.
*   `pandas` / `openpyxl`: Analytical Excel database tracking.
*   `matplotlib`: Plotting trend charts.
