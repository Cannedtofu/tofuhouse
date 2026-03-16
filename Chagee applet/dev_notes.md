# Chagee Applet Scraper: OCR Methodology

## Project Overview
This project automates data extraction from the "Chagee" (霸王茶姬) WeChat applet using a **Vision-Based Scraping** approach. Unlike network interception, this method relies on UI automation and high-accuracy Optical Character Recognition (OCR) to scrape store queues and order statuses directly from the screen.

## Execution Logic & Workflow

### 1. UI Navigation & Orchestration
**Module**: `ui_modules/applet_interact.py`
**Goal**: Locate the applet, navigate to the data source, and manage the scrolling feedback loop.

*   **`interact_with_applet(target_count=15)`**: 
    1.  **Window Discovery**: Uses `uiautomation` to find the `Chrome_WidgetWin_0` class window (excluding the main WeChat window).
    2.  **Entry Trigger**: Clicks specifically at `(120, 600)` relative to the applet window to enter the store list page.
    3.  **Initial Positioning**: Performs a 3-wheel-rotation scroll at `(200, 566)` to settle the list into its scraping position.
    4.  **Dynamic Scrape Loop**:
        -   **Capture**: `applet_window.CaptureToImage()` saves the current view to `temp_scrape.png`.
        -   **OCR Process**: Calls the `ChageeOCRExtractor` to parse the screenshot.
        -   **Feedback**: Compares detected stores against `all_scraped_stores`. If no new stores are found after a scroll, it increments a retry counter.
        -   **Auto-Scroll**: `auto.WheelDown(wheelTimes=5)` moves the list down to bring fresh entries into view.
    5.  **Persistence**: Once the target count (default 15) is reached, it uses `pandas` to export the results to `scraped_stores.xlsx` with current Date, Time, and Day.

### 2. Vision & OCR Engine
**Module**: `ocr_extractor.py`
**Goal**: Low-latency, high-accuracy localization and character recognition.

*   **`ChageeOCRExtractor.extract_data(image_path)`**:
    -   **Localization**: Uses OpenCV (Canny/Contours) to find store containers within a ROI (ignoring the top 12% of the screen). 
    -   **Deduplication**: Implements a vertical IoU (Intersection over Union) logic to handle overlapping or nested boxes, ensuring each store is counted only once.
    -   **Targeted Cropping**:
        -   **Store Name**: Fixed offset `(22px to 46px)` relative to the box top.
        -   **Order Status**: Fixed offset `(46px to 70px)` relative to the box top.
    -   **OCR Core**: Uses **PaddleOCR** in recognition-only mode (`det=False`). Images are upscaled 4x before processing to enhance character clarity.
    -   **Post-Processing**:
        -   `clean_text`: Strips noise and applies a custom dictionary for common OCR errors (e.g., `芸` -> `荟`).
        -   `parse_status`: Extracts the numerical **Cup Count** (e.g., "前方 18 杯制作中" -> `18`).

### 3. Accuracy Verification & Benchmarking
**Module**: `verify_ocr.py`
**Goal**: Ensure extraction quality remains >95%.

*   **`verify_ocr()`**: Runs the extractor against a set of ground-truth samples in `OCR_sample/`. It employs an **order-insensitive matching** algorithm that finds the best store similarity match, proving the system's robustness even when items shift vertically.

## Directory Structure
- `ui_modules/applet_interact.py`: The main scraper driver (Navigation + Loop).
- `ocr_extractor.py`: The brain of the scraper (Localization + PaddleOCR).
- `verify_ocr.py`: Accuracy benchmarking tool.
- `export_to_excel.py`: Offline batch processing tool for sample images.
- `scraped_stores.xlsx`: The final output file containing store names, statuses, and cup counts.

## Operational Prerequisites
- **PaddleOCR**: Local GPU acceleration (RTX series recommended) or CPU.
- **uiautomation**: Windows UI accessibility bridge.
- **Resolution**: The applet window should be visible and not minimized during the `applet_interact` sequence.
