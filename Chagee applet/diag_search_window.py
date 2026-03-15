import uiautomation as auto
import time

def identify_search_window():
    print("Enumerating all top-level windows...")
    root = auto.GetRootControl()
    
    # We'll look for windows that might be the search result
    # Often they have class 'WebViewWnd', 'Chrome_WidgetWin_0', or are related to '微信'
    candidates = []
    for window in root.GetChildren():
        name = window.Name
        classname = window.ClassName
        # Skip common system stuff
        if classname in ["Shell_TrayWnd", "Progman", "Button", "IME"]:
            continue
            
        print(f"Window -> Name: '{name}' | Class: '{classname}'")
        candidates.append(window)

    print("\n--- Deep Inspection of likely candidates ---")
    for window in candidates:
        # Check if '小程序' exists anywhere in this window
        target = window.Control(searchDepth=10, Name="小程序")
        if target.Exists(0, 0):
            print(f"SUCCESS! Found '小程序' inside window: '{window.Name}' | Class: '{window.ClassName}'")
            print(f"Parent of '小程序': {target.GetParentControl().Name if target.GetParentControl() else 'None'}")
            return window
        
    print("\nCould not find '小程序' in any window names or immediate descendants.")
    return None

if __name__ == "__main__":
    identify_search_window()
