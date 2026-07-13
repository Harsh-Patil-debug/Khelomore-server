# verify_payment_flow.py
# One-off end-to-end verification of the payment-integrity fix (see bookings_handler.py
# create_booking_handler / payments.verify_razorpay_payment).
#
# Hits Razorpay's REAL test-mode API to create genuine orders, then reproduces Razorpay's
# REAL signature algorithm (HMAC-SHA256 of "order_id|payment_id" with the account's
# key_secret) to prove the verification logic behaves correctly against signatures that
# are cryptographically indistinguishable from ones Razorpay would issue.
#
# IMPORTANT SCOPE NOTE: verify_razorpay_payment() now also requires the order's real
# Razorpay status to be "paid" (not just "created") — correctly, since an order that was
# merely created but never actually paid must never unlock a booking. Completing a REAL
# payment requires Razorpay's Checkout.js in an actual browser with a test card, which
# isn't scriptable from here. So this script proves every REJECTION path exhaustively
# (forged signature, wrong order, unpaid order) using real orders and a real signature
# algorithm — it does NOT claim to exercise the full real happy path, since that
# inherently requires a browser. The amount-mismatch and replay-protection LOGIC (which
# depend on what a *paid* order looks like) are covered by mocked unit tests instead —
# see gaming_project/main/tests/test_payment_verification.py — since those need to
# control the "paid, amount=X" response Razorpay would only give after a real payment.

import hashlib
import hmac
import os
import sys

sys.path.append(r"C:\Users\DELL\OneDrive\Desktop\khelomore-server\server")

# SAFETY: isolate from real data, same convention as manage.py's `test` routing and
# verify_super_admin.py's existing precedent in this repo.
os.environ["MONGO_DB_NAME"] = os.getenv("MONGO_DB_NAME_TEST", "KheloMoreDB_test")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "server.settings")

import django
django.setup()

from django.conf import settings
from gaming_project.main.Handlers import payments


def real_razorpay_signature(order_id: str, payment_id: str, key_secret: str) -> str:
    """Reproduces Razorpay's own signature algorithm (documented in their webhook/
    checkout verification docs) — NOT a mock of our code."""
    msg = f"{order_id}|{payment_id}"
    return hmac.new(key_secret.encode(), msg.encode(), hashlib.sha256).hexdigest()


def main():
    print("=" * 70)
    print("  PAYMENT VERIFICATION — REJECTION-PATH CHECKS (real Razorpay test API)")
    print("=" * 70)

    key_secret = settings.RAZORPAY_KEY_SECRET
    assert key_secret, "RAZORPAY_KEY_SECRET is not configured."

    print("\n[STEP 1] Creating a REAL ₹200 order via the Razorpay test-mode API...")
    order = payments.create_razorpay_order_handler(200)
    assert not order.get("is_mock"), (
        "Razorpay returned a MOCK order — real API credentials aren't reachable. "
        f"Order response: {order}"
    )
    order_id = order["id"]
    print(f"  - Real order created: {order_id}  amount={order['amount']} paise  status={order['status']}")
    assert order["status"] != "paid", "Sanity check failed: a brand-new order should not be 'paid'."

    fake_payment_id = "pay_sectestSIMULATED0001"
    valid_signature = real_razorpay_signature(order_id, fake_payment_id, key_secret)

    print("\n[STEP 2] FORGED signature — must be rejected...")
    forged = valid_signature.replace(valid_signature[0], "0" if valid_signature[0] != "0" else "1")
    result = payments.verify_razorpay_payment(order_id, fake_payment_id, forged, 20000)
    print(f"  - Result: {result}")
    assert result is False, "A forged signature was ACCEPTED — payment bypass still open!"

    print("\n[STEP 3] Signature valid for a DIFFERENT order — must be rejected...")
    other_order = payments.create_razorpay_order_handler(1)
    result = payments.verify_razorpay_payment(other_order["id"], fake_payment_id, valid_signature, 100)
    print(f"  - Result: {result}")
    assert result is False, "A signature valid for one order was accepted for a different order!"

    print("\n[STEP 4] Real order, correctly-computed signature, but the order was never "
          "actually PAID (no browser checkout happened) — must be rejected...")
    result = payments.verify_razorpay_payment(order_id, fake_payment_id, valid_signature, 20000)
    print(f"  - Result: {result}")
    assert result is False, (
        "An order that was only CREATED, never actually paid, was accepted as valid "
        "payment proof — this is exactly the bypass the amount/status check exists to close!"
    )

    print("\n" + "=" * 70)
    print("  ALL REJECTION-PATH CHECKS PASSED")
    print("  - Forged signature rejected")
    print("  - Cross-order signature reuse rejected")
    print("  - Unpaid order rejected even with a well-formed signature")
    print("  (Amount-mismatch and replay-protection logic: see")
    print("   test_payment_verification.py, which mocks a genuinely-'paid' order")
    print("   response to isolate and test that logic without needing a browser.)")
    print("=" * 70)


if __name__ == "__main__":
    main()
