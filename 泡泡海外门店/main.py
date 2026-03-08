import logging
import json
import os
from scraper import scrape_stores
from db import init_db, insert_scrape, get_last_two_scrapes
from compare import compare_stores, format_comparison_for_email
from emailer import send_update_email

# Configure logging for main process
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('main_scraper_orch')

def main():
    logger.info("Starting Popmart Scraper Orchestrator...")
    
    # 1. Ensure DB exists
    init_db()
    
    # 2. Run the scraper
    try:
        scraped_data = scrape_stores()
        if not scraped_data:
            logger.warning("Scraper returned no data. Aborting.")
            return
    except Exception as e:
        logger.error(f"Scraping failed: {e}")
        return
        
    # 3. Store the results in the DB
    try:
        # Load the JSON file created by the scraper
        with open('stores_data.json', 'r', encoding='utf-8') as f:
            scraped_data = json.load(f)
            
        scrape_id = insert_scrape(scraped_data)
        logger.info(f"Successfully stored {len(scraped_data)} stores in DB under scrape ID {scrape_id}")
    except Exception as e:
        logger.error(f"Database insertion failed: {e}")
        return
        
    # 4. Compare latest with previous
    try:
        current_stores, previous_stores = get_last_two_scrapes()
        
        # If there's only 1 scrape in the DB, it's the very first run.
        if len(previous_stores) == 0:
            logger.info("This is the first scrape. No comparison possible yet.")
            report_body = "这是首次检测。系统目前正在追踪 " + str(len(current_stores)) + " 家门店。尚无净增减数据可供比对。"
        else:
            comparison_results = compare_stores(current_stores, previous_stores)
            report_body = format_comparison_for_email(comparison_results)
            
        logger.info("\n" + report_body)
    except Exception as e:
        logger.error(f"Comparison logic failed: {e}")
        return

    # 5. Send Email
    try:
        html_body = f"<pre>{report_body}</pre>" # Simple HTML formatting for pre-formatted text
        
        # Provide path to stores_data.json if desired as attachment
        json_path = os.path.join(os.path.dirname(__file__), 'stores_data.json')
        
        send_update_email(html_body=html_body, plain_body=report_body, attachment_path=json_path)
    except Exception as e:
        logger.error(f"Emailer failed: {e}")

if __name__ == '__main__':
    # Initialize the database if it doesn't already exist
    init_db()
    main()
