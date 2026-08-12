# payouts.py
# Settles bookings/tournament-entry money the PLATFORM collected on a cafe's behalf (see
# payment_settlement: "platform_pending_payout" in bookings_handler.py/tournaments.py —
# set whenever a cafe hasn't connected its own Cashfree account) out to that cafe's real
# bank account, via Cashfree's Payouts product. This is a SEPARATE Cashfree product from
# Payment Gateway (separate credentials, separate API surface) — see
# CASHFREE_PAYOUTS_CLIENT_ID/SECRET in settings.py.
#
# IMPORTANT: this is written against Cashfree's documented Payouts v2 shape (token-auth,
# addBeneficiary/requestTransfer/getTransferStatus). Cashfree's Payouts API has changed
# shape across versions more than the core Payment Gateway API has — verify every
# endpoint path and field name here against the current Cashfree Payouts docs before
# relying on this in production, and adjust as needed. Nothing here has been exercised
# against a real Cashfree Payouts sandbox account yet.
#
# Deliberately manual, not an unattended cron job, for this first version — see
# settle_pending_payouts_for_cafe_handler. Automate later once the manual flow (triggered
# from the super admin panel) has been proven correct over real transactions.

import logging
import requests
from datetime import datetime, timezone
from bson import ObjectId
from django.conf import settings

logger = logging.getLogger(__name__)


def _payouts_base_url():
    env = getattr(settings, "CASHFREE_ENV", "sandbox")
    return "https://payout-gamma.cashfree.com/payout/v2" if env != "production" else "https://payout-api.cashfree.com/payout/v2"


def _get_payouts_token():
    """
    Cashfree Payouts uses short-lived bearer tokens (unlike the Payment Gateway API's
    per-request client-id/secret headers) — authorize once, reuse the token for
    subsequent calls within its validity window. Not cached across requests here since
    payout settlement is a low-frequency, admin-triggered action, not a hot path.
    """
    client_id = getattr(settings, "CASHFREE_PAYOUTS_CLIENT_ID", "")
    client_secret = getattr(settings, "CASHFREE_PAYOUTS_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        return None
    try:
        response = requests.post(
            f"{_payouts_base_url()}/authorize",
            headers={"X-Client-Id": client_id, "X-Client-Secret": client_secret},
            timeout=15,
        )
        response.raise_for_status()
        return response.json().get("data", {}).get("token")
    except Exception as e:
        logger.error(f"[Payouts] Failed to authorize: {e}", exc_info=True)
        return None


def create_cafe_beneficiary_handler(cafe_id, beneficiary_name, bank_account_number, ifsc, email=None, phone=None):
    """
    Registers a cafe's bank account as a Cashfree Payouts beneficiary — one-time setup
    per cafe, done from cafe-command-center when the owner enters their settlement bank
    details. beneId is derived deterministically from cafe_id so a re-registration
    (owner updates their bank details) reuses the same beneficiary rather than
    accumulating duplicates.
    """
    if not ObjectId.is_valid(str(cafe_id)):
        return {"status": "error", "message": "Invalid cafe id."}

    token = _get_payouts_token()
    if not token:
        return {"status": "error", "message": "Payouts is not configured — contact platform support."}

    bene_id = f"cafe_{str(cafe_id)}"
    try:
        response = requests.post(
            f"{_payouts_base_url()}/beneficiary",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={
                "beneficiary_id": bene_id,
                "beneficiary_name": beneficiary_name,
                "beneficiary_instrument_details": {
                    "bank_account_number": bank_account_number,
                    "bank_ifsc": ifsc,
                },
                "beneficiary_contact_details": {
                    "beneficiary_email": email or "",
                    "beneficiary_phone": phone or "",
                },
            },
            timeout=15,
        )
        response.raise_for_status()
    except Exception as e:
        logger.error(f"[Payouts] Failed to register beneficiary for cafe {cafe_id}: {e}", exc_info=True)
        return {"status": "error", "message": f"Failed to register bank account: {e}"}

    from .db_connection import db_main
    db_main.cafes.update_one(
        {"_id": ObjectId(cafe_id)},
        {"$set": {"payout_beneficiary_id": bene_id, "payout_bank_last4": bank_account_number[-4:]}},
    )
    return {"status": "success", "beneficiary_id": bene_id}


def settle_pending_payouts_for_cafe_handler(cafe_id, triggered_by):
    """
    Sums up every un-settled platform-collected booking/registration for this cafe
    (payment_settlement == "platform_pending_payout", payout_settled not yet set) and
    transfers that total to the cafe's registered beneficiary in one batch. Marks every
    included booking/registration as settled on success so it's never paid out twice.

    Manually triggered (super admin panel "Settle payouts" button) — see module docstring
    for why this isn't an unattended cron job yet.
    """
    if not ObjectId.is_valid(str(cafe_id)):
        return {"status": "error", "message": "Invalid cafe id."}

    from .db_connection import db_main
    cafe = db_main.cafes.find_one({"_id": ObjectId(cafe_id)})
    if not cafe:
        return {"status": "error", "message": "Cafe not found."}
    bene_id = cafe.get("payout_beneficiary_id")
    if not bene_id:
        return {"status": "error", "message": "This cafe hasn't registered a payout bank account yet."}

    pending_filter = {
        "cafe_id": str(cafe_id),
        "payment_settlement": "platform_pending_payout",
        "payout_settled": {"$ne": True},
    }
    pending_bookings = list(db_main.bookings.find(pending_filter))
    pending_registrations = list(db_main.tournament_registrations.find(pending_filter))
    total_amount = sum(b.get("price", 0) for b in pending_bookings) + sum(r.get("amount_paid", 0) for r in pending_registrations)

    if total_amount <= 0:
        return {"status": "error", "message": "Nothing pending to settle for this cafe."}

    token = _get_payouts_token()
    if not token:
        return {"status": "error", "message": "Payouts is not configured — contact platform support."}

    transfer_id = f"settle_{str(cafe_id)}_{int(datetime.now(timezone.utc).timestamp())}"
    try:
        response = requests.post(
            f"{_payouts_base_url()}/transfers",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={
                "transfer_id": transfer_id,
                "transfer_amount": total_amount,
                "beneficiary_details": {"beneficiary_id": bene_id},
            },
            timeout=15,
        )
        response.raise_for_status()
        transfer_result = response.json()
    except Exception as e:
        logger.error(f"[Payouts] Transfer failed for cafe {cafe_id}: {e}", exc_info=True)
        return {"status": "error", "message": f"Transfer failed: {e}"}

    booking_ids = [b["_id"] for b in pending_bookings]
    registration_ids = [r["_id"] for r in pending_registrations]
    if booking_ids:
        db_main.bookings.update_many(
            {"_id": {"$in": booking_ids}},
            {"$set": {"payout_settled": True, "payout_batch_id": transfer_id}},
        )
    if registration_ids:
        db_main.tournament_registrations.update_many(
            {"_id": {"$in": registration_ids}},
            {"$set": {"payout_settled": True, "payout_batch_id": transfer_id}},
        )

    db_main.payout_batches.insert_one({
        "cafe_id": str(cafe_id),
        "transfer_id": transfer_id,
        "amount": total_amount,
        "booking_count": len(booking_ids),
        "registration_count": len(registration_ids),
        "triggered_by": triggered_by,
        "created_at": datetime.now(timezone.utc),
        "cashfree_response": transfer_result,
    })

    return {"status": "success", "transfer_id": transfer_id, "amount": total_amount}
