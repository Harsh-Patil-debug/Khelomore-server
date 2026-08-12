import uuid
import logging
import requests
from bson import ObjectId
from django.conf import settings

logger = logging.getLogger(__name__)

CASHFREE_API_VERSION = "2023-08-01"


def _cashfree_base_url():
    """Sandbox vs production Cashfree API host, switched by a single env var
    (CASHFREE_ENV) rather than different code paths — flipping to production later is a
    one-line settings change, not a redeploy of different logic."""
    env = getattr(settings, "CASHFREE_ENV", "sandbox")
    return "https://sandbox.cashfree.com/pg" if env != "production" else "https://api.cashfree.com/pg"


def _cashfree_headers(client_id, client_secret):
    return {
        "x-client-id": client_id,
        "x-client-secret": client_secret,
        "x-api-version": CASHFREE_API_VERSION,
        "Content-Type": "application/json",
    }


def get_cafe_cashfree_credentials(cafe_doc):
    """
    Resolves which Cashfree account a cafe's booking payments should go through.

    Returns (client_id, client_secret, used_platform_fallback). If the cafe owner has
    entered their own Cashfree Client ID + Secret (cafe-command-center → Cafe Profile),
    payments go straight to their own account. Otherwise this falls back to the
    platform's own account (per product decision: bookings still work immediately for a
    cafe that hasn't configured payments yet, and the platform settles that money to the
    owner manually/via Payouts until they do) — used_platform_fallback tells the caller
    which happened, so it can be recorded on the booking for that reconciliation.
    """
    client_id = cafe_doc.get("cashfree_client_id")
    client_secret_enc = cafe_doc.get("cashfree_client_secret_enc")
    if client_id and client_secret_enc:
        try:
            from .auth_handler import decrypt_secret_key
            client_secret = decrypt_secret_key(client_secret_enc)
            return client_id, client_secret, False
        except Exception as e:
            logger.error(f"[Cashfree] Failed to decrypt cafe {cafe_doc.get('_id')}'s client secret: {e}")

    platform_client_id = getattr(settings, "CASHFREE_CLIENT_ID", "")
    platform_client_secret = getattr(settings, "CASHFREE_CLIENT_SECRET", "")
    return platform_client_id, platform_client_secret, True


_used_payments_index_ensured = False


def _ensure_used_payments_index():
    global _used_payments_index_ensured
    if not _used_payments_index_ensured:
        try:
            from .db_connection import db_main
            db_main.used_cashfree_payments.create_index("order_id", unique=True)
        except Exception:
            pass
        _used_payments_index_ensured = True


