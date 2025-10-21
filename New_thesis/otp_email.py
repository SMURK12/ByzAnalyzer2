#!/usr/bin/env python3
# otp_email.py - Enhanced OTP email sender with better formatting

import smtplib
from email.message import EmailMessage
import os
from dotenv import load_dotenv
import logging

load_dotenv()
logger = logging.getLogger(__name__)

try:
    OTP_TTL_SECONDS = int(os.getenv('OTP_TTL_SECONDS', '300'))
except ValueError:
    OTP_TTL_SECONDS = 300

def send_otp_email(email: str, otp: str, name: str = "") -> bool:
    """
    Send OTP verification email with nice HTML formatting.
    
    Args:
        email: Recipient email address
        otp: 6-digit OTP code
        name: Optional user name for personalization
    
    Returns:
        True if email sent successfully, False otherwise
    """
    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", 587))
    user = os.getenv("SMTP_USER2")
    passwd = os.getenv("SMTP_PASS2")
    email_from = os.getenv("EMAIL_FROM", user)

    if not host or not user or not passwd:
        logger.error("Missing SMTP configuration. Check SMTP_HOST, SMTP_USER2, SMTP_PASS2")
        return False

    try:
        msg = EmailMessage()
        
        # Plain text version
        greeting = f"Hi {name}," if name else "Hi there,"
        plain_text = f"""{greeting}

Your verification code for Business Location Analyzer is:

{otp}

This code will expire in {OTP_TTL_SECONDS//60} minutes.

If you didn't request this code, please ignore this email.

Best regards,
Business Location Analyzer Team
"""
        
        # HTML version with styling
        html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin: 0; padding: 0; font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif; background-color: #f3f4f6;">
    <table width="100%" cellpadding="0" cellspacing="0" style="background-color: #f3f4f6; padding: 40px 20px;">
        <tr>
            <td align="center">
                <table width="600" cellpadding="0" cellspacing="0" style="background-color: #ffffff; border-radius: 16px; overflow: hidden; box-shadow: 0 4px 6px rgba(0,0,0,0.1);">
                    <!-- Header -->
                    <tr>
                        <td style="padding: 40px 40px 30px; text-align: center; background: linear-gradient(135deg, #1e40af 0%, #3b82f6 100%);">
                            <h1 style="margin: 0; color: #ffffff; font-size: 24px; font-weight: 700;">Email Verification</h1>
                        </td>
                    </tr>
                    
                    <!-- Body -->
                    <tr>
                        <td style="padding: 40px;">
                            <p style="margin: 0 0 20px; color: #1f2937; font-size: 16px; line-height: 1.6;">
                                {greeting}
                            </p>
                            <p style="margin: 0 0 24px; color: #4b5563; font-size: 15px; line-height: 1.6;">
                                Your verification code for <strong style="color: #1f2937;">Business Location Analyzer</strong> is:
                            </p>
                            
                            <!-- OTP Code Box -->
                            <div style="background-color: #f9fafb; border: 2px solid #e5e7eb; border-radius: 12px; padding: 24px; text-align: center; margin-bottom: 24px;">
                                <div style="font-size: 36px; font-weight: 700; letter-spacing: 8px; color: #1e40af; font-family: 'Courier New', monospace;">
                                    {otp}
                                </div>
                            </div>
                            
                            <div style="background-color: #fef3c7; border-left: 4px solid #f59e0b; padding: 16px; border-radius: 8px; margin-bottom: 16px;">
                                <p style="margin: 0; color: #92400e; font-size: 14px;">
                                    <strong>Important:</strong> This code will expire in <strong>{OTP_TTL_SECONDS//60} minutes</strong>
                                </p>
                            </div>
                            
                            <p style="margin: 0; color: #6b7280; font-size: 13px; line-height: 1.6;">
                                If you didn't request this code, please ignore this email or contact our support team if you have concerns.
                            </p>
                        </td>
                    </tr>
                    
                    <!-- Footer -->
                    <tr>
                        <td style="padding: 30px 40px; text-align: center; background-color: #f9fafb; border-top: 1px solid #e5e7eb;">
                            <p style="margin: 0; color: #6b7280; font-size: 12px;">
                                © 2025 Business Location Analyzer. All rights reserved.
                            </p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>
"""
        
        msg.set_content(plain_text)
        msg.add_alternative(html_content, subtype='html')
        
        msg['Subject'] = f"Your verification code: {otp}"
        msg['From'] = email_from
        msg['To'] = email

        # Send email
        with smtplib.SMTP(host, port, timeout=10) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(user, passwd)
            server.send_message(msg)
        
        logger.info(f"OTP email sent successfully to {email}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to send OTP email to {email}: {repr(e)}")
        return False

