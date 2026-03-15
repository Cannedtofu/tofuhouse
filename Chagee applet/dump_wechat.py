import uiautomation as auto

def dump_tree():
    wechat_window = auto.WindowControl(ClassName="Qt51514QWindowIcon")
    if wechat_window.Exists(1, 0):
        print("WeChat Window found. Dumping children...")
        # Get all descendants might be too much, let's try a structured walk
        def walk(control, depth=0):
            if depth > 5: return
            name = control.Name
            type_name = control.ControlTypeName
            print("  " * depth + f"{type_name}: '{name}'")
            for child in control.GetChildren():
                walk(child, depth + 1)
        
        walk(wechat_window)
    else:
        print("WeChat not found.")

if __name__ == "__main__":
    dump_tree()
