import uiautomation as auto
import time

def list_all_windows():
    print("Listing all top-level windows...")
    for window in auto.GetRootControl().GetChildren():
        try:
            print(f"Name: '{window.Name}', ClassName: '{window.ClassName}', ProcessId: {window.ProcessId}")
        except Exception as e:
            print(f"Error getting window info: {e}")

if __name__ == "__main__":
    list_all_windows()
