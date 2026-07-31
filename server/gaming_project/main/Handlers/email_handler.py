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
import html
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

# Contact-support inbox — falls back to the sender address if not explicitly set.
SUPPORT_EMAIL = os.getenv("SUPPORT_EMAIL", "") or SENDER_EMAIL
SUPPORT_PHONE = os.getenv("SUPPORT_PHONE", "")


def _send_email(recipient: str, subject: str, html_body: str, reply_to: str = None) -> bool:
    """Core sender — Brevo's HTTP transactional email API. `reply_to` lets a recipient
    hit "Reply" and land in the actual sender's inbox instead of the platform's own
    (used by support-query emails, where support should be able to reply straight to
    the gamer/cafe owner who wrote in)."""
    if not BREVO_API_KEY or not SENDER_EMAIL:
        print(f"[EMAIL] Brevo credentials missing — skipping send to {recipient}")
        return False

    try:
        payload = {
            "sender": {"name": SENDER_NAME, "email": SENDER_EMAIL},
            "to": [{"email": recipient}],
            "subject": subject,
            "htmlContent": html_body,
        }
        if reply_to:
            payload["replyTo"] = {"email": reply_to}
        response = requests.post(
            BREVO_API_URL,
            headers={
                "accept": "application/json",
                "api-key": BREVO_API_KEY,
                "content-type": "application/json",
            },
            json=payload,
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


def send_booking_confirmation_email(recipient: str, user_name: str, cafe_name: str, rig: str, zone: str,
                                     date: str, slot: str, price: int, code: str) -> bool:
    """Sent to the gamer right after a booking is created — confirms which station/rig at
    which cafe they've booked, so there's no ambiguity about where to show up."""
    # SECURITY: user_name/rig/cafe_name/zone/date/slot are all client-controlled (rig and
    # customerName in particular have no server-side allowlist) — HTML-escape everything
    # before interpolating into the email body so a crafted booking can't inject markup
    # (fake links, spoofed "official" content, etc) into a transactional email.
    user_name = html.escape(user_name or "")
    cafe_name = html.escape(cafe_name or "")
    rig = html.escape(rig) if rig else rig
    zone = html.escape(zone or "")
    date = html.escape(date or "")
    slot = html.escape(slot or "")
    code = html.escape(code or "")
    subject = f"BookMyConsole — Booking Confirmed at {cafe_name}"

    html_body = f"""
    <html>
    <body style="margin:0;padding:0;background-color:#0B0C10;font-family:'Courier New',monospace;">
      <table width="100%" cellpadding="0" cellspacing="0" style="background:#0B0C10;padding:50px 20px;">
        <tr><td align="center">
          <table width="520" cellpadding="0" cellspacing="0"
                 style="background:#0E0E12;border:1px solid #E11D2E;border-radius:16px;overflow:hidden;">
            <tr>
              <td align="center" style="background:#0B0C10;padding:36px 0 24px;">
                <p style="margin:0;font-size:11px;letter-spacing:6px;color:#E11D2E;text-transform:uppercase;">
                  ⚡ BOOKMYCONSOLE GAMING HUB ⚡
                </p>
                <h1 style="margin:10px 0 0;font-size:26px;letter-spacing:3px;color:#ffffff;text-transform:uppercase;">
                  BOOKING CONFIRMED
                </h1>
              </td>
            </tr>
            <tr>
              <td style="padding:32px 40px;">
                <p style="color:#9CA3AF;font-size:13px;margin:0 0 24px;letter-spacing:1px;">
                  Operator <strong style="color:#ffffff;">{user_name}</strong>, your station is reserved.
                </p>
                <table width="100%" cellpadding="0" cellspacing="0" style="background:#0B0C10;border:1px solid #1f1f28;border-radius:12px;">
                  <tr><td style="padding:16px 20px;border-bottom:1px solid #1f1f28;">
                    <p style="margin:0;font-size:9px;letter-spacing:2px;color:#6B7280;text-transform:uppercase;">Gaming Cafe</p>
                    <p style="margin:4px 0 0;font-size:15px;color:#ffffff;font-weight:bold;">{cafe_name}</p>
                  </td></tr>
                  <tr><td style="padding:16px 20px;border-bottom:1px solid #1f1f28;">
                    <p style="margin:0;font-size:9px;letter-spacing:2px;color:#6B7280;text-transform:uppercase;">Station / Rig</p>
                    <p style="margin:4px 0 0;font-size:15px;color:#E11D2E;font-weight:bold;">{rig or "Auto-assigned on arrival"}</p>
                  </td></tr>
                  <tr><td style="padding:16px 20px;border-bottom:1px solid #1f1f28;">
                    <p style="margin:0;font-size:9px;letter-spacing:2px;color:#6B7280;text-transform:uppercase;">Zone</p>
                    <p style="margin:4px 0 0;font-size:14px;color:#ffffff;">{zone}</p>
                  </td></tr>
                  <tr><td style="padding:16px 20px;border-bottom:1px solid #1f1f28;">
                    <p style="margin:0;font-size:9px;letter-spacing:2px;color:#6B7280;text-transform:uppercase;">Date &amp; Slot</p>
                    <p style="margin:4px 0 0;font-size:14px;color:#ffffff;">{date} · {slot}</p>
                  </td></tr>
                  <tr><td style="padding:16px 20px;">
                    <p style="margin:0;font-size:9px;letter-spacing:2px;color:#6B7280;text-transform:uppercase;">Amount &amp; Check-in Code</p>
                    <p style="margin:4px 0 0;font-size:14px;color:#ffffff;">₹{price} · Code <strong style="color:#E11D2E;letter-spacing:3px;">{code}</strong></p>
                  </td></tr>
                </table>
              </td>
            </tr>
            <tr>
              <td align="center" style="border-top:1px solid #1f1f28;padding:20px 40px;">
                <p style="margin:0;font-size:9px;letter-spacing:3px;color:#374151;text-transform:uppercase;">
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


def send_booking_admin_notification_email(recipient: str, user_name: str, user_phone: str, cafe_name: str,
                                           rig: str, zone: str, date: str, slot: str, price: int, code: str) -> bool:
    """Sent to the cafe's owner_email right after a booking is created — tells them who's
    coming in, and specifically which of their rigs/stations was just booked."""
    # SECURITY: this email goes to a DIFFERENT party than whoever supplied these values
    # (the booking gamer) — user_name/user_phone/rig in particular are fully
    # client-controlled with no server-side allowlist, so escape everything before
    # interpolating into HTML. Otherwise a crafted booking could inject markup (fake
    # links, spoofed content) into an email a cafe owner receives under the platform's
    # own verified sender identity.
    user_name = html.escape(user_name or "")
    user_phone = html.escape(user_phone or "")
    cafe_name = html.escape(cafe_name or "")
    rig = html.escape(rig) if rig else rig
    zone = html.escape(zone or "")
    date = html.escape(date or "")
    slot = html.escape(slot or "")
    code = html.escape(code or "")
    subject = f"New Booking at {cafe_name} — {rig or zone}"

    html_body = f"""
    <html>
    <body style="margin:0;padding:0;background-color:#0B0C10;font-family:'Courier New',monospace;">
      <table width="100%" cellpadding="0" cellspacing="0" style="background:#0B0C10;padding:50px 20px;">
        <tr><td align="center">
          <table width="520" cellpadding="0" cellspacing="0"
                 style="background:#0E0E12;border:1px solid #E11D2E;border-radius:16px;overflow:hidden;">
            <tr>
              <td align="center" style="background:#0B0C10;padding:36px 0 24px;">
                <p style="margin:0;font-size:11px;letter-spacing:6px;color:#E11D2E;text-transform:uppercase;">
                  ⚡ BOOKMYCONSOLE GAMING HUB ⚡
                </p>
                <h1 style="margin:10px 0 0;font-size:26px;letter-spacing:3px;color:#ffffff;text-transform:uppercase;">
                  NEW BOOKING
                </h1>
              </td>
            </tr>
            <tr>
              <td style="padding:32px 40px;">
                <p style="color:#9CA3AF;font-size:13px;margin:0 0 24px;letter-spacing:1px;">
                  A gamer just booked a station at <strong style="color:#ffffff;">{cafe_name}</strong>.
                </p>
                <table width="100%" cellpadding="0" cellspacing="0" style="background:#0B0C10;border:1px solid #1f1f28;border-radius:12px;">
                  <tr><td style="padding:16px 20px;border-bottom:1px solid #1f1f28;">
                    <p style="margin:0;font-size:9px;letter-spacing:2px;color:#6B7280;text-transform:uppercase;">Station / Rig Booked</p>
                    <p style="margin:4px 0 0;font-size:15px;color:#E11D2E;font-weight:bold;">{rig or "Auto-assigned"}</p>
                  </td></tr>
                  <tr><td style="padding:16px 20px;border-bottom:1px solid #1f1f28;">
                    <p style="margin:0;font-size:9px;letter-spacing:2px;color:#6B7280;text-transform:uppercase;">Gamer</p>
                    <p style="margin:4px 0 0;font-size:14px;color:#ffffff;">{user_name}{f" · {user_phone}" if user_phone else ""}</p>
                  </td></tr>
                  <tr><td style="padding:16px 20px;border-bottom:1px solid #1f1f28;">
                    <p style="margin:0;font-size:9px;letter-spacing:2px;color:#6B7280;text-transform:uppercase;">Zone</p>
                    <p style="margin:4px 0 0;font-size:14px;color:#ffffff;">{zone}</p>
                  </td></tr>
                  <tr><td style="padding:16px 20px;border-bottom:1px solid #1f1f28;">
                    <p style="margin:0;font-size:9px;letter-spacing:2px;color:#6B7280;text-transform:uppercase;">Date &amp; Slot</p>
                    <p style="margin:4px 0 0;font-size:14px;color:#ffffff;">{date} · {slot}</p>
                  </td></tr>
                  <tr><td style="padding:16px 20px;">
                    <p style="margin:0;font-size:9px;letter-spacing:2px;color:#6B7280;text-transform:uppercase;">Amount &amp; Check-in Code</p>
                    <p style="margin:4px 0 0;font-size:14px;color:#ffffff;">₹{price} · Code <strong style="color:#E11D2E;letter-spacing:3px;">{code}</strong></p>
                  </td></tr>
                </table>
              </td>
            </tr>
            <tr>
              <td align="center" style="border-top:1px solid #1f1f28;padding:20px 40px;">
                <p style="margin:0;font-size:9px;letter-spacing:3px;color:#374151;text-transform:uppercase;">
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


def send_support_query_email(from_name: str, from_email: str, message: str, source: str = "App") -> bool:
    """Forwards a user-submitted "Contact Support" query to SUPPORT_EMAIL, with
    reply-to set to the submitter so support can just hit Reply. `source` distinguishes
    the gamer app from the cafe-owner dashboard in the subject line."""
    # SECURITY: from_name/message are fully user-controlled free text - escape before
    # interpolating into HTML, same as the booking-confirmation emails.
    safe_name = html.escape(from_name or "A user")
    safe_email = html.escape(from_email or "")
    safe_message = html.escape(message or "").replace("\n", "<br>")
    subject = f"BookMyConsole Support Query ({source}) — {from_name or from_email or 'Unknown'}"

    html_body = f"""
    <html>
    <body style="margin:0;padding:0;background-color:#0B0C10;font-family:'Courier New',monospace;">
      <table width="100%" cellpadding="0" cellspacing="0" style="background:#0B0C10;padding:50px 20px;">
        <tr><td align="center">
          <table width="520" cellpadding="0" cellspacing="0"
                 style="background:#0E0E12;border:1px solid #E11D2E;border-radius:16px;overflow:hidden;">
            <tr>
              <td align="center" style="background:#0B0C10;padding:36px 0 24px;">
                <p style="margin:0;font-size:11px;letter-spacing:6px;color:#E11D2E;text-transform:uppercase;">
                  ⚡ BOOKMYCONSOLE GAMING HUB ⚡
                </p>
                <h1 style="margin:10px 0 0;font-size:26px;letter-spacing:3px;color:#ffffff;text-transform:uppercase;">
                  SUPPORT QUERY
                </h1>
                <p style="margin:6px 0 0;font-size:10px;letter-spacing:2px;color:#6B7280;text-transform:uppercase;">
                  via {html.escape(source)}
                </p>
              </td>
            </tr>
            <tr>
              <td style="padding:32px 40px;">
                <table width="100%" cellpadding="0" cellspacing="0" style="background:#0B0C10;border:1px solid #1f1f28;border-radius:12px;margin-bottom:20px;">
                  <tr><td style="padding:16px 20px;border-bottom:1px solid #1f1f28;">
                    <p style="margin:0;font-size:9px;letter-spacing:2px;color:#6B7280;text-transform:uppercase;">From</p>
                    <p style="margin:4px 0 0;font-size:15px;color:#ffffff;font-weight:bold;">{safe_name}</p>
                  </td></tr>
                  <tr><td style="padding:16px 20px;">
                    <p style="margin:0;font-size:9px;letter-spacing:2px;color:#6B7280;text-transform:uppercase;">Email</p>
                    <p style="margin:4px 0 0;font-size:14px;color:#E11D2E;">{safe_email}</p>
                  </td></tr>
                </table>
                <p style="margin:0 0 8px;font-size:9px;letter-spacing:2px;color:#6B7280;text-transform:uppercase;">Message</p>
                <p style="color:#e5e7eb;font-size:14px;line-height:1.7;margin:0;white-space:pre-wrap;">{safe_message}</p>
              </td>
            </tr>
            <tr>
              <td align="center" style="border-top:1px solid #1f1f28;padding:20px 40px;">
                <p style="margin:0;font-size:9px;letter-spacing:3px;color:#374151;text-transform:uppercase;">
                  Reply to this email to respond directly to {safe_email or "the sender"}.
                </p>
              </td>
            </tr>
          </table>
        </td></tr>
      </table>
    </body>
    </html>
    """
    return _send_email(SUPPORT_EMAIL, subject, html_body, reply_to=from_email or None)


def send_sms_otp(phone_number: str, otp: str) -> bool:
    """SMS OTP stub — always succeeds (no Twilio configured for BookMyConsole)."""
    if settings.DEBUG:
        print(f"[SMS OTP STUB] {phone_number} → {otp}")
    return True
