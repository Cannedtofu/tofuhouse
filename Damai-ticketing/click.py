from PIL import Image, ImageDraw
from appium.webdriver.webdriver import WebDriver
from typing import Tuple

def click_by_bounds(
    driver: WebDriver,
    bounds: Tuple[int, int, int, int],
    duration: int = 100,
    debug: bool = False,
    debug_prefix: str = "tap"
) -> bool:
    """
    Clicks the center of a rectangle defined by bounds on an Appium driver.

    Args:
        driver: Appium WebDriver instance.
        bounds: (x1, y1, x2, y2) rectangle coordinates.
        duration: Tap duration in milliseconds.
        debug: If True, saves screenshots with tap highlighted.
        debug_prefix: Filename prefix for debug images.
    """
    x1, y1, x2, y2 = bounds
    cx = (x1 + x2) // 2
    cy = (y1 + y2) // 2

    if debug:
        print(f"[DEBUG] Clicking at center: ({cx}, {cy})")
        before_path = f"{debug_prefix}_before.png"
        highlight_path = f"{debug_prefix}_highlight.png"
        after_path = f"{debug_prefix}_after.png"

        # Screenshot before tap
        driver.save_screenshot(before_path)

        # Draw highlight
        with Image.open(before_path) as im:
            draw = ImageDraw.Draw(im)
            r = 20
            draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline="red", width=3)
            im.save(highlight_path)
        print(f"[DEBUG] Highlight saved: {highlight_path}")

    # Perform tap
    driver.execute_script("mobile: clickGesture", {
        "x": cx,
        "y": cy
    })

    if debug:
        driver.save_screenshot(after_path)
        print(f"[DEBUG] After-tap screenshot saved: {after_path}")

    return True
    
