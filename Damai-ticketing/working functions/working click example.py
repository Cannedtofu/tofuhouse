from appium import webdriver
from appium.options.android import UiAutomator2Options
from PIL import Image, ImageDraw

# --- Appium server URL ---
APPIUM_SERVER_URL = "http://127.0.0.1:4723"

# --- Capabilities ---
options = UiAutomator2Options()
options.platform_name = "Android"
options.device_name = "emulator-5554"
options.automation_name = "UiAutomator2"

# MuMu / stability flags
options.no_reset = True
options.full_reset = False
options.skip_device_initialization = True
options.skip_server_installation = True
options.ignore_unimportant_views = False

# --- Start Appium session ---
print("[DEBUG] Starting Appium session...")
driver = webdriver.Remote(APPIUM_SERVER_URL, options=options)
print("[DEBUG] Session started")

# --- Known bounds of element ---
x1, y1 = 120, 498
x2, y2 = 237, 526

# Calculate center coordinates
cx = (x1 + x2) // 2
cy = (y1 + y2) // 2
print(f"[DEBUG] Element center coordinates: ({cx}, {cy})")

# --- Screenshot before tap ---
driver.save_screenshot("before_tap.png")

# Highlight tap location
im = Image.open("before_tap.png")
draw = ImageDraw.Draw(im)
r = 20  # radius of highlight circle
draw.ellipse((cx-r, cy-r, cx+r, cy+r), outline="red", width=3)
im.save("before_tap_highlight.png")
print("[DEBUG] Highlighted tap location saved as 'before_tap_highlight.png'")

# --- Perform tap ---
driver.tap([(cx, cy)], duration=100)
print("[DEBUG] Element clicked successfully via driver.tap")

# --- Screenshot after tap ---
driver.save_screenshot("after_tap.png")
print("[DEBUG] Screenshot after tap saved as 'after_tap.png'")

# --- Optional cleanup ---
# driver.quit()
