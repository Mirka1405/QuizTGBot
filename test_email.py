import smtplib
from email.mime.text import MIMEText

# Configuration
SMTP_SERVER = "smtp.yandex.com"
SMTP_PORT = 465
SENDER_EMAIL = "teamintelligenceindex@yandex.ru"
EMAIL_PASSWORD = "the same token as in .env"
RECEIVER_EMAIL = "mironsamokhvalov@gmail.com"

# Create message
msg = MIMEText("This is a test email from Python")
msg['Subject'] = "Test Email"
msg['From'] = SENDER_EMAIL
msg['To'] = RECEIVER_EMAIL

# Send email
try:
    with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as server:
        server.login(SENDER_EMAIL, EMAIL_PASSWORD)
        server.send_message(msg)
    print("Email sent successfully!")
except Exception as e:
    print(f"Failed to send email: {e}")