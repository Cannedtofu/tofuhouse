import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime
import logging
import config

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def _build_html(current_date, total_stores, total_cups, stats_text, wow_analysis, attachment_warning, chart_b64=None):
    warn_html = f'<p style="color:#c00;"><b>[WARNING]</b> 附件文件缺失！</p>' if attachment_warning else ''
    chart_html = (
        f'<img src="data:image/png;base64,{chart_b64}" '
        f'style="max-width:100%;height:auto;display:block;margin-bottom:20px;" alt="杯数趋势图">'
    ) if chart_b64 else ''

    return f"""<!DOCTYPE html>
<html>
<body style="font-family:Arial,sans-serif;font-size:14px;color:#333;max-width:820px;margin:0 auto;padding:16px;">

  {chart_html}

  <h2 style="margin-bottom:2px;">古茗 门店数据报告</h2>
  <p style="color:#888;margin-top:0;">{current_date}</p>

  <table style="border-collapse:collapse;font-size:14px;margin-bottom:20px;">
    <tr>
      <td style="padding:5px 20px 5px 0;color:#555;">今日爬取门店总数</td>
      <td style="padding:5px 0;font-weight:bold;">{total_stores}</td>
    </tr>
    <tr>
      <td style="padding:5px 20px 5px 0;color:#555;">今日总制作杯数</td>
      <td style="padding:5px 0;font-weight:bold;">{total_cups}</td>
    </tr>
  </table>

  <hr style="border:none;border-top:1px solid #e0e0e0;margin:20px 0;">

  {wow_analysis}

  <hr style="border:none;border-top:1px solid #e0e0e0;margin:20px 0;">

  <h3 style="margin-bottom:8px;">各城市详细统计</h3>
  <pre style="background:#f8f8f8;padding:12px 16px;border-radius:4px;font-size:13px;
              line-height:1.7;border:1px solid #e8e8e8;margin:0;">{stats_text}</pre>

  <p style="margin-top:20px;color:#666;font-size:13px;">详细汇总报告见附件。</p>
  {warn_html}

</body>
</html>"""

def send_report_email(total_stores: int, total_cups: int, stats_text: str, attachment_path: str, wow_analysis: str = "", chart_b64: str = None) -> None:
    sender_email = config.SENDER_EMAIL
    sender_password = config.SENDER_PASSWORD
    receiver_email = config.RECEIVER_EMAIL

    current_date = datetime.now().strftime("%Y-%m-%d")
    subject = f'古茗-门店数据报告 {current_date}'

    attachment_missing = not os.path.exists(attachment_path)
    html_body = _build_html(current_date, total_stores, total_cups, stats_text, wow_analysis, attachment_missing, chart_b64)

    msg = MIMEMultipart()
    msg['From'] = sender_email
    msg['To'] = receiver_email
    msg['Subject'] = subject
    msg.attach(MIMEText(html_body, 'html', 'utf-8'))

    if not attachment_missing:
        with open(attachment_path, 'rb') as attachment:
            part = MIMEBase('application', 'octet-stream')
            part.set_payload(attachment.read())
            encoders.encode_base64(part)
            part.add_header('Content-Disposition', f'attachment; filename={os.path.basename(attachment_path)}')
            msg.attach(part)
    else:
        logger.warning(f"Guming attachment not found at {attachment_path}")

    try:
        with smtplib.SMTP_SSL('smtp.qq.com', 465) as smtp:
            smtp.login(sender_email, sender_password)
            smtp.send_message(msg)
        print('Guming email report sent successfully!')
        logger.info('Guming email report sent successfully!')
    except Exception as e:
        print(f'Failed to send Guming email: {e}')
        logger.error(f'Failed to send Guming email: {e}')

if __name__ == "__main__":
    project_dir = os.path.dirname(os.path.abspath(__file__))
    output_file = os.path.join(project_dir, "guming_city_stores.xlsx")
    send_report_email(0, 0, "Test stats text", output_file)
