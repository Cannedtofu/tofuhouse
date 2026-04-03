import smtplib, ssl; context=ssl.create_default_context(); smtp = smtplib.SMTP_SSL('smtp.qq.com', 465, context=context); smtp.quit()
