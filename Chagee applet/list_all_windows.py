import uiautomation as auto

def list_all():
    print("Listing ALL windows currently on desktop...")
    for window in auto.GetRootControl().GetChildren():
        print(f"Name: {window.Name} | Class: {window.ClassName}")

if __name__ == "__main__":
    list_all()
