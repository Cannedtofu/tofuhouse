# Chagee Applet Scraper: Global Settings

# --- Workflow: City Selection (config.py / city_switching.py) ---
CITY_LIST = [
    ('杭州', 150, 'xxx'),
    ('深圳', 100, 'xxx'),
    ('成都', 150, 'xxx'),
    ('重庆', 100, 'xxx'),
    ('北京', 100, 'xxx'),
    ('广州', 100, 'xxx'),
]
DEFAULT_TARGET_COUNT = 200

# CITY_LIST = [
#     ('杭州', 15, 'xxx'),
#     ('深圳', 10, 'xxx'),
#     ('成都', 15, 'xxx'),
#     ('重庆', 10, 'xxx'),
#     ('北京', 10, 'xxx'),
#     ('广州', 10, 'xxx'),
# ]
# DEFAULT_TARGET_COUNT = 20

# Coordinate for city selection trigger (Relative to Applet Window)
# Method: Locate "搜索门店" and shift left
CITY_TRIGGER_KEYWORD = "搜索门店"
CITY_TRIGGER_OFFSET_X = -89 

# Sidebar Index Region (A-Z list)
CITY_INDEX_REGION = {
    'x_min': 380, 'x_max': 410,
    'y_min': 670, 'y_max': 1100
}

# --- Workflow: Applet Interaction (applet_interact.py) ---
# Main entry click on the applet home screen to enter store list
STORE_LIST_ENTRY_REL_COORD = (120, 600)

# Scrolling starting point
SCROLL_REL_COORD = (200, 566)

# Scraping Logic

MAX_NO_NEW_SCROLLS = 4  # Abort city if this many scrolls yield no new data
SCROLL_WHEEL_TIMES = 5  # Strength of each scroll

# --- Workflow: OCR Extraction (ocr_extractor.py) ---
# Region of Interest (ROI) - ignore top % of screen
ROI_TOP_IGNORE_PERCENT = 0.12

# Store Box Constraints (Detection)
BOX_MIN_WIDTH = 320
BOX_MAX_WIDTH = 450
BOX_MIN_HEIGHT = 50
BOX_MAX_HEIGHT = 500

# Text Cropping Offsets (Inside each store box)
# Store Name vertical range
SN_CROP_Y_START = 22
SN_CROP_HEIGHT = 24

# Order Status vertical range
OS_CROP_Y_START = 46
OS_CROP_HEIGHT = 24

# Horizontal Cutoff (% of box width to keep from left)
BOX_WIDTH_CUTOFF_PERCENT = 0.7

# --- New Feature: High Threshold Screenshot (ocr_extractor.py) ---
SCREENSHOT_ON_THRESHOLD = True  # Toggle for capturing high-volume stores
CUP_COUNT_THRESHOLD = 80       # Capture store screenshot if cup count is >= this
DATA_FOLDER = "data"           # Folder to store threshold screenshots
