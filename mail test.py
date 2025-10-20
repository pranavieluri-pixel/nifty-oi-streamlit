import smtplib, ssl
from email.mime.text import MIMEText

sender = "yourname@gmail.com"
password = "16 digit app password"

msg = MIMEText("Test email from Python")
msg["Subject"] = "Test"
msg["From"] = sender
msg["To"] = sender

context = ssl.create_default_context()
with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
    server.login(sender, password)
    server.send_message(msg)

print("Email sent")
