import os
import re
import logging
import pandas as pd
from datetime import datetime
from bs4 import BeautifulSoup

# Re-use our robust scraping functions from the main script
from polaris_ai import setup_driver, safe_get_page, extract_brands

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

def retry_na_records():
    file_path = r"D:\代码项目\CF moto NA dealer\polaris_results.xlsx"
    
    if not os.path.exists(file_path):
        logger.error(f"File not found: {file_path}")
        return
        
    logger.info(f"Loading data from {file_path}")
    df = pd.read_excel(file_path)
    
    # Identify rows where address is "N/A" or empty/NaN
    na_mask = df['地址'].isna() | (df['地址'] == "N/A") | (df['地址'] == "")
    na_indices = df[na_mask].index.tolist()
    
    if not na_indices:
        logger.info("No N/A addresses found! All data is fully populated.")
        return
        
    logger.info(f"Found {len(na_indices)} records with missing addresses. Initializing driver...")
    
    driver = setup_driver()
    
    try:
        for count, idx in enumerate(na_indices, 1):
            row = df.loc[idx]
            url = row['网址']
            logger.info(f"[{count}/{len(na_indices)}] Retrying URL: {url}")
            
            # Max 3 retries, with our newly updated robust backoff logic
            page_source = safe_get_page(driver, url, max_retries=3)
            
            if not page_source:
                logger.error(f"Failed to fetch {url} completely.")
                continue
                
            soup = BeautifulSoup(page_source, 'html.parser')
            
            address_tag = soup.find('address')
            brands_div = soup.find('div', class_='multiple-brands-support')
            name_tag = soup.find('h1', itemprop='name') or soup.find('h1')
            
            detail_address = address_tag.get_text(strip=True) if address_tag else ""
            brands_text = brands_div.get_text(separator=', ', strip=True) if brands_div else ""
            
            # Safely get current name, state, zip handling potential NaNs from pandas
            final_name = name_tag.get_text(strip=True) if name_tag else str(row.get('经销商名称', ''))
            final_state = str(row.get('州', ''))
            final_zip = str(row.get('邮编', ''))
            
            if final_state == 'nan': final_state = ''
            if final_zip == 'nan': final_zip = ''
            
            if not detail_address:
                logger.warning(f"Still unable to extract address for {url}. Keeping as N/A.")
                detail_address = "N/A"
            else:
                logger.info(f"Successfully recovered address: {detail_address}")
                
                # Regex out State Code and Zip Code
                match = re.search(r'\b([A-Z]{2})\s*(\d{5}(?:-\d{4})?)\s*$', detail_address)
                if match:
                    final_state = match.group(1)
                    final_zip = match.group(2)
            
            brands_mapped = extract_brands(brands_text)
            
            # Update the dataframe directly
            df.at[idx, '地址'] = detail_address
            df.at[idx, '经销商名称'] = final_name
            df.at[idx, '州'] = final_state
            df.at[idx, '邮编'] = final_zip
            
            for brand, val in brands_mapped.items():
                df.at[idx, brand] = val
                
            df.at[idx, '数据时间'] = datetime.now().strftime('%m/%d/%Y')
            
            # Save checkpoint every 10 records
            if count % 10 == 0:
                df.to_excel(file_path, index=False)
                logger.info(f"Checkpoint saved ({count}/{len(na_indices)}).")
                
    except KeyboardInterrupt:
        logger.warning("Process manually interrupted. Saving partial recovery data...")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
    finally:
        df.to_excel(file_path, index=False)
        logger.info(f"Finished retrying N/A records. Data saved to {file_path}")
        driver.quit()

if __name__ == "__main__":
    retry_na_records()
