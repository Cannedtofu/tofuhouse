import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
import os
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

def send_update_email(html_body: str, plain_body: str, attachment_path: str = None) -> None:
    """
    Sends the store update report via QQ Mail SMTP to the designated recipients.
    """
    sender_email = '396481139@qq.com'
    sender_password = 'mocjzkhznmudbghf'
    receiver_email = 'cuiyuan@maisoncapital.com, zengleshi@maisoncapital.com,lidongzhuang@outlook.com,396481139@qq.com'
    
    current_date = datetime.now().strftime("%Y-%m-%d")
    subject = f'泡泡玛特海外门店追踪更新 {current_date}'

    msg = MIMEMultipart('alternative')
    msg['From'] = sender_email
    msg['To'] = receiver_email
    msg['Subject'] = subject

    # Keep the plain text as a fallback
    part1 = MIMEText(plain_body, 'plain')
    msg.attach(part1)

    # Use HTML if provided
    if html_body:
        part2 = MIMEText(html_body, 'html')
        msg.attach(part2)

    if attachment_path and os.path.exists(attachment_path):
        with open(attachment_path, 'rb') as attachment:
            filename = os.path.basename(attachment_path)
            part = MIMEBase('application', 'octet-stream')
            part.set_payload(attachment.read())
            encoders.encode_base64(part)
            part.add_header('Content-Disposition', f'attachment; filename={filename}')
            msg.attach(part)

    try:
        with smtplib.SMTP('smtp.qq.com', 587) as smtp:
            smtp.starttls()
            smtp.login(sender_email, sender_password)
            smtp.send_message(msg)
        logger.info('Email report sent successfully!')
    except Exception as e:
        logger.error(f'Failed to send email: {e}')
