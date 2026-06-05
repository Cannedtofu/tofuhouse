import os
import time
import logging
import argparse
import config
from wechat_interaction import search_and_open_applet
from scraping_logic import main_workflow
from data_manager import calculate_daily_stats
from email_sender import send_report_email
from cleanup_manager import close_chagee_windows
from analyze_stores import analyze_stores, cups_per_store_chart

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def run_daily_automation(skip_init=False, skip_scrape=False, skip_report=False, skip_cleanup=False):
    """
    The master orchestrator for the Chagee daily scraping task.
    """
    max_restarts = 3
    restart_count = 0
    
    while restart_count <= max_restarts:
        logger.info(f"=== Starting Chagee Daily Automation (Attempt {restart_count + 1}) ===")
        
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
        data_found = True
        if not skip_report:
            project_dir = os.path.dirname(os.path.abspath(__file__))
            output_file = os.path.join(project_dir, "multi_city_stores.xlsx")
            
            if os.path.exists(output_file):
                stats = calculate_daily_stats(output_file)
                if stats and stats.get('total_stores', 0) > 0:
                    logger.info(f"Stats calculated: {stats['total_stores']} stores, {stats['total_cups']} cups.")
                    
                    # Perform Week-on-Week analysis
                    logger.info("Performing Week-on-Week analysis...")
                    wow_report = analyze_stores(output_file)

                    logger.info("Generating cups-per-store trend chart...")
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
                        logger.info("Email skipped (SEND_EMAIL=False in config).")
                else:
                    logger.warning("No stats could be calculated or 0 stores found.")
                    data_found = False
            else:
                logger.error(f"Output file missing at {output_file}. Skipping report.")
                data_found = False
        else:
            logger.info("Skipping reporting and email (--skip-report).")

        # 4. Cleanup: Ensure all windows are closed
        if not skip_cleanup or not data_found:
            logger.info("Automation step finished. Running cleanup (closing applet and search windows)...")
            close_chagee_windows()
        else:
            logger.info("Skipping final cleanup (--skip-cleanup). Applet windows will remain open.")
        
        if data_found or skip_scrape:
            logger.info("=== Chagee Daily Automation Finished Successfully ===")
            return True
            
        restart_count += 1
        if restart_count <= max_restarts:
            logger.warning(f"No data added (0 stores found). Restarting whole script... (Restart {restart_count}/{max_restarts})")
            time.sleep(2)
        else:
            logger.error("Max restarts reached. Failed to gather data.")
            return False

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
