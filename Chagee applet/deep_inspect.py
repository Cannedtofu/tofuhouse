import uiautomation as auto
import time

def deep_inspect(window):
    print(f"Deep Inspecting Window: '{window.Name}'")
    def walk(control, depth=0):
        if depth > 4: return
        try:
            name = control.Name
            type_name = control.ControlTypeName
            class_name = control.ClassName
            print("  " * depth + f"- [{type_name}] Name: '{name}', Class: '{class_name}'")
            for child in control.GetChildren():
                walk(child, depth + 1)
        except:
            pass

    walk(window)

if __name__ == "__main__":
    for window in auto.GetRootControl().GetChildren():
        if "霸王茶姬" in window.Name or (window.ClassName == "Chrome_WidgetWin_0" and window.Name != "微信" and window.Name != ""):
            deep_inspect(window)
            break
