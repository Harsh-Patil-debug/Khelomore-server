# support.py
# "Contact Support" — shows the platform's support contact info and forwards
# user-submitted queries to it by email. Used by both the gamer app and the
# cafe-owner dashboard (both authenticate via the same JWT scheme).

from . import email_handler


def get_support_info_handler():
    """Public info the UI displays — support email + phone. No auth needed, not sensitive."""
    return {
        "status": "success",
        "support_email": email_handler.SUPPORT_EMAIL,
        "support_phone": email_handler.SUPPORT_PHONE,
    }, 200


def create_support_query_handler(from_email: str, from_name: str, message: str, source: str = "App"):
    """Forwards a support query to SUPPORT_EMAIL. `from_email` is the authenticated
    caller's own email (from their verified JWT) when logged in — the marketing site
    lets a logged-out visitor submit too, in which case it's whatever email they typed
    into the form instead, same as any public "contact us" form."""
    try:
        if not from_email or not from_email.strip():
            return {"status": "error", "message": "Please provide your email so support can respond."}, 400
        if not message or not message.strip():
            return {"status": "error", "message": "Please write a message before submitting."}, 400
        if len(message) > 5000:
            return {"status": "error", "message": "Message is too long (max 5000 characters)."}, 400

        sent = email_handler.send_support_query_email(
            from_name=from_name or from_email, from_email=from_email, message=message.strip(), source=source
        )
        if not sent:
            return {
                "status": "error",
                "message": "Failed to send your message right now — please try again in a moment.",
            }, 502

        return {"status": "success", "message": "Your message has been sent to support."}, 200
    except Exception as e:
        return {"status": "error", "message": f"Failed to submit support query: {e}"}, 500
