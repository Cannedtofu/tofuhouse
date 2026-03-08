import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
import os
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

def send_report_email(success_count: int, failed_count: int, attachment_path: str) -> None:
    """
    Sends the generated Excel report via QQ Mail SMTP to the designated recipients.
    """
    sender_email = '396481139@qq.com'
    sender_password = 'mocjzkhznmudbghf'
    receiver_email = 'cuiyuan@maisoncapital.com,linhuaqiang@maisoncapital.com,zengleshi@maisoncapital.com,lidongzhuang@outlook.com,396481139@qq.com'
    
    current_date = datetime.now().strftime("%Y-%m-%d")
    subject = f'泡泡玛特二手数据-分sku {current_date}'

    total_count = success_count + failed_count
    
    email_body = (
        f"Total datapoints collected: {total_count}\n"
        f"Total success: {success_count}\n"
        f"Total failed: {failed_count}\n\n"
        f"The aggregated lean tracking report is attached.\n"
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
        with smtplib.SMTP('smtp.qq.com', 587) as smtp:
            smtp.starttls()
            smtp.login(sender_email, sender_password)
            smtp.send_message(msg)
        logger.info('Email report sent successfully!')
    except Exception as e:
        logger.error(f'Failed to send email: {e}')
