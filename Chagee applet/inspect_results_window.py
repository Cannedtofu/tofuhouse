import uiautomation as auto
import time

def inspect_chrome_widget():
    print("Inspecting the '微信' (Chrome_WidgetWin_0) window...")
    win = auto.WindowControl(ClassName="Chrome_WidgetWin_0", Name="微信")
    if not win.Exists(1, 0):
        print("Window '微信' (Chrome_WidgetWin_0) not found.")
        return

    win.SetActive()
    print(f"Window found. Listing all descendants with any text...")
    
    # Let's try to find ANY control that contains '小程序' in its name
    found = False
    for ctrl in win.GetChildren():
        print(f"  Level 1: Type={ctrl.ControlTypeName}, Name='{ctrl.Name}', ID={ctrl.AutomationId}")
        for sub in ctrl.GetChildren():
            print(f"    Level 2: Type={sub.ControlTypeName}, Name='{sub.Name}'")
            if "小程序" in sub.Name:
                print(f"    >>> HIT: Found '{sub.Name}'")
                found = True
            for sub2 in sub.GetChildren():
                if "小程序" in sub2.Name:
                    print(f"      Level 3 HIT: Found '{sub2.Name}'")
                    found = True

    if not found:
        print("No matches for '小程序' found in top 3 levels.")

if __name__ == "__main__":
    inspect_chrome_widget()