def verify_cashfree_payment(order_id, expected_amount_paise, cafe_id=None):
    """
    Verifies a completed Cashfree payment server-side. Unlike Razorpay's model, there is
    no client-side signature to check — the client only ever receives a payment_session_id,
    never a secret, so nothing it reports back is trusted at all. Instead this
    independently asks Cashfree's own API (using OUR credentials, never anything the
    client supplied) what actually happened to this order. Returns True only if ALL of:
      1. Cashfree's own records show this exact order_id has order_status == "PAID".
      2. The order was created for — and paid at — EXACTLY expected_amount_paise
         (converted to rupees, since Cashfree's order_amount is in standard currency
         units, not paise like Razorpay). A client could otherwise create a genuinely
         paid order for ₹1 and reuse that success to unlock a ₹200 slot.
      3. This order_id has not already been used for an earlier booking/registration
         (replay protection) — one order may only ever unlock one booking.
    On success, atomically claims the order_id so it can never be reused.

    cafe_id is only passed by the per-cafe booking payment flow (see
    create_cafe_booking_order_handler). When present, the credentials used to verify are
    whichever ones actually created this order — looked up from cafe_payment_orders,
    never trusted from the client — and the order is required to belong to that cafe, so
    an order paid for at cafe A can never be replayed to unlock a booking at cafe B.
    Callers that don't pass cafe_id (subscriptions, tournament registration) keep using
    the platform's own account, unchanged.
    """
    # SECURITY: order_id arrives straight from the client's JSON body. A dict here (e.g.
    # {"$ne": None}) instead of a plain string would corrupt the cafe_payment_orders/
    # used_cashfree_payments filters below into matching anything. The real gate is
    # Cashfree's own order-status lookup further down, which a forged dict can't fake —
    # but that shouldn't be the only thing standing between a type-confused filter and an
    # unintended match, so fail closed here explicitly too.
    if not isinstance(order_id, str) or not order_id:
        return False

    client_id = getattr(settings, "CASHFREE_CLIENT_ID", "")
    client_secret = getattr(settings, "CASHFREE_CLIENT_SECRET", "")

    if cafe_id is not None:
        from .db_connection import db_main
        order_record = db_main.cafe_payment_orders.find_one({"order_id": order_id, "cafe_id": str(cafe_id)})
        if not order_record:
            logger.warning(f"[Cashfree] No order record for order {order_id} under cafe {cafe_id} — rejecting.")
            return False
        if not order_record.get("used_platform_fallback"):
            cafe = db_main.cafes.find_one({"_id": ObjectId(cafe_id)}) if ObjectId.is_valid(str(cafe_id)) else None
            if not cafe:
                return False
            client_id, client_secret, _ = get_cafe_cashfree_credentials(cafe)

    if not client_id or not client_secret:
        logger.warning("[Cashfree] Missing credentials — cannot verify payment.")
        return False

    try:
        response = requests.get(
            f"{_cashfree_base_url()}/orders/{order_id}",
            headers=_cashfree_headers(client_id, client_secret),
            timeout=15,
        )
        response.raise_for_status()
        order = response.json()
    except Exception as e:
        logger.error(f"[Cashfree] Failed to fetch order {order_id}: {str(e)}", exc_info=True)
        return False

    expected_amount_inr = expected_amount_paise / 100
    order_amount = order.get("order_amount")
    if order_amount is None or round(float(order_amount), 2) != round(expected_amount_inr, 2):
        logger.warning(
            f"[Cashfree] Amount mismatch on order {order_id}: "
            f"order={order_amount} expected={expected_amount_inr}"
        )
        return False

    if order.get("order_status") != "PAID":
        logger.warning(f"[Cashfree] Order {order_id} is not fully paid (status={order.get('order_status')}).")
        return False

    _ensure_used_payments_index()
    from .db_connection import db_main
    try:
        claim = db_main.used_cashfree_payments.update_one(
            {"order_id": order_id},
            {"$setOnInsert": {"order_id": order_id}},
            upsert=True,
        )
    except Exception as e:
        # Duplicate key on the unique index also means "already used" — treat as replay.
        logger.warning(f"[Cashfree] Payment claim failed for order {order_id}: {str(e)}")
        return False

    if claim.upserted_id is None:
        logger.warning(f"[Cashfree] Order {order_id} has already been used — replay blocked.")
        return False

    return True


def create_cashfree_order_handler(amount_in_inr, customer_email=None, customer_phone=None, client_id=None, client_secret=None):
    """
    Creates a real Cashfree Order using Cashfree API credentials. Amount is in standard
    INR (not paise like Razorpay) — Cashfree's own API takes rupees directly.

    client_id/client_secret default to the platform's own account (subscriptions,
    tournament registration) — the per-cafe booking flow passes a specific cafe's own
    credentials (or the platform's, as a fallback) instead. See
    create_cafe_booking_order_handler.

    Cashfree requires customer_details on every order (unlike Razorpay) — falls back to
    placeholder values if the caller didn't have real ones on hand, though real
    deployments should always pass the actual customer's email/phone.
    """
    if client_id is None:
        client_id = getattr(settings, "CASHFREE_CLIENT_ID", "")
    if client_secret is None:
        client_secret = getattr(settings, "CASHFREE_CLIENT_SECRET", "")

    order_amount = round(float(amount_in_inr), 2)
    order_id = f"order_{uuid.uuid4().hex[:20]}"
    customer_id = uuid.uuid5(uuid.NAMESPACE_DNS, customer_email or order_id).hex[:24]

    if not client_id or not client_secret:
        logger.warning("[Cashfree] Missing CASHFREE_CLIENT_ID or CASHFREE_CLIENT_SECRET. Generating mock order.")
        return {
            "order_id": order_id,
            "order_amount": order_amount,
            "order_currency": "INR",
            "order_status": "ACTIVE",
            "payment_session_id": f"session_mock_{uuid.uuid4().hex[:16]}",
            "is_mock": True,
        }

    try:
        payload = {
            "order_id": order_id,
            "order_amount": order_amount,
            "order_currency": "INR",
            "customer_details": {
                "customer_id": customer_id,
                "customer_email": customer_email or "guest@bookmyconsole.com",
                "customer_phone": customer_phone or "9999999999",
            },
            # The mobile app's WebView never actually navigates here — it intercepts this
            # exact URL pattern via onShouldStartLoadWithRequest the instant Cashfree's
            # hosted checkout tries to redirect to it (see cashfree-webview.ts), extracts
            # order_id, and hands control back to the app. {order_id} is Cashfree's own
            # template placeholder, substituted server-side on redirect.
            "order_meta": {
                "return_url": "https://bookmyconsole.com/payment-return?order_id={order_id}",
            },
        }
        response = requests.post(
            f"{_cashfree_base_url()}/orders",
            headers=_cashfree_headers(client_id, client_secret),
            json=payload,
            timeout=15,
        )
        response.raise_for_status()
        order = response.json()
        logger.info(f"[Cashfree] Successfully created order: {order.get('order_id')}")
        return order
    except Exception as e:
        logger.error(f"[Cashfree] Exception during order creation: {str(e)}. Falling back to mock.", exc_info=True)
        return {
            "order_id": order_id,
            "order_amount": order_amount,
            "order_currency": "INR",
            "order_status": "ACTIVE",
            "payment_session_id": f"session_mock_{uuid.uuid4().hex[:16]}",
            "error_msg": str(e),
            "is_mock": True,
        }


