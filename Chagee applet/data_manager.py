import os
import pandas as pd
from datetime import datetime
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def save_results_to_excel(all_results, output_file=None):
    """Saves the results of the scraping to an Excel file, appending if it exists."""
    if not all_results:
        print("No results to save.")
        return
        
    if output_file is None:
        project_dir = os.path.dirname(os.path.abspath(__file__))
        output_file = os.path.join(project_dir, "multi_city_stores.xlsx")

    export_data = []
    for r in all_results:
        export_data.append({
            "City": r.get('City', 'Unknown'),
            "Store Name": r['store_name'] if 'store_name' in r else r.get('Store Name', ''),
            "Order Status": r['order_status'] if 'order_status' in r else r.get('Order Status', ''),
            "Cup Count": r['cup_count'] if 'cup_count' in r else r.get('Cup Count', 0),
            "Date": r.get('Date', ''),
            "Time": r.get('Time', ''),
            "Day": r.get('Day', '')
        })
        
    df_new = pd.DataFrame(export_data)
    
    if os.path.exists(output_file):
        try:
            df_old = pd.read_excel(output_file)
            # Remove any empty or malformed rows if necessary
            df_old = df_old.dropna(subset=['Store Name'])
            
            # Append new data to old data
            df_final = pd.concat([df_old, df_new], ignore_index=True)
            print(f"\nAppended {len(df_new)} new results to existing {len(df_old)} records.")
        except Exception as e:
            print(f"Error reading existing file ({e}). Saving new data only.")
            df_final = df_new
    else:
        df_final = df_new
        print(f"\nCreated new output file with {len(df_new)} results.")

    df_final.to_excel(output_file, index=False)
    print(f"Total records in historical file: {len(df_final)}. Saved to {output_file}")

def calculate_daily_stats(excel_path):
    """Calculates statistics for today's scrape from the historical Excel file."""
    if not os.path.exists(excel_path):
        logger.error(f"Output file {excel_path} not found!")
        return None

    try:
        # Load the results
        df = pd.read_excel(excel_path)
        
        # Remove any empty or malformed rows
        df = df.dropna(subset=['Store Name'])

        # Filter for only the current day's scraping
        today_date = datetime.now().strftime("%Y-%m-%d")
        
        if 'Date' in df.columns:
            try:
                # Handle strings, timestamps, and mixed types
                df_standardized_date = pd.to_datetime(df['Date']).dt.strftime('%Y-%m-%d')
                df_latest = df[df_standardized_date == today_date]
            except Exception as e:
                logger.warning(f"Standard date conversion failed ({e}), falling back to string comparison")
                df_latest = df[df['Date'].astype(str) == today_date]
            
            if df_latest.empty:
                logger.info(f"No new data found for today ({today_date}).")
                return None
                
            logger.info(f"Summarizing stats for today's scrape: {today_date}")
        else:
            logger.error("No 'Date' column was found in the Excel file.")
            return None
        
        total_stores = len(df_latest)
        total_cups = int(df_latest['Cup Count'].sum())
        
        stores_per_city = df_latest.groupby('City').size().sort_values(ascending=False)
        cups_per_city = df_latest.groupby('City')['Cup Count'].sum().sort_values(ascending=False)
        
        # Format the city-specific stats text
        stats_text = f"数据日期: {today_date}\n"
        stats_text += "--- 各城市已爬取门店数 ---\n"
        for city, count in stores_per_city.items():
            stats_text += f"  {city}: {count}\n"
            
        stats_text += "\n--- 各城市总制作杯数 ---\n"
        for city, count in cups_per_city.items():
            stats_text += f"  {city}: {int(count)}\n"
        
        return {
            "total_stores": total_stores,
            "total_cups": total_cups,
            "stats_text": stats_text,
            "today_date": today_date
        }
        
    except Exception as e:
        logger.error(f"Error calculating stats: {e}")
        return None

def export_results_to_excel_from_samples():
    """Logic from legacy export_to_excel.py for processing OCR samples."""
    # Since this logic relies on externalocr_extractor, we'll keep it as defined in existing script
    try:
        from scraping_logic import ChageeOCRExtractor
        extractor = ChageeOCRExtractor()
        project_dir = os.path.dirname(os.path.abspath(__file__))
        base_path = os.path.join(project_dir, "OCR_sample")
        
        if not os.path.exists(base_path):
             print(f"Sample path {base_path} not found.")
             return

        # Get current timestamp info
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M")
        day_str = now.strftime("%A")
        
        # Get all png files
        image_files = [f for f in os.listdir(base_path) if f.endswith('.png') and f.startswith('test_')]
        image_files.sort()
        
        all_results = []
        for img_file in image_files:
            img_path = os.path.join(base_path, img_file)
            print(f"Processing {img_file}...")
            results = extractor.extract_data(img_path)
            for res in results:
                all_results.append({
                    "Store Name": res['store_name'],
                    "Order Status": res['order_status'],
                    "Cup Count": res['cup_count'],
                    "Date": date_str,
                    "Time": time_str,
                    "Day": day_str
                })
        
        if all_results:
            df = pd.DataFrame(all_results)
            df = df[["Store Name", "Order Status", "Cup Count", "Date", "Time", "Day"]]
            output_file = os.path.join(base_path, "ocr_results.xlsx")
            df.to_excel(output_file, index=False)
            print(f"Results successfully exported to {output_file}")
    except Exception as e:
         print(f"Error in sample export: {e}")

if __name__ == "__main__":
    # Test stats
    project_dir = os.path.dirname(os.path.abspath(__file__))
    result_file = os.path.join(project_dir, "multi_city_stores.xlsx")
    stats = calculate_daily_stats(result_file)
    if stats:
        print(stats['stats_text'])
