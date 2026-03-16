# Chagee Applet Scraper: OCR Methodology

## Project Overview
This project automates data extraction from the "Chagee" (霸王茶姬) WeChat applet using a **Vision-Based Scraping** approach. Unlike network interception, this method relies on UI automation and high-accuracy Optical Character Recognition (OCR) to scrape store queues and order statuses directly from the screen across multiple cities.

## Execution Logic & Workflow

### 1. Multi-City Orchestration
**Module**: `ui_modules/applet_interact.py`
**Goal**: Manage the transition between the initial city and a list of target cities defined in `config.py`.

*   **`main_workflow()`**: 
    1.  **Initial Scrape**: Scrapes 15 stores from the city currently selected in the applet (Default: "Initial City") via `scrape_city_stores(click_entry=True)`.
    2.  **Iterative City Switching**: Loops through `CITY_LIST` in `config.py`.
    3.  **Regional Transition**: Calls `switch_city(name)` to trigger the applet's internal city selector.
    4.  **Targeted Scrape**: Once switched, calls `scrape_city_stores(click_entry=False)` to collect results for the new region.
    5.  **Unified Export**: Aggregates all results into `multi_city_stores.xlsx` with city labels and timestamps.

### 2. City Switching Logic
**Module**: `city_switching.py`
**Goal**: Automate region selection using OCR and Pinyin calculation.

*   **`switch_city(city_name)`**:
    1.  **Trigger Detection**: Uses full-screen OCR to find the keyword **"搜索门店"** (Search Store) and clicks **89 pixels to the left** to open the city selector.
    2.  **Index Navigation**: Calculates the city's first Pinyin character (e.g., "H" for "杭州") using `pypinyin`.
    3.  **Sidebar Interaction**: Searches for the index character in the sidebar using OCR and clicks it to jump to the correct alphabetical section.
    4.  **City Search**: Scans the scrollable city list for the target name and clicks it, with an automatic scroll-down fallback (max 10 retries).

### 3. Vision & OCR Engine
**Module**: `ocr_extractor.py`
**Goal**: Low-latency, high-accuracy localization and character recognition.

*   **`ChageeOCRExtractor`**:
    -   **`extract_data`**: Detects store boxes, deduplicates via area-aware sorting, and crops Store Name/Order Status regions using fixed pixel offsets.
    -   **`ocr_full_image`**: Runs full detection-recognition OCR to localize UI elements like buttons or keywords.
    -   **Core**: Powered by **PaddleOCR** with 4x upscaling and aggressive text cleaning (mapping common OCR hallucinations like `芸` -> `荟`).

## Configuration
**Module**: `config.py`
Contains the `CITY_LIST` of tuples: `(city_name, target_store_count, initial_location)`.

## Directory Structure
- `ui_modules/applet_interact.py`: Multi-city orchestration driver.
- `city_switching.py`: Region navigation logic using relative OCR positioning.
- `ocr_extractor.py`: Brain of the scraper (Localization + Character recognition).
- `config.py`: Global settings and city targets.
- `multi_city_stores.xlsx`: The unified output file.

## Operational Prerequisites
- **PaddleOCR**: Local GPU acceleration (RTX series recommended).
- **uiautomation**: Windows UI accessibility bridge.
- **Robust I/O**: The system uses numpy-buffered reading/writing to ensure compatibility with Chinese file paths in the workspace.
