import scraping_logic
import time

def test_switch():
    applet = scraping_logic.get_applet_window()
    if not applet:
        print("Applet window not found.")
        return
        
    applet.SetActive()
    time.sleep(1)
    
    print("Testing new coordinate-based city switch method...")
    success = scraping_logic.switch_city(applet, '杭州', 1)
    print("City switch successful:", success)

if __name__ == "__main__":
    test_switch()
