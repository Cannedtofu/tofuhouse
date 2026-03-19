import uiautomation as auto
import time

def inspect_window():
    print("Inspecting window: '霸王茶姬'...")
    window = auto.WindowControl(searchDepth=1, Name="霸王茶姬", ClassName="Chrome_WidgetWin_0")
    if not window.Exists(0, 0):
        print("Window not found. Re-checking all windows...")
        for w in auto.GetRootControl().GetChildren():
            if "霸王茶姬" in w.Name:
                window = w
                break
    
    if window.Exists(1, 1):
        print(f"Window found: Name={window.Name}, ClassName={window.ClassName}")
        window.SetActive()
        
        # Take a screenshot to see if it's actually in focus
        window.CaptureToImage("d:\\代码项目\\Chagee applet\\chagee_window.png")
        print("Screenshot saved to chagee_window.png")
        
        # Try to find all buttons under the window
        print("Searching for buttons...")
        buttons = window.ButtonControl().GetChildren()
        print(f"Found {len(buttons)} immediate button children.")
        
        # Try a more exhaustive search for anything named "Close" or "关闭"
        print("Exhaustive search for '关闭' or 'Close' or '退出'...")
        close_controls = []
        for ctrl in window.GetChildren():
             if "关闭" in ctrl.Name or "Close" in ctrl.Name or "退出" in ctrl.Name:
                 close_controls.append(ctrl)
        
        # WeChat applets often have the close button in a specific toolbar
        # Let's just list the first 20 elements' types and names
        print("Listing first 20 children elements...")
        children = window.GetChildren()
        for i, child in enumerate(children[:20]):
            print(f"Child {i}: Name='{child.Name}', ControlType={child.ControlTypeName}, ClassName='{child.ClassName}'")
    else:
        print("Window not found.")

if __name__ == "__main__":
    inspect_window()
