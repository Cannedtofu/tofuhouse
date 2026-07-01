import os
import time
import logging
import argparse
import config
from wechat_interaction import search_and_open_applet
from scraping_logic import scrape_city_stores, switch_city, GumingOCRExtractor
from data_manager import calculate_daily_stats, save_results_to_excel
from email_sender import send_report_email
from cleanup_manager import close_guming_windows
from analyze_stores import analyze_stores, cups_per_store_chart
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def run_daily_automation(skip_init=False, skip_scrape=False, skip_report=False, skip_cleanup=False):
    """
    The master orchestrator for Guming daily scraping tasks.
    """
    max_restarts = 3
    restart_count = 0
    
    while restart_count <= max_restarts:
        logger.info(f"=== Starting Guming Daily Automation (Attempt {restart_count + 1}) ===")
        
        # 1. Initialize: Open WeChat and launch Guming applet
        if not skip_init:
            applet_name = "古茗"
            applet_window = search_and_open_applet(applet_name)
            
            if not applet_window:
                logger.error(f"Failed to initialize: Guming applet could not be opened.")
                return False
                
            logger.info("Successfully opened Guming applet. Starting scraping workflow...")
            time.sleep(2)
        else:
            logger.info("Skipping initialization. Assuming Guming applet is already open.")
            # Try to grab open window
            applet_window = None
            import uiautomation as auto
            for window in auto.GetRootControl().GetChildren():
                if window.ClassName == "Chrome_WidgetWin_0" and window.Name != "微信" and window.Name != "":
                    applet_window = window
                    break
            if not applet_window:
                logger.error("Applet window not found on screen. Cannot skip init.")
                return False
                
        # 2. Scrape: Multi-city loop
        all_results = []
        if not skip_scrape:
            try:
                extractor = GumingOCRExtractor()
                
                # Enter the store list page first by clicking the "点单" tab
                import uiautomation as auto
                rect = applet_window.BoundingRectangle
                entry_x = rect.left + config.STORE_LIST_ENTRY_REL_COORD[0]
                entry_y = rect.top + config.STORE_LIST_ENTRY_REL_COORD[1]
                logger.info(f"Entering store list: Clicking '点单' tab at absolute ({entry_x}, {entry_y})")
                applet_window.SetActive()
                time.sleep(0.5)
                auto.Click(entry_x, entry_y)
                time.sleep(2)
                
                # Iterate through all target cities, switch to each, and scrape
                for city_name, target_count, _, sub_regions in config.CITY_LIST:
                    if switch_city(applet_window, city_name, extractor):
                        # After switching city, we are already inside the store list page
                        city_res = scrape_city_stores(applet_window, extractor, target_count, city_name, sub_regions=sub_regions, click_entry=False)
                        now = datetime.now()
                        for r in city_res:
                            r['Date'] = now.strftime("%Y-%m-%d")
                            r['Time'] = now.strftime("%H:%M")
                            r['Day'] = now.strftime("%A")
                        all_results.extend(city_res)
                    else:
                        logger.error(f"Failed to switch city to {city_name}. Skipping.")
                
                # Save scraped data
                if all_results:
                    save_results_to_excel(all_results)
                    
            except Exception as e:
                logger.error(f"Error during Guming scraping workflow: {e}")
                import traceback
                logger.error(traceback.format_exc())
        else:
            logger.info("Skipping scraping workflow (--skip-scrape).")
            
        # 3. Data & Report: Calculate metrics and email reports
        data_found = True
        if not skip_report:
            project_dir = os.path.dirname(os.path.abspath(__file__))
            output_file = os.path.join(project_dir, "guming_city_stores.xlsx")
            
            if os.path.exists(output_file):
                stats = calculate_daily_stats(output_file)
                if stats and stats.get('total_stores', 0) > 0:
                    logger.info(f"Guming Stats: {stats['total_stores']} stores, {stats['total_cups']} cups.")
                    
                    logger.info("Performing Guming WoW Analysis...")
                    wow_report = analyze_stores(output_file)
                    
                    logger.info("Generating cups-per-store chart...")
                    chart = cups_per_store_chart(output_file)
                    
                    if config.SEND_EMAIL:
                        send_report_email(
                            stats['total_stores'],
                            stats['total_cups'],
                            stats['stats_text'],
                            output_file,
                            wow_analysis=wow_report,
                            chart_b64=chart
                        )
                    else:
                        logger.info("Email reports skipped (SEND_EMAIL=False in config).")
                else:
                    logger.warning("No Guming stats could be calculated or 0 stores found.")
                    data_found = False
            else:
                logger.error(f"Guming results spreadsheet missing at {output_file}.")
                data_found = False
        else:
            logger.info("Skipping reporting and email (--skip-report).")
            
        # 4. Cleanup: Close Guming applet and search result windows
        if not skip_cleanup or not data_found:
            logger.info("Scraper completed. Running cleanup...")
            close_guming_windows()
        else:
            logger.info("Skipping cleanup (--skip-cleanup). Applet window remains open.")
            
        if data_found or skip_scrape:
            logger.info("=== Guming Daily Automation Finished Successfully ===")
            return True
            
        restart_count += 1
        if restart_count <= max_restarts:
            logger.warning(f"No Guming data added. Restarting scraper... (Attempt {restart_count + 1}/{max_restarts + 1})")
            time.sleep(2)
        else:
            logger.error("Max restarts reached. Failed to gather Guming data.")
            return False

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Guming Applet Scraper Master Orchestrator")
    parser.add_argument("--skip-init", action="store_true", help="Skip WeChat interaction and applet opening")
    parser.add_argument("--skip-scrape", action="store_true", help="Skip the actual scraping loop")
    parser.add_argument("--skip-report", action="store_true", help="Skip calculating stats and sending email reports")
    parser.add_argument("--skip-cleanup", action="store_true", help="Skip closing applet windows at the end")
    
    args = parser.parse_args()
    
    run_daily_automation(
        skip_init=args.skip_init,
        skip_scrape=args.skip_scrape,
        skip_report=args.skip_report,
        skip_cleanup=args.skip_cleanup
    )
