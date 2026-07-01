import os
import pandas as pd
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

def save_results_to_excel(all_results, output_file=None):
    """Saves the results of the Guming scraping run to an Excel file, appending if it exists."""
    if not all_results:
        print("No Guming results to save.")
        return
        
    if output_file is None:
        project_dir = os.path.dirname(os.path.abspath(__file__))
        output_file = os.path.join(project_dir, "guming_city_stores.xlsx")

    export_data = []
    for r in all_results:
        export_data.append({
            "City": r.get('City', 'Unknown'),
            "Store Name": r['store_name'] if 'store_name' in r else r.get('Store Name', ''),
            "Order Status": r['order_status'] if 'order_status' in r else r.get('Order Status', ''),
            "Cup Count": r['cup_count'] if 'cup_count' in r else r.get('Cup Count', 0),
            "Order Count": r['order_count'] if 'order_count' in r else r.get('Order Count', 0),
            "Date": r.get('Date', ''),
            "Time": r.get('Time', ''),
            "Day": r.get('Day', '')
        })
        
    df_new = pd.DataFrame(export_data)
    
    if os.path.exists(output_file):
        try:
            df_old = pd.read_excel(output_file)
            df_old = df_old.dropna(subset=['Store Name'])
            
            # Combine historical and current day records
            df_final = pd.concat([df_old, df_new], ignore_index=True)
            print(f"\nAppended {len(df_new)} Guming results to existing {len(df_old)} records.")
        except Exception as e:
            print(f"Error reading existing file ({e}). Saving new data only.")
            df_final = df_new
    else:
        df_final = df_new
        print(f"\nCreated new Guming output file with {len(df_new)} results.")

    df_final.to_excel(output_file, index=False)
    print(f"Total Guming records in file: {len(df_final)}. Saved to {output_file}")

def calculate_daily_stats(excel_path):
    """Calculates statistics for today's Guming scrape."""
    if not os.path.exists(excel_path):
        logger.error(f"Guming output file {excel_path} not found!")
        return None

    try:
        df = pd.read_excel(excel_path)
        df = df.dropna(subset=['Store Name'])
        
        today_date = datetime.now().strftime("%Y-%m-%d")
        
        if 'Date' in df.columns:
            try:
                df_standardized_date = pd.to_datetime(df['Date']).dt.strftime('%Y-%m-%d')
                df_latest = df[df_standardized_date == today_date]
            except Exception as e:
                logger.warning(f"Standard date conversion failed ({e}), falling back to string comparison")
                df_latest = df[df['Date'].astype(str) == today_date]
            
            if df_latest.empty:
                logger.info(f"No Guming data found for today ({today_date}).")
                return None
                
            logger.info(f"Summarizing stats for Guming today's scrape: {today_date}")
        else:
            logger.error("No 'Date' column found in Guming Excel file.")
            return None
        
        total_stores = len(df_latest)
        total_cups = int(df_latest['Cup Count'].sum())
        
        stores_per_city = df_latest.groupby('City').size().sort_values(ascending=False)
        cups_per_city = df_latest.groupby('City')['Cup Count'].sum().sort_values(ascending=False)
        
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
        logger.error(f"Error calculating Guming stats: {e}")
        return None
