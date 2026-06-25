"""
KheloMore Gaming Hub — Email Handler
──────────────────────────────────────────────────────────────────────────────
Sends OTP and welcome emails via Gmail SMTP.
Credentials sourced from .env: EMAIL_HOST_USER / EMAIL_HOST_PASSWORD
"""

import os
import smtplib
import random
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

load_dotenv()

SMTP_HOST = os.getenv("EMAIL_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("EMAIL_PORT", 587))
SMTP_USER = os.getenv("EMAIL_HOST_USER", "")
SMTP_PASS = os.getenv("EMAIL_HOST_PASSWORD", "")
SENDER_NAME = "KheloMore Gaming Hub"


def _send_email(recipient: str, subject: str, html_body: str) -> bool:
    """Core SMTP sender using Gmail."""
    if not SMTP_USER or not SMTP_PASS:
        print(f"[EMAIL] SMTP credentials missing — skipping send to {recipient}")
        return False

    msg = MIMEMultipart("alternative")
    msg["From"] = f"{SENDER_NAME} <{SMTP_USER}>"
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, recipient, msg.as_string())
        print(f"[EMAIL] Sent '{subject}' to {recipient}")
        return True
    except Exception as e:
        print(f"[EMAIL] SMTP Error: {e}")
        return False


def send_otp_email(recipient: str, otp: str, gamertag: str = "PLAYER", purpose: str = "verification") -> bool:
    """
    Sends a 6-digit OTP for login or signup verification.
    purpose: 'login' | 'signup' | 'verification'
    """
    # Always print OTP to console for local development
    print("\n" + "=" * 55)
    print(f"[KHELOMORE] OTP INTERCEPTED — {purpose.upper()}")
    print(f"  Player  : {gamertag}")
    print(f"  Email   : {recipient}")
    print(f"  OTP Code: {otp}")
    print("=" * 55 + "\n")

    action_label = "Sign Up" if purpose == "signup" else "Login"
    subject = f"KheloMore — Your {action_label} Verification Code: {otp}"

    html_body = f"""
    <html>
    <body style="margin:0;padding:0;background-color:#0B0C10;font-family:'Courier New',monospace;">
      <table width="100%" cellpadding="0" cellspacing="0" style="background:#0B0C10;padding:50px 20px;">
        <tr><td align="center">
          <table width="520" cellpadding="0" cellspacing="0"
                 style="background:#0E0E12;border:1px solid #E11D2E;border-radius:16px;overflow:hidden;">

            <!-- Header -->
            <tr>
              <td align="center" style="background:#0B0C10;padding:36px 0 24px;">
                <p style="margin:0;font-size:11px;letter-spacing:6px;color:#E11D2E;text-transform:uppercase;">
                  ⚡ KHELOMORE GAMING HUB ⚡
                </p>
                <h1 style="margin:10px 0 0;font-size:28px;letter-spacing:4px;color:#ffffff;text-transform:uppercase;">
                  ACCESS CODE
                </h1>
              </td>
            </tr>

            <!-- Body -->
            <tr>
              <td style="padding:32px 40px;text-align:center;">
                <p style="color:#9CA3AF;font-size:13px;margin-bottom:28px;letter-spacing:1px;">
                  Operator <strong style="color:#ffffff;">{gamertag}</strong>,
                  use the code below to verify your identity.
                </p>

                <!-- OTP Box -->
                <div style="background:#0B0C10;border:2px solid #E11D2E;border-radius:12px;
                            padding:28px 0;margin:0 auto 28px;max-width:320px;">
                  <p style="margin:0;font-size:42px;letter-spacing:14px;color:#E11D2E;
                             font-weight:bold;text-align:center;">{otp}</p>
                </div>

                <p style="color:#6B7280;font-size:11px;margin:0;letter-spacing:1px;">
                  This code expires in <strong style="color:#ffffff;">10 minutes</strong>.
                  Never share it with anyone.
                </p>
              </td>
            </tr>

            <!-- Footer -->
            <tr>
              <td align="center" style="border-top:1px solid #1f1f28;padding:20px 40px;">
                <p style="margin:0;font-size:9px;letter-spacing:3px;color:#374151;text-transform:uppercase;">
                  SECURE AUTH · AES-256 ENCRYPTED TRANSIT · SHA-256
                </p>
                <p style="margin:6px 0 0;font-size:9px;color:#374151;">
                  © 2026 KheloMore Gaming Hub. All rights reserved.
                </p>
              </td>
            </tr>

          </table>
        </td></tr>
      </table>
    </body>
    </html>
    """

    return _send_email(recipient, subject, html_body)


def send_welcome_email(recipient: str, gamertag: str = "PLAYER") -> bool:
    """Sends a welcome email after a player's account is verified and activated."""
    subject = "Welcome to KheloMore Gaming Hub ⚡"

    html_body = f"""
    <html>
    <body style="margin:0;padding:0;background-color:#0B0C10;font-family:'Courier New',monospace;">
      <table width="100%" cellpadding="0" cellspacing="0" style="background:#0B0C10;padding:50px 20px;">
        <tr><td align="center">
          <table width="520" cellpadding="0" cellspacing="0"
                 style="background:#0E0E12;border:1px solid #E11D2E;border-radius:16px;overflow:hidden;">
            <tr>
              <td align="center" style="background:#0B0C10;padding:40px 0 28px;">
                <p style="margin:0;font-size:11px;letter-spacing:6px;color:#E11D2E;">⚡ KHELOMORE GAMING HUB ⚡</p>
                <h1 style="margin:10px 0 0;font-size:26px;letter-spacing:4px;color:#ffffff;text-transform:uppercase;">
                  WELCOME, OPERATOR
                </h1>
              </td>
            </tr>
            <tr>
              <td style="padding:32px 40px;text-align:center;">
                <h2 style="color:#E11D2E;font-size:20px;letter-spacing:3px;margin-bottom:20px;">
                  {gamertag}
                </h2>
                <p style="color:#9CA3AF;font-size:13px;line-height:1.8;margin-bottom:28px;">
                  Your neural link has been registered in the core database.<br>
                  The arena is open. Find your nearest gaming station and dominate.
                </p>
                <p style="color:#6B7280;font-size:11px;letter-spacing:2px;text-transform:uppercase;">
                  STARTING XP: <strong style="color:#ffffff;">0</strong> ·
                  RANK: <strong style="color:#E11D2E;">RECRUIT</strong>
                </p>
              </td>
            </tr>
            <tr>
              <td align="center" style="border-top:1px solid #1f1f28;padding:20px 40px;">
                <p style="margin:0;font-size:9px;letter-spacing:3px;color:#374151;text-transform:uppercase;">
                  © 2026 KheloMore Gaming Hub · All rights reserved
                </p>
              </td>
            </tr>
          </table>
        </td></tr>
      </table>
    </body>
    </html>
    """

    return _send_email(recipient, subject, html_body)


def send_admin_otp_email(recipient: str, otp: str, name: str = "Admin") -> bool:
    """Admin OTP email (stub — logs to console for now)."""
    print(f"[ADMIN OTP] {name} / {recipient} → {otp}")
    return send_otp_email(recipient, otp, gamertag=name, purpose="admin login")


def send_sms_otp(phone_number: str, otp: str) -> bool:
    """SMS OTP stub — always succeeds (no Twilio configured for KheloMore)."""
    print(f"[SMS OTP STUB] {phone_number} → {otp}")
    return True
