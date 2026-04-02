import os
import time
import logging
import argparse
from wechat_interaction import search_and_open_applet
from scraping_logic import main_workflow
from data_manager import calculate_daily_stats
from email_sender import send_report_email
from cleanup_manager import close_chagee_windows
from analyze_stores import analyze_stores

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def run_daily_automation(skip_init=False, skip_scrape=False, skip_report=False, skip_cleanup=False):
    """
    The master orchestrator for the Chagee daily scraping task.
    """
    logger.info("=== Starting Chagee Daily Automation ===")
    
    # 1. Initialize: Open WeChat and find/open the Chagee applet
    if not skip_init:
        applet_name = "霸王茶姬小程序"
        applet_window = search_and_open_applet(applet_name)
        
        if not applet_window:
            logger.error(f"Failed to initialize: Applet '{applet_name}' could not be opened.")
            return False
            
        logger.info(f"Successfully opened {applet_name}. Starting scraping workflow...")
        time.sleep(5) # Buffer for applet to settle
    else:
        logger.info("Skipping initialization (WeChat interaction). Assuming applet is already open.")
    
    # 2. Scrape: Run the city-switching and data extraction loop
    if not skip_scrape:
        try:
            main_workflow()
        except Exception as e:
            logger.error(f"Error during scraping workflow: {e}")
            import traceback
            logger.error(traceback.format_exc())
    else:
        logger.info("Skipping scraping workflow (--skip-scrape).")
    
    # 3. Data & Report: Calculate stats from the generated Excel and email it
    if not skip_report:
        project_dir = os.path.dirname(os.path.abspath(__file__))
        output_file = os.path.join(project_dir, "multi_city_stores.xlsx")
        
        if os.path.exists(output_file):
            stats = calculate_daily_stats(output_file)
            if stats:
                logger.info(f"Stats calculated: {stats['total_stores']} stores, {stats['total_cups']} cups.")
                
                # Perform Week-on-Week analysis
                logger.info("Performing Week-on-Week analysis...")
                wow_report = analyze_stores(output_file)
                
                send_report_email(
                    stats['total_stores'], 
                    stats['total_cups'], 
                    stats['stats_text'], 
                    output_file,
                    wow_analysis=wow_report
                )
            else:
                logger.warning("No stats could be calculated (maybe no new data for today).")
        else:
            logger.error(f"Output file missing at {output_file}. Skipping report.")
    else:
        logger.info("Skipping reporting and email (--skip-report).")

    # 4. Cleanup: Ensure all windows are closed
    if not skip_cleanup:
        logger.info("Automation finished. Running final cleanup...")
        close_chagee_windows()
    else:
        logger.info("Skipping final cleanup (--skip-cleanup). Applet windows will remain open.")
    
    logger.info("=== Chagee Daily Automation Finished ===")
    return True

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Chagee Applet Scraper Master Orchestrator")
    parser.add_argument("--skip-init", action="store_true", help="Skip WeChat interaction and applet opening (e.g. if already manually opened)")
    parser.add_argument("--skip-scrape", action="store_true", help="Skip the actual scraping workflow")
    parser.add_argument("--skip-report", action="store_true", help="Skip calculating stats and sending the email report")
    parser.add_argument("--skip-cleanup", action="store_true", help="Skip closing the applet windows at the end")
    
    args = parser.parse_args()
    
    run_daily_automation(
        skip_init=args.skip_init,
        skip_scrape=args.skip_scrape,
        skip_report=args.skip_report,
        skip_cleanup=args.skip_cleanup
    )
