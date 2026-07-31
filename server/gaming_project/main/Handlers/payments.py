import razorpay
import razorpay.errors
import uuid
import logging
from bson import ObjectId
from django.conf import settings

logger = logging.getLogger(__name__)


def get_cafe_razorpay_credentials(cafe_doc):
    """
    Resolves which Razorpay account a cafe's booking payments should go through.

    Returns (key_id, key_secret, used_platform_fallback). If the cafe owner has entered
    their own Razorpay Key ID + Secret (cafe-command-center → Cafe Profile), payments go
    straight to their own account. Otherwise this falls back to the platform's own
    account (per product decision: bookings still work immediately for a cafe that
    hasn't configured payments yet, and the platform settles that money to the owner
    manually until they do) — used_platform_fallback tells the caller which happened, so
    it can be recorded on the booking for that reconciliation.
    """
    key_id = cafe_doc.get("razorpay_key_id")
    key_secret_enc = cafe_doc.get("razorpay_key_secret_enc")
    if key_id and key_secret_enc:
        try:
            from .auth_handler import decrypt_secret_key
            key_secret = decrypt_secret_key(key_secret_enc)
            return key_id, key_secret, False
        except Exception as e:
            logger.error(f"[Razorpay] Failed to decrypt cafe {cafe_doc.get('_id')}'s key secret: {e}")

    platform_key_id = getattr(settings, 'RAZORPAY_KEY_ID', '')
    platform_key_secret = getattr(settings, 'RAZORPAY_KEY_SECRET', '')
    return platform_key_id, platform_key_secret, True


_used_payments_index_ensured = False


def _ensure_used_payments_index():
    global _used_payments_index_ensured
    if not _used_payments_index_ensured:
        try:
            from .db_connection import db_main
            db_main.used_razorpay_payments.create_index("payment_id", unique=True)
        except Exception:
            pass
        _used_payments_index_ensured = True


def verify_razorpay_payment(order_id, payment_id, signature, expected_amount_paise, cafe_id=None):
    """
    Verifies a completed Razorpay payment server-side. Returns True only if ALL of:
      1. The signature is cryptographically valid for (order_id, payment_id).
      2. The order was actually created for — and is fully paid at — EXACTLY
         expected_amount_paise. Signature validity alone is NOT enough: a client can
         create (and genuinely pay) a real order for any amount via
         /payments/create-order/, so the order's real amount must be cross-checked
         against what this specific booking/registration actually costs, or someone
         could pay ₹1 for real and use that valid payment to unlock a ₹200 slot.
      3. This payment_id has not already been used for an earlier booking/registration
         (replay protection) — one payment may only ever unlock one booking.
    On success, atomically claims the payment_id so it can never be reused.

    cafe_id is only passed by the per-cafe booking payment flow (see
    create_cafe_booking_order_handler). When present, the credentials used to verify are
    whichever ones actually created this order — looked up from cafe_payment_orders,
    never trusted from the client — and the order is required to belong to that cafe, so
    an order paid for at cafe A can never be replayed to unlock a booking at cafe B.
    Callers that don't pass cafe_id (subscriptions, tournament registration) keep using
    the platform's own account, unchanged.
    """
    # SECURITY: order_id/payment_id/signature arrive straight from the client's JSON body.
    # A dict here (e.g. {"$ne": None}) instead of a plain string would corrupt the
    # cafe_payment_orders/used_razorpay_payments filters below into matching anything.
    # The real gate is the cryptographic signature check further down, which a forged
    # dict can't pass — but that shouldn't be the only thing standing between a type-
    # confused filter and an unintended match, so fail closed here explicitly too.
    if not isinstance(order_id, str) or not isinstance(payment_id, str) or not isinstance(signature, str):
        return False

    key_id = getattr(settings, 'RAZORPAY_KEY_ID', '')
    key_secret = getattr(settings, 'RAZORPAY_KEY_SECRET', '')

    if cafe_id is not None:
        from .db_connection import db_main
        order_record = db_main.cafe_payment_orders.find_one({"order_id": order_id, "cafe_id": str(cafe_id)})
        if not order_record:
            logger.warning(f"[Razorpay] No order record for order {order_id} under cafe {cafe_id} — rejecting.")
            return False
        if not order_record.get("used_platform_fallback"):
            cafe = db_main.cafes.find_one({"_id": ObjectId(cafe_id)}) if ObjectId.is_valid(str(cafe_id)) else None
            if not cafe:
                return False
            key_id, key_secret, _ = get_cafe_razorpay_credentials(cafe)

    if not key_id or not key_secret:
        logger.warning("[Razorpay] Missing credentials — cannot verify payment signature.")
        return False
    if not order_id or not payment_id or not signature:
        return False

    client = razorpay.Client(auth=(key_id, key_secret))

    try:
        client.utility.verify_payment_signature({
            "razorpay_order_id": order_id,
            "razorpay_payment_id": payment_id,
            "razorpay_signature": signature,
        })
    except razorpay.errors.SignatureVerificationError:
        logger.warning(f"[Razorpay] Signature verification failed for order {order_id}.")
        return False
    except Exception as e:
        logger.error(f"[Razorpay] Exception during signature verification: {str(e)}", exc_info=True)
        return False

    try:
        order = client.order.fetch(order_id)
    except Exception as e:
        logger.error(f"[Razorpay] Failed to fetch order {order_id}: {str(e)}", exc_info=True)
        return False

    if order.get("amount") != expected_amount_paise:
        logger.warning(
            f"[Razorpay] Amount mismatch on order {order_id}: "
            f"order={order.get('amount')} expected={expected_amount_paise}"
        )
        return False

    if order.get("status") != "paid":
        logger.warning(f"[Razorpay] Order {order_id} is not fully paid (status={order.get('status')}).")
        return False

    _ensure_used_payments_index()
    from .db_connection import db_main
    try:
        claim = db_main.used_razorpay_payments.update_one(
            {"payment_id": payment_id},
            {"$setOnInsert": {"payment_id": payment_id, "order_id": order_id}},
            upsert=True,
        )
    except Exception as e:
        # Duplicate key on the unique index also means "already used" — treat as replay.
        logger.warning(f"[Razorpay] Payment claim failed for {payment_id}: {str(e)}")
        return False

    if claim.upserted_id is None:
        logger.warning(f"[Razorpay] Payment {payment_id} has already been used — replay blocked.")
        return False

    return True