def create_cafe_booking_order_handler(cafe_id, amount_in_inr, zone=None, date=None, slots=None, rig=None, user_email=None):
    """
    Creates a Cashfree order for a booking at a specific cafe, using that cafe's own
    Cashfree account if the owner has configured one (cafe-command-center → Cafe
    Profile), or the platform's account as a fallback otherwise.

    Records which account was used, keyed by order_id, in cafe_payment_orders — this is
    the source of truth verify_cashfree_payment consults later (never the client), and
    lets bookings_handler stamp "settled directly to cafe" vs "held by platform, needs
    payout" onto the resulting booking.

    zone/date/slots/rig are only passed for slot-booking payments (never tournament
    entry, which has no slot). When present, this atomically HOLDS those exact slots
    before the Cashfree order is even created — see hold_slots_for_payment. That's what
    actually stops "pay in full for a slot someone else already took, then get told to
    contact support for a refund": the slot is claimed the instant checkout starts, not
    just soft-checked and hoped-for, so a losing concurrent request is rejected here,
    before it ever reaches Cashfree, instead of after paying.
    """
    from .db_connection import db_main
    if not ObjectId.is_valid(str(cafe_id)):
        return {"status": "error", "message": "Invalid cafe id."}
    cafe = db_main.cafes.find_one({"_id": ObjectId(cafe_id)})
    if not cafe:
        return {"status": "error", "message": "Cafe not found."}

    hold_token = None
    if date and slots:
        import uuid as _uuid
        from .bookings_handler import hold_slots_for_payment
        hold_token = _uuid.uuid4().hex
        ok, message = hold_slots_for_payment(str(cafe_id), str(date), zone, slots, rig, hold_token, user_email=user_email)
        if not ok:
            return {"status": "error", "message": message}

    customer_phone = None
    if user_email:
        user_doc = db_main.users.find_one({"email": user_email}, {"phone": 1})
        customer_phone = user_doc.get("phone") if user_doc else None

    client_id, client_secret, used_platform_fallback = get_cafe_cashfree_credentials(cafe)
    order = create_cashfree_order_handler(amount_in_inr, user_email, customer_phone, client_id, client_secret)

    if not order.get("is_mock") and order.get("order_id"):
        db_main.cafe_payment_orders.insert_one({
            "order_id": order["order_id"],
            "cafe_id": str(cafe_id),
            "used_platform_fallback": used_platform_fallback,
            "hold_token": hold_token,
            "hold_date": date,
            "hold_slots": slots,
            "hold_rig": rig,
        })
    elif hold_token:
        # Order creation failed or fell back to a mock (missing Cashfree creds) — release
        # the hold immediately rather than leaving it to sit out its full TTL for nothing.
        from .bookings_handler import release_slot_hold
        release_slot_hold(str(cafe_id), str(date), rig, slots or [], hold_token)

    order["used_platform_fallback"] = used_platform_fallback
    return order


def release_cafe_booking_hold(cafe_id, order_id):
    """
    Explicitly releases a pre-payment slot hold when the customer cancels or fails
    checkout, so the slot frees up immediately instead of waiting out the TTL (see
    hold_slots_for_payment). Best-effort UX nicety only — the TTL index on
    slot_locks.expires_at is what actually guarantees an abandoned hold (app killed,
    network drop, anything that never calls this) can't lock a slot forever.
    """
    from .db_connection import db_main
    from .bookings_handler import release_slot_hold
    order_record = db_main.cafe_payment_orders.find_one({"order_id": order_id, "cafe_id": str(cafe_id)})
    if not order_record or not order_record.get("hold_token"):
        return
    release_slot_hold(
        str(cafe_id),
        order_record.get("hold_date"),
        order_record.get("hold_rig"),
        order_record.get("hold_slots") or [],
        order_record.get("hold_token"),
    )
