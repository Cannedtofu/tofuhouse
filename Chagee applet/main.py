import subprocess
import time
import os
import sys

def run_main_workflow(applet_name):
    print("Starting WeChat Applet Scraper Workflow")
    
    # 1. Ensure output dir exists
    if not os.path.exists("data"):
        os.makedirs("data")

    # 2. Start Mitmproxy in the background
    print("Starting mitmproxy...")
    proxy_cmd = ["mitmdump", "-s", "proxy/interceptor.py", "-p", "8080"]
    # We use Popen so it runs independently while we do UI automation
    proxy_process = subprocess.Popen(proxy_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    time.sleep(3) # Give proxy time to start

    # 3. Import and run UI automation
    # Make sure to set up your VPN/Netch to route WeChat traffic through localhost:8080 before running!
    try:
        from ui_modules.core_wechat import focus_wechat_and_open_search
        from ui_modules.applet_nav import search_and_open_applet
        from ui_modules.applet_interact import interact_with_applet
        
        print("\n--- Starting UI Automation ---")
        if focus_wechat_and_open_search():
            if search_and_open_applet(applet_name):
                interact_with_applet(applet_name)
                
                # Wait for data to be intercepted by proxy
                print("\nWaiting 10 seconds to allow network requests to finish...")
                time.sleep(10)
                
    except Exception as e:
        print(f"An error occurred during UI automation: {e}")
    finally:
        # 4. Clean up proxy process
        print("Stopping proxy process...")
        proxy_process.terminate()
        proxy_process.wait()
        print("Workflow completed.")

if __name__ == "__main__":
    target_applet = "霸王茶姬小程序" # Update this
    run_main_workflow(target_applet)
