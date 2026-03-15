import uiautomation as auto

def inspect_wechat_deep():
    print("Inspecting WeChat window controls (Depth 2)...")
    wechat_window = auto.WindowControl(Name="微信", ClassName="Qt51514QWindowIcon")
    if not wechat_window.Exists(3, 1):
        print("Could not find WeChat window.")
        return

    # Print children recursively to find the edit box
    def print_children(control, depth=0):
        if depth > 3: return
        for child in control.GetChildren():
            print("  " * depth + f"Type: {child.ControlTypeName} | Name: {child.Name} | ID: {child.AutomationId}")
            print_children(child, depth + 1)

    print_children(wechat_window)

if __name__ == "__main__":
    inspect_wechat_deep()
