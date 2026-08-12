# verify_payment_flow.py
# One-off end-to-end verification of the payment-integrity fix (see bookings_handler.py
# create_booking_handler / payments.verify_cashfree_payment).
#
# Hits Cashfree's REAL sandbox API to create genuine orders, then proves the verification
# logic correctly rejects everything it should.
#
# IMPORTANT SCOPE NOTE: unlike Razorpay, Cashfree never gives the client a signature to
# verify at all — verify_cashfree_payment works purely by asking Cashfree's own API what
# actually happened to a given order_id, using OUR credentials. Completing a REAL payment
# requires Cashfree's hosted checkout in an actual browser with a sandbox test card,
# which isn't scriptable from here. So this script proves every REJECTION path
# exhaustively (unpaid order, unknown order) using real sandbox orders — it does NOT
# claim to exercise the full real happy path, since that inherently requires a browser.
# The amount-mismatch and replay-protection LOGIC (which depend on what a *paid* order
# looks like) are covered by mocked unit tests instead — see
# gaming_project/main/tests/test_payment_verification.py — since those need to control
# the "PAID, amount=X" response Cashfree would only give after a real payment.
#
# Requires real CASHFREE_CLIENT_ID/CASHFREE_CLIENT_SECRET (sandbox) configured in the
# environment this runs in — see .env.example. Run this after any change to payments.py
# before trusting it against real money.

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


def main():
    print("=" * 70)
    print("  PAYMENT VERIFICATION — REJECTION-PATH CHECKS (real Cashfree sandbox API)")
    print("=" * 70)

    assert getattr(settings, "CASHFREE_ENV", "sandbox") != "production", (
        "Refusing to run against a production CASHFREE_ENV — this script creates real "
        "orders and is meant for sandbox verification only."
    )
    client_id = settings.CASHFREE_CLIENT_ID
    client_secret = settings.CASHFREE_CLIENT_SECRET
    assert client_id and client_secret, "CASHFREE_CLIENT_ID/CASHFREE_CLIENT_SECRET are not configured."

    print("\n[STEP 1] Creating a REAL ₹200 order via the Cashfree sandbox API...")
    order = payments.create_cashfree_order_handler(200, customer_email="verify-script@bookmyconsole.invalid", customer_phone="9999999999")
    assert not order.get("is_mock"), (
        "Cashfree returned a MOCK order — real sandbox credentials aren't reachable. "
        f"Order response: {order}"
    )
    order_id = order["order_id"]
    print(f"  - Real order created: {order_id}  amount={order['order_amount']}  status={order['order_status']}  session={order['payment_session_id']}")
    assert order["order_status"] != "PAID", "Sanity check failed: a brand-new order should not be 'PAID'."

    print("\n[STEP 2] Real order, correct amount, but never actually PAID (no browser "
          "checkout happened) — must be rejected...")
    result = payments.verify_cashfree_payment(order_id, 20000)
    print(f"  - Result: {result}")
    assert result is False, (
        "An order that was only CREATED, never actually paid, was accepted as valid "
        "payment proof — this is exactly the bypass the status check exists to close!"
    )

    print("\n[STEP 3] Unknown/nonexistent order_id — must be rejected...")
    result = payments.verify_cashfree_payment("order_never_created_by_this_script", 20000)
    print(f"  - Result: {result}")
    assert result is False, "A nonexistent order_id was somehow accepted as valid payment proof!"

    print("\n[STEP 4] Non-string order_id (type-confusion / NoSQL-injection shape) — must "
          "be rejected before ever reaching the network...")
    result = payments.verify_cashfree_payment({"$ne": None}, 20000)
    print(f"  - Result: {result}")
    assert result is False, "A dict passed as order_id was not rejected outright!"

    print("\n" + "=" * 70)
    print("  ALL REJECTION-PATH CHECKS PASSED")
    print("  - Unpaid order rejected")
    print("  - Unknown order_id rejected")
    print("  - Non-string/type-confused order_id rejected")
    print("  (Amount-mismatch and replay-protection logic: see")
    print("   test_payment_verification.py, which mocks a genuinely-'PAID' order")
    print("   response to isolate and test that logic without needing a browser.)")
    print("=" * 70)


if __name__ == "__main__":
    main()