def create_razorpay_order_handler(amount_in_inr, key_id=None, key_secret=None):
    """
    Creates a real Razorpay Order using Razorpay API credentials.
    Converts INR amount to Paise (e.g. 100 INR = 10000 Paise).

    key_id/key_secret default to the platform's own account (subscriptions, tournament
    registration) — the per-cafe booking flow passes a specific cafe's own credentials
    (or the platform's, as a fallback) instead. See create_cafe_booking_order_handler.
    """
    if key_id is None:
        key_id = getattr(settings, 'RAZORPAY_KEY_ID', '')
    if key_secret is None:
        key_secret = getattr(settings, 'RAZORPAY_KEY_SECRET', '')

    amount_in_paise = int(float(amount_in_inr) * 100)
    receipt_id = f"rcpt_{uuid.uuid4().hex[:10]}"

    if not key_id or not key_secret:
        logger.warning("[Razorpay] Missing RAZORPAY_KEY_ID or RAZORPAY_KEY_SECRET. Generating mock order.")
        return {
            "id": f"order_mock_{uuid.uuid4().hex[:12]}",
            "entity": "order",
            "amount": amount_in_paise,
            "amount_paid": 0,
            "amount_due": amount_in_paise,
            "currency": "INR",
            "receipt": receipt_id,
            "status": "created",
            "created_at": 1600000000,
            "is_mock": True
        }

    try:
        client = razorpay.Client(auth=(key_id, key_secret))
        data = {
            "amount": amount_in_paise,
            "currency": "INR",
            "receipt": receipt_id
        }
        order = client.order.create(data=data)
        logger.info(f"[Razorpay] Successfully created order: {order.get('id')}")
        return order
    except Exception as e:
        logger.error(f"[Razorpay] Exception during order creation: {str(e)}. Falling back to mock.", exc_info=True)
        return {
            "id": f"order_mock_{uuid.uuid4().hex[:12]}",
            "entity": "order",
            "amount": amount_in_paise,
            "amount_paid": 0,
            "amount_due": amount_in_paise,
            "currency": "INR",
            "receipt": receipt_id,
            "status": "created",
            "created_at": 1600000000,
            "error_msg": str(e),
            "is_mock": True
        }


def create_cafe_booking_order_handler(cafe_id, amount_in_inr):
    """
    Creates a Razorpay order for a booking at a specific cafe, using that cafe's own
    Razorpay account if the owner has configured one (cafe-command-center → Cafe
    Profile), or the platform's account as a fallback otherwise.

    Records which account was used, keyed by order_id, in cafe_payment_orders — this is
    the source of truth verify_razorpay_payment consults later (never the client), and
    lets bookings_handler stamp "settled directly to cafe" vs "held by platform, needs
    manual payout" onto the resulting booking.
    """
    from .db_connection import db_main
    if not ObjectId.is_valid(str(cafe_id)):
        return {"status": "error", "message": "Invalid cafe id."}
    cafe = db_main.cafes.find_one({"_id": ObjectId(cafe_id)})
    if not cafe:
        return {"status": "error", "message": "Cafe not found."}

    key_id, key_secret, used_platform_fallback = get_cafe_razorpay_credentials(cafe)
    order = create_razorpay_order_handler(amount_in_inr, key_id, key_secret)

    if not order.get("is_mock") and order.get("id"):
        db_main.cafe_payment_orders.insert_one({
            "order_id": order["id"],
            "cafe_id": str(cafe_id),
            "used_platform_fallback": used_platform_fallback,
        })

    order["key_id"] = key_id
    order["used_platform_fallback"] = used_platform_fallback
    return order
