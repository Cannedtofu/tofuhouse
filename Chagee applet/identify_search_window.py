import uiautomation as auto
import time

def list_search_windows():
    print("Searching for the WeChat search result window...")
    # Recent WeChat versions open a separate window for search results
    # It might have ClassName 'WebViewWnd' or 'Chrome_WidgetWin_0'
    
    # Let's list all windows to find anything related to search or wechat
    for window in auto.GetRootControl().GetChildren():
        name = window.Name
        classname = window.ClassName
        if "微信" in name or "搜索" in name or classname in ["WebViewWnd", "Chrome_WidgetWin_0"]:
            print(f"Found Window -> Name: {name} | Class: {classname}")
            
            # If we find a likely candidate, let's look for "小程序"
            target = window.TextControl(SubName="小程序")
            if not target.Exists(0, 0):
                target = window.ButtonControl(SubName="小程序")
            if not target.Exists(0, 0):
                target = window.PaneControl(SubName="小程序")
                
            if target.Exists(1, 0):
                print(f"  SUCCESS: Found '小程序' element inside this window!")
                print(f"  Type: {target.ControlTypeName} | ID: {target.AutomationId}")
            else:
                print("  '小程序' text not found in this window yet.")

if __name__ == "__main__":
    list_search_windows()
