from scraper import setup_driver
import time
import os

def test_privacy_html():
    driver = setup_driver()
    driver.get("https://www.popmart.com/us/store-list")
    print("Waiting 5 seconds for privacy blur to appear...")
    time.sleep(5)
    
    # Save a screenshot
    screenshot_path = os.path.join(os.path.dirname(__file__), "privacy_blur_test.png")
    driver.save_screenshot(screenshot_path)
    print(f"Saved blur screenshot to {screenshot_path}")
    
    # Dump entire body HTML to a file
    html_path = os.path.join(os.path.dirname(__file__), "body_dump.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(driver.execute_script("return document.body.outerHTML;"))
    print(f"Saved body HTML to {html_path}")
    
    driver.quit()

if __name__ == "__main__":
    test_privacy_html()
