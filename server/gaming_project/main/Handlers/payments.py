import razorpay
import uuid
import logging
from django.conf import settings

logger = logging.getLogger(__name__)

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
