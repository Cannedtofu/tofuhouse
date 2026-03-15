import argparse
import sys
from ui_modules.core_wechat import focus_wechat_and_open_search
from ui_modules.applet_nav import (
    search_applet_only, 
    find_search_result_window, 
    click_xiaochengxu_in_window,
    click_xiaochengxu_button
)
from ui_modules.applet_interact import interact_with_applet

def main():
    parser = argparse.ArgumentParser(description="Test separate UI automation modules.")
    parser.add_argument("--step", type=int, choices=[1, 2, 3, 4, 5, 6], 
                        help="1: Focus Search, 2: Open Applet, 3: Interact, 4: Find Search Window, 5: Click '小程序', 6: Dump Window Tree")
    parser.add_argument("--applet", type=str, default="霸王茶姬小程序", help="Name of the applet")
    
    args = parser.parse_args()
    
    if args.step == 1:
        focus_wechat_and_open_search()
    elif args.step == 2:
        search_applet_only(args.applet)
    elif args.step == 3:
        interact_with_applet(args.applet)
    elif args.step == 4:
        find_search_result_window()
    elif args.step == 5:
        click_xiaochengxu_button()
    elif args.step == 6:
        window = find_search_result_window()
        if window:
            def dump_ctrl(ctrl, depth=0):
                if depth > 4: return
                print(f"{'  '*depth}[{ctrl.ControlTypeName}] '{ctrl.Name}' | ID: {ctrl.AutomationId}")
                for child in ctrl.GetChildren():
                    dump_ctrl(child, depth + 1)
            dump_ctrl(window)
    else:
        print("Running full UI sequence...")
        if focus_wechat_and_open_search():
            if search_and_open_applet(args.applet):
                interact_with_applet(args.applet)
                
if __name__ == "__main__":
    main()
