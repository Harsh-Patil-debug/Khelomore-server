"""
BookMyConsole Gaming Hub — Email Handler
──────────────────────────────────────────────────────────────────────────────
Sends OTP and welcome emails via Brevo's HTTP transactional email API.

Not raw SMTP: Render silently black-holes outbound SMTP connections instead of
refusing them, so smtplib.SMTP(...) hung forever regardless of any timeout
passed to it, taking down the gunicorn worker on every login (confirmed live —
WORKER TIMEOUT -> SIGKILL). An HTTPS API call behaves like any other outbound
request Render already handles fine, and fails fast/cleanly if Brevo is down.

Credentials sourced from .env: BREVO_API_KEY / EMAIL_HOST_USER (the sender
address — must be verified as a "sender" in the Brevo dashboard first).
"""

import os
import requests
from email.utils import parseaddr
from dotenv import load_dotenv
from django.conf import settings

load_dotenv()

BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"
BREVO_API_KEY = os.getenv("BREVO_API_KEY", "")

# EMAIL_SENDER holds "Display Name <address@example.com>" — Brevo's API wants name and
# email as separate fields, so split it. Falls back to EMAIL_HOST_USER (bare address, no
# display name) if EMAIL_SENDER isn't set, for compatibility with the old SMTP-era var.
_parsed_name, _parsed_email = parseaddr(os.getenv("EMAIL_SENDER", ""))
SENDER_EMAIL = _parsed_email or os.getenv("EMAIL_HOST_USER", "")
SENDER_NAME = _parsed_name or "BookMyConsole Gaming Hub"


def _send_email(recipient: str, subject: str, html_body: str) -> bool:
    """Core sender — Brevo's HTTP transactional email API."""
    if not BREVO_API_KEY or not SENDER_EMAIL:
        print(f"[EMAIL] Brevo credentials missing — skipping send to {recipient}")
        return False

    try:
        response = requests.post(
            BREVO_API_URL,
            headers={
                "accept": "application/json",
                "api-key": BREVO_API_KEY,
                "content-type": "application/json",
            },
            json={
                "sender": {"name": SENDER_NAME, "email": SENDER_EMAIL},
                "to": [{"email": recipient}],
                "subject": subject,
                "htmlContent": html_body,
            },
            timeout=15,
        )
        if response.status_code >= 400:
            print(f"[EMAIL] Brevo API Error {response.status_code}: {response.text}")
            return False
        print(f"[EMAIL] Sent '{subject}' to {recipient}")
        return True
    except requests.RequestException as e:
        print(f"[EMAIL] Brevo request failed: {e}")
        return False


def send_otp_email(recipient: str, otp: str, gamertag: str = "PLAYER", purpose: str = "verification") -> bool:
    """
    Sends a 6-digit OTP for login, signup, or password-reset verification.
    purpose: 'login' | 'signup' | 'password_reset' | 'verification'
    """
    # SECURITY: OTP codes must never hit server logs in production — anyone with log
    # access could otherwise authenticate as any user (including super admins) without
    # ever touching their inbox. Only print in local dev (DEBUG=True).
    if settings.DEBUG:
        print("\n" + "=" * 55)
        print(f"[BOOKMYCONSOLE] OTP INTERCEPTED — {purpose.upper()}")
        print(f"  Player  : {gamertag}")
        print(f"  Email   : {recipient}")
        print(f"  OTP Code: {otp}")
        print("=" * 55 + "\n")

    if purpose == "signup":
        action_label = "Sign Up"
    elif purpose == "password_reset":
        action_label = "Password Reset"
    else:
        action_label = "Login"
    subject = f"BookMyConsole — Your {action_label} Verification Code: {otp}"

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
                  ⚡ BOOKMYCONSOLE GAMING HUB ⚡
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
                  © 2026 BookMyConsole Gaming Hub. All rights reserved.
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
    subject = "Welcome to BookMyConsole Gaming Hub ⚡"

    html_body = f"""
    <html>
    <body style="margin:0;padding:0;background-color:#0B0C10;font-family:'Courier New',monospace;">
      <table width="100%" cellpadding="0" cellspacing="0" style="background:#0B0C10;padding:50px 20px;">
        <tr><td align="center">
          <table width="520" cellpadding="0" cellspacing="0"
                 style="background:#0E0E12;border:1px solid #E11D2E;border-radius:16px;overflow:hidden;">
            <tr>
              <td align="center" style="background:#0B0C10;padding:40px 0 28px;">
                <p style="margin:0;font-size:11px;letter-spacing:6px;color:#E11D2E;">⚡ BOOKMYCONSOLE GAMING HUB ⚡</p>
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
                  © 2026 BookMyConsole Gaming Hub · All rights reserved
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
    """Admin OTP email."""
    if settings.DEBUG:
        print(f"[ADMIN OTP] {name} / {recipient} → {otp}")
    return send_otp_email(recipient, otp, gamertag=name, purpose="admin login")


def send_sms_otp(phone_number: str, otp: str) -> bool:
    """SMS OTP stub — always succeeds (no Twilio configured for BookMyConsole)."""
    if settings.DEBUG:
        print(f"[SMS OTP STUB] {phone_number} → {otp}")
    return True
