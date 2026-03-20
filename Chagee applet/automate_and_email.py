import os
import sys
import pandas as pd
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime
import time
import logging

# Configure logging to match the style of the other project
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Add relevant paths
sys.path.append(os.path.join(os.getcwd(), "ui_modules"))

# Import the main workflow
try:
    from full_test import full_back_to_back_test
except ImportError:
    # If full_test is not correctly in the path, try to add current dir
    sys.path.append(os.getcwd())
    from full_test import full_back_to_back_test

def send_report_email(total_stores: int, total_cups: int, stats_text: str, attachment_path: str) -> None:
    """
    Sends the generated Excel report via QQ Mail SMTP, following the setup from the 泡泡千岛2.0 project.
    """
    # Same credentials and recipients as 泡泡千岛2.0
    sender_email = '396481139@qq.com'
    sender_password = 'mocjzkhznmudbghf'
    receiver_email = 'cuiyuan@maisoncapital.com, 396481139@qq.com, linhuaqiang@maisoncapital.com'
    
    current_date = datetime.now().strftime("%Y-%m-%d")
    subject = f'霸王茶姬-门店数据 {current_date}'

    email_body = (
        f"Total stores scrapped: {total_stores}\n"
        f"Total cup count (combined): {total_cups}\n\n"
        f"{stats_text}\n"
        f"The aggregated report is attached.\n"
    )

    msg = MIMEMultipart()
    msg['From'] = sender_email
    msg['To'] = receiver_email
    msg['Subject'] = subject

    msg.attach(MIMEText(email_body, 'plain'))

    if os.path.exists(attachment_path):
        with open(attachment_path, 'rb') as attachment:
            filename = os.path.basename(attachment_path)
            part = MIMEBase('application', 'octet-stream')
            part.set_payload(attachment.read())
            encoders.encode_base64(part)
            part.add_header('Content-Disposition', f'attachment; filename={filename}')
            msg.attach(part)
    else:
        logger.warning(f"Attachment not found at {attachment_path}")
        email_body += "\n[WARNING] Attachment file was missing!"

    try:
        # Using QQ SMTP settings from 泡泡千岛2.0 project
        with smtplib.SMTP('smtp.qq.com', 587) as smtp:
            smtp.starttls()
            smtp.login(sender_email, sender_password)
            smtp.send_message(msg)
        logger.info('Email report sent successfully!')
    except Exception as e:
        logger.error(f'Failed to send email: {e}')

def run_automation_and_report():
    logger.info("=== Automation Started ===")
    
    # 1. Run the full test (scraping + export)
    try:
        # Increase safety: ensures we are in the correct directory
        project_root = os.path.dirname(os.path.abspath(__file__))
        os.chdir(project_root)
        full_back_to_back_test()
    except Exception as e:
        logger.error(f"Error during scraping: {e}")
    finally:
        # User Rule: Ensure applet and search windows are closed at the end
        try:
            from cleanup import close_chagee_windows
            close_chagee_windows()
        except Exception as e:
            logger.error(f"Failed to run final cleanup: {e}")

    
    # 2. Locate the output file
    # Based on applet_interact.py, it's 'multi_city_stores.xlsx' in the project root
    output_file = os.path.join(os.getcwd(), "multi_city_stores.xlsx")
    
    if not os.path.exists(output_file):
        logger.error(f"Output file {output_file} not found!")
        return

    # 3. Calculate statistics
    try:
        # Load the results
        df = pd.read_excel(output_file)
        
        # Remove any empty or malformed rows
        df = df.dropna(subset=['Store Name'])

        # Filter for only the current day's scraping
        today_date = datetime.now().strftime("%Y-%m-%d")
        
        # Ensure we only proceed if data for today was actually added
        if 'Date' in df.columns:
            try:
                # Handle strings, timestamps, and mixed types
                df_standardized_date = pd.to_datetime(df['Date']).dt.strftime('%Y-%m-%d')
                df_latest = df[df_standardized_date == today_date]
            except Exception as e:
                logger.warning(f"Standard date conversion failed ({e}), falling back to string comparison")
                df_latest = df[df['Date'].astype(str) == today_date]
            
            if df_latest.empty:
                logger.info(f"No new data found for today ({today_date}). Skipping email report.")
                return # Stop here: do not send email
                
            logger.info(f"Summarizing stats for today's scrape: {today_date}")
            latest_date = today_date
        else:
            logger.error("No 'Date' column was found in the Excel file. Cannot determine what's new. Skipping email.")
            return
        
        total_stores = len(df_latest)
        total_cups = df_latest['Cup Count'].sum()
        
        stores_per_city = df_latest.groupby('City').size().sort_values(ascending=False)
        cups_per_city = df_latest.groupby('City')['Cup Count'].sum().sort_values(ascending=False)
        
        # Format the city-specific stats for the email body text
        city_stats_text = f"Summary for Date: {latest_date if 'Date' in df.columns else 'All'}\n"
        city_stats_text += "--- Stores Scrapped for Each City ---\n"
        for city, count in stores_per_city.items():
            city_stats_text += f"  {city}: {count}\n"
            
        city_stats_text += "\n--- Total Cup Count for Each City ---\n"
        for city, count in cups_per_city.items():
            city_stats_text += f"  {city}: {count}\n"
        
        # 4. Send Email using the shared setup
        send_report_email(total_stores, total_cups, city_stats_text, output_file)
        
    except Exception as e:
        logger.error(f"Error calculating stats or sending email: {e}")
        import traceback
        logger.error(traceback.format_exc())

if __name__ == "__main__":
    run_automation_and_report()
