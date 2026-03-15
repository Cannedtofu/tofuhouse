import uiautomation as auto

def inspect_wechat_search_results():
    print("Looking for '小程序' inside WeChat window structure...")
    # Find the main WeChat window
    wechat_window = auto.WindowControl(ClassName="Qt51514QWindowIcon")
    if not wechat_window.Exists(1, 0):
        print("WeChat window not found.")
        return

    # Look for ANY child that has "小程序" in its name or its subtree
    def find_xiaochengxu(control, depth=0):
        if depth > 8: return None
        
        # Check this control
        if "小程序" in control.Name:
            return control
        
        # Check children
        for child in control.GetChildren():
            res = find_xiaochengxu(child, depth + 1)
            if res: return res
        return None

    target = find_xiaochengxu(wechat_window)
    if target:
        print(f"FOUND! Name: {target.Name} | Type: {target.ControlTypeName}")
        print("Visualizing context...")
        parent = target.GetParentControl()
        if parent:
            print(f"Parent Name: {parent.Name} | Type: {parent.ControlTypeName}")
    else:
        print("Could not find any element with '小程序' in name.")

if __name__ == "__main__":
    inspect_wechat_search_results()
