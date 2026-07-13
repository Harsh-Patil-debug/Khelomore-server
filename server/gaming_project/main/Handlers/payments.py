import razorpay
import razorpay.errors
import uuid
import logging
from django.conf import settings

logger = logging.getLogger(__name__)


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


def verify_razorpay_payment(order_id, payment_id, signature, expected_amount_paise):
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
    """
    key_id = getattr(settings, 'RAZORPAY_KEY_ID', '')
    key_secret = getattr(settings, 'RAZORPAY_KEY_SECRET', '')
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

def create_razorpay_order_handler(amount_in_inr):
    """
    Creates a real Razorpay Order using Razorpay API credentials.
    Converts INR amount to Paise (e.g. 100 INR = 10000 Paise).
    """
    key_id = getattr(settings, 'RAZORPAY_KEY_ID', '')
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
