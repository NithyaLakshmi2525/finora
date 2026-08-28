import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from config import Config

logger = logging.getLogger(__name__)

def send_email(to_email: str, subject: str, html_content: str, text_content: str = None) -> bool:
    """
    Sends an email using standard SMTP settings from Config.
    Returns True if delivery succeeded, False if email service is unconfigured or fails.
    """
    mail_server = Config.MAIL_SERVER
    mail_port = Config.MAIL_PORT
    mail_username = Config.MAIL_USERNAME
    mail_password = Config.MAIL_PASSWORD
    mail_use_tls = Config.MAIL_USE_TLS
    mail_use_ssl = Config.MAIL_USE_SSL
    mail_from = Config.MAIL_FROM or mail_username or "Finora <noreply@finora.app>"

    if not mail_server or not mail_username or not mail_password:
        logger.warning(
            "[Email Service] SMTP configuration missing (MAIL_SERVER, MAIL_USERNAME, or MAIL_PASSWORD not set). "
            "Skipping live email delivery."
        )
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = mail_from
    msg["To"] = to_email

    if text_content:
        msg.attach(MIMEText(text_content, "plain", "utf-8"))
    if html_content:
        msg.attach(MIMEText(html_content, "html", "utf-8"))

    try:
        if mail_use_ssl:
            with smtplib.SMTP_SSL(mail_server, mail_port, timeout=10) as server:
                server.login(mail_username, mail_password)
                server.sendmail(mail_from, [to_email], msg.as_string())
        else:
            with smtplib.SMTP(mail_server, mail_port, timeout=10) as server:
                if mail_use_tls:
                    server.starttls()
                server.login(mail_username, mail_password)
                server.sendmail(mail_from, [to_email], msg.as_string())
        logger.info("[Email Service] Successfully sent email to %s", to_email)
        return True
    except Exception as e:
        logger.error("[Email Service] Failed to send email to %s: %s", to_email, str(e))
        return False


def send_password_reset_email(to_email: str, reset_url: str) -> bool:
    """
    Sends a formatted password reset email to the specified user.
    """
    subject = "Reset Your Password - Finora"
    
    text_content = (
        f"Hi there,\n\n"
        f"We received a request to reset the password for your Finora account.\n\n"
        f"To reset your password, please click or copy the link below into your browser:\n"
        f"{reset_url}\n\n"
        f"This link is valid for 1 hour. If you did not request a password reset, you can safely ignore this email; "
        f"your account remains secure.\n\n"
        f"Best regards,\n"
        f"The Finora Team"
    )

    html_content = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  body {{ font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background-color: #10141a; color: #dfe2eb; margin: 0; padding: 24px; }}
  .container {{ max-width: 520px; margin: 0 auto; background: #1c2026; border: 1px solid #3c4a42; border-radius: 16px; padding: 32px; box-shadow: 0 10px 30px rgba(0,0,0,0.5); }}
  .brand {{ display: flex; align-items: center; gap: 10px; margin-bottom: 24px; }}
  .brand-logo {{ width: 36px; height: 36px; background: #4edea3; border-radius: 10px; display: inline-block; text-align: center; line-height: 36px; color: #003824; font-weight: 800; font-size: 20px; }}
  .brand-title {{ font-size: 20px; font-weight: 800; color: #dfe2eb; letter-spacing: -0.5px; margin: 0; }}
  h1 {{ font-size: 18px; font-weight: 700; color: #dfe2eb; margin-top: 0; margin-bottom: 16px; }}
  p {{ font-size: 14px; line-height: 1.6; color: #bbcabf; margin-bottom: 20px; }}
  .btn-wrap {{ margin: 28px 0; text-align: center; }}
  .btn {{ display: inline-block; background-color: #4edea3; color: #003824; font-weight: 700; font-size: 14px; padding: 12px 28px; text-decoration: none; border-radius: 12px; box-shadow: 0 4px 12px rgba(78,222,163,0.25); }}
  .btn:hover {{ background-color: #3bc78f; }}
  .link-fallback {{ font-size: 12px; word-break: break-all; color: #86948a; background: #181c22; padding: 12px; border-radius: 8px; border: 1px solid #3c4a42; }}
  .warning {{ font-size: 12px; color: #86948a; border-top: 1px solid #3c4a42; padding-top: 16px; margin-top: 24px; }}
</style>
</head>
<body>
<div class="container">
  <div class="brand">
    <div class="brand-title"><span style="color:#4edea3;">Fin</span>ora</div>
  </div>
  <h1>Password Reset Request</h1>
  <p>We received a request to reset your password for your Finora account. Click the button below to choose a new password:</p>
  <div class="btn-wrap">
    <a href="{reset_url}" class="btn" target="_blank">Reset Password</a>
  </div>
  <p>If the button doesn't work, copy and paste this link into your web browser:</p>
  <div class="link-fallback">{reset_url}</div>
  <div class="warning">
    <p style="margin:0;"><strong>Security Note:</strong> This link will expire in 1 hour. If you did not request a password reset, please ignore this email; your password will remain unchanged.</p>
  </div>
</div>
</body>
</html>"""

    return send_email(to_email, subject, html_content, text_content)
