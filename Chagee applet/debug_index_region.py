import uiautomation as auto
import cv2
import numpy as np
import os
import time
import config

def debug_index_region():
    print("Finding applet window to debug index region...")
    applet_window = None
    for window in auto.GetRootControl().GetChildren():
        if window.ClassName == "Chrome_WidgetWin_0" and window.Name != "微信" and window.Name != "":
            applet_window = window
            break
            
    if not applet_window:
        print("Applet window not found. Please open the city selection screen.")
        return

    print(f"Focusing window: {applet_window.Name}")
    applet_window.SetActive()
    applet_window.SetTopmost(True)
    time.sleep(1) # Wait for focus to settle
    applet_window.SetTopmost(False)
    
    rect = applet_window.BoundingRectangle
    screenshot_path = "debug_index_region_full.png"
    applet_window.CaptureToImage(screenshot_path)
    
    # Load with numpy to handle potential path encoding issues
    with open(screenshot_path, 'rb') as f:
        img = cv2.imdecode(np.frombuffer(f.read(), np.uint8), cv2.IMREAD_COLOR)
    
    if img is not None:
        idx_reg = config.CITY_INDEX_REGION
        x1, x2 = idx_reg['x_min'], idx_reg['x_max']
        y1, y2 = idx_reg['y_min'], idx_reg['y_max']
        
        # Draw the region on the full image for context
        cv2.rectangle(img, (x1, y1), (x2, y2), (0, 255, 0), 2)
        
        # Crop the region
        crop = img[y1:y2, x1:x2]
        
        # Save results
        def save_img(path, img):
            _, ext = os.path.splitext(path)
            res, encode = cv2.imencode(ext, img)
            if res:
                with open(path, 'wb') as f: f.write(encode)
        
        save_img("debug_index_region_cropped.png", crop)
        save_img("debug_index_region_marked.png", img)
        print(f"Region saved to debug_index_region_cropped.png and debug_index_region_marked.png")
    else:
        print("Failed to capture/load image.")

if __name__ == "__main__":
    debug_index_region()
