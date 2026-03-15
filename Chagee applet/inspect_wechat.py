import uiautomation as auto
import time

def inspect_wechat():
    print("Inspecting WeChat window controls...")
    wechat_window = auto.WindowControl(Name="微信", ClassName="Qt51514QWindowIcon")
    if not wechat_window.Exists(3, 1):
        print("Could not find WeChat window.")
        return

    # List children to find the search bar
    for control in wechat_window.GetChildren():
        print(f"Type: {control.ControlTypeName} | Name: {control.Name}")

if __name__ == "__main__":
    inspect_wechat()
