import uiautomation as auto

def list_windows():
    print("Listing all top-level windows...")
    for window in auto.GetRootControl().GetChildren():
        if window.ClassName:
            print(f"Name: {window.Name} | Class: {window.ClassName}")

if __name__ == "__main__":
    list_windows()
