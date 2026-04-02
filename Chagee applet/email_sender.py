import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def send_report_email(total_stores: int, total_cups: int, stats_text: str, attachment_path: str, wow_analysis: str = "") -> None:
    """
    Sends the generated Excel report via QQ Mail SMTP.
    """
    # Same credentials and recipients as the original automate_and_email.py
    sender_email = '396481139@qq.com'
    sender_password = 'mocjzkhznmudbghf'
    receiver_email = 'cuiyuan@maisoncapital.com, 396481139@qq.com, linhuaqiang@maisoncapital.com'
    
    current_date = datetime.now().strftime("%Y-%m-%d")
    subject = f'霸王茶姬-门店数据报告 {current_date}'

    email_body = ""
    
    if wow_analysis:
        email_body += f"{wow_analysis}\n\n"
        email_body += "--------------------------------------------------\n\n"

    email_body += (
        f"今日已爬取门店总数: {total_stores}\n"
        f"今日总制作杯数 (汇总): {total_cups}\n\n"
        f"{stats_text}\n"
        f"详细汇总报告见附件。\n"
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
        # Using QQ SMTP settings
        with smtplib.SMTP('smtp.qq.com', 587) as smtp:
            smtp.starttls()
            smtp.login(sender_email, sender_password)
            smtp.send_message(msg)
        logger.info('Email report sent successfully!')
    except Exception as e:
        logger.error(f'Failed to send email: {e}')

if __name__ == "__main__":
    # Test call (requires attachment to exist)
    project_dir = os.path.dirname(os.path.abspath(__file__))
    output_file = os.path.join(project_dir, "multi_city_stores.xlsx")
    send_report_email(0, 0, "Test stats text", output_file)
