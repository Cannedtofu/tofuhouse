import uiautomation as auto
import time

def find_xiaochengxu():
    print("Searching for any window with '小程序'...")
    # Iterate through all top-level windows
    for window in auto.GetRootControl().GetChildren():
        # Search for any control with "小程序" in its name
        # Using a deeper search depth
        target = window.Control(searchDepth=10, Name="小程序")
        if target.Exists(0, 0):
            print(f"FOUND '小程序' in Window: Name='{window.Name}', Class='{window.ClassName}'")
            print(f"  Element Type: {target.ControlTypeName}")
            return window, target
        
        # Try finding by substring manually if Name doesn't match exactly
        def search_recursive(ctrl, depth=0):
            if depth > 5: return None
            if "小程序" in ctrl.Name:
                return ctrl
            for child in ctrl.GetChildren():
                res = search_recursive(child, depth + 1)
                if res: return res
            return None
        
        target = search_recursive(window)
        if target:
            print(f"FOUND Substring '小程序' in Window: Name='{window.Name}', Class='{window.ClassName}'")
            print(f"  Element Name: {target.Name} | Type: {target.ControlTypeName}")
            return window, target
            
    return None, None

if __name__ == "__main__":
    time.sleep(2)
    find_xiaochengxu()
