# Chagee Applet Scraper Project Notes

## Project Overview
This project is designed to automate the scraping of data from the "Chagee" (霸王茶姬) WeChat applet. Because WeChat applets run within the WeChat container, we use a hybrid approach:
1. **UI Automation** (`uiautomation` library): To mimic human interaction, search for the applet, open it, and navigate through the UI to trigger data generation.
2. **Network Interception** (`mitmproxy`): To run in the background, intercept the backend API calls made by the applet, and capture the JSON payload directly.

## Directory Structure
- `main.py`: The entry point orchestration script. It spins up the `mitmproxy` interceptor in the background and sequentially calls the UI automation modules.
- `proxy/interceptor.py`: The `mitmproxy` addon script. It filters network traffic searching for the specific API endpoint and dumps the JSON payload into a `data` folder.
- `ui_modules/`:
  - `core_wechat.py`: Handles finding the WeChat application window, bringing it to the foreground, and focusing the search bar.
  - `applet_nav.py`: Responsible for searching the specific applet ("Chagee" / 霸王茶姬) and clicking it to open the applet window.
  - `applet_interact.py`: Coordinates the in-applet UI clicks (e.g., clicking the menu, store locator, scrolling) to push the applet into making the target data request.
- `test_ui.py`: A convenience script to test individual steps independently (`--step 1`, `--step 2`, etc.).

## Prerequisites & Setup (Netch / Proxifier)
To intercept the traffic successfully, network routing is required:
1. **Mitmproxy Port**: `mitmproxy` runs on `127.0.0.1:8080` (by default configured in `main.py`).
2. **Traffic Routing**: You must use a tool like **Netch** or **Proxifier** to force the `WeChatAppEx.exe` (the WeChat applet process) to route its traffic through `127.0.0.1:8080`.
3. **Certificate Installation**: The `mitmproxy` CA certificate (`%USERPROFILE%\.mitmproxy\mitmproxy-ca-cert.cer`) must be installed in the Windows **Trusted Root Certification Authorities** store so the applet trusts the HTTPS interception.

## Next Steps / TODOs
- **Applet Name Configuration**: Verify the exact search name for the Chagee applet inside the script.
- **UI Logic Customization**: Update `ui_modules/applet_interact.py` with the exact UI tags, names, or coordinates needed to navigate the Chagee applet. You can use the Accessibility Insights tool or `uiautomation` inspect mechanisms to find the right elements.
- **API Target Identification**: We need to use Charles Proxy or Mitmproxy web interface to manually identify the exact URL fragment of the Chagee data API, and update `proxy/interceptor.py` with this target.

## Running the Complete Flow
1. Ensure Netch/Proxifier is running and routing `WeChatAppEx.exe`.
2. Activate Virtual Environment: `.venv\\Scripts\\activate`
3. Run the orchestrator: `python main.py`
