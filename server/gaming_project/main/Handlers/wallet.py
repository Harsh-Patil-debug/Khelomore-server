# wallet.py
# Per-cafe wallet ("BC Coins") for BookMyConsole Gaming Hub.
#
# A customer tops up real money for ONE specific cafe; it converts 1:1 into BC Coins
# scoped to that (user_email, cafe_id) pair only — never transferable to another cafe,
# never withdrawable to cash. Those coins can later cover part or all of a booking's
# price at that same cafe (see bookings_handler.create_booking_handler's use_wallet
# param), with a real Cashfree payment covering any remainder.
#
# Reuses this codebase's existing payment primitives rather than duplicating them:
# get_cafe_cashfree_credentials/create_cashfree_order_handler/verify_cashfree_payment
# (payments.py) are the same functions bookings use, so a top-up's Cashfree order_id is
# claimed via the same unique-indexed used_cashfree_payments collection — one order_id
# can never fund both a top-up AND a booking, by construction, with no extra plumbing.

from datetime import datetime, timezone, timedelta
from bson import ObjectId
from pymongo import ReturnDocument
from .db_connection import db_main, get_client

IST = timezone(timedelta(hours=5, minutes=30))

MIN_TOPUP_INR = 10
MAX_TOPUP_INR = 50000

_wallet_indexes_ensured = False


def _ensure_wallet_indexes():
    """Lazily creates the indexes backing wallet lookups/atomicity. The unique compound
    index on wallet_balances is what makes the upsert in confirm_wallet_topup_handler
    safe for a customer's very first top-up at a cafe (no pre-existing balance doc)."""
    global _wallet_indexes_ensured
    if not _wallet_indexes_ensured:
        try:
            db_main.wallet_balances.create_index([("user_email", 1), ("cafe_id", 1)], unique=True)
            db_main.wallet_topup_orders.create_index("order_id", unique=True)
            db_main.wallet_transactions.create_index([("user_email", 1), ("cafe_id", 1), ("created_at", -1)])
            db_main.wallet_transactions.create_index("cafe_id")
        except Exception:
            pass
        _wallet_indexes_ensured = True


def _serialize_txn(t):
    created_at = t.get("created_at")
    return {
        "id": str(t["_id"]),
        "type": t.get("type"),
        "amount": int(t.get("amount", 0)),
        "balanceAfter": int(t.get("balance_after", 0)),
        "createdAt": created_at.isoformat() if isinstance(created_at, datetime) else created_at,
    }


def get_wallet_balance_handler(user_email: str, cafe_id: str):
    _ensure_wallet_indexes()
    doc = db_main.wallet_balances.find_one({"user_email": user_email, "cafe_id": str(cafe_id)})
    return {"status": "success", "balance": int(doc["balance"]) if doc else 0}, 200


def get_wallet_transactions_handler(user_email: str, cafe_id: str):
    _ensure_wallet_indexes()
    cursor = (
        db_main.wallet_transactions.find({"user_email": user_email, "cafe_id": str(cafe_id)})
        .sort("created_at", -1)
        .limit(100)
    )
    return {"status": "success", "transactions": [_serialize_txn(t) for t in cursor]}, 200


def create_wallet_topup_order_handler(user_email: str, cafe_id: str, amount_in_inr):
    """
    Creates a real Cashfree order for a wallet top-up, routed through the SAME
    cafe-vs-platform credential resolution booking payments use — a cafe's wallet money
    settles the same way its booking money does, since it's ultimately an obligation to
    be redeemed at that cafe. amount_inr is pinned onto the wallet_topup_orders record
    here and is what confirm_wallet_topup_handler verifies against later — the client
    never gets to supply/override the credited amount at confirm time.
    """
    _ensure_wallet_indexes()
    if not ObjectId.is_valid(str(cafe_id)):
        return {"status": "error", "message": "Invalid cafe id."}, 400
    cafe = db_main.cafes.find_one({"_id": ObjectId(cafe_id)})
    if not cafe:
        return {"status": "error", "message": "Cafe not found."}, 404

    try:
        amount = round(float(amount_in_inr), 2)
    except (TypeError, ValueError):
        return {"status": "error", "message": "Invalid amount."}, 400
    if amount < MIN_TOPUP_INR or amount > MAX_TOPUP_INR:
        return {
            "status": "error",
            "message": f"Top-up amount must be between ₹{MIN_TOPUP_INR} and ₹{MAX_TOPUP_INR}.",
        }, 400

    from .auth_handler import decrypt_phone_field
    user_doc = db_main.users.find_one({"email": user_email}, {"phone": 1})
    phone_raw = user_doc.get("phone") if user_doc else None
    customer_phone = decrypt_phone_field(phone_raw) if phone_raw else None

    from .payments import get_cafe_cashfree_credentials, create_cashfree_order_handler
    client_id, client_secret, used_platform_fallback = get_cafe_cashfree_credentials(cafe)
    order = create_cashfree_order_handler(amount, user_email, customer_phone, client_id, client_secret)

    if not order.get("order_id"):
        return {"status": "error", "message": "Failed to create payment order."}, 502

    if not order.get("is_mock"):
        db_main.wallet_topup_orders.insert_one({
            "order_id": order["order_id"],
            "cafe_id": str(cafe_id),
            "user_email": user_email,
            "amount_inr": amount,
            "used_platform_fallback": used_platform_fallback,
            "status": "pending",
            "created_at": datetime.now(IST),
            "completed_at": None,
        })
        # verify_cashfree_payment(cafe_id=...) resolves which Cashfree account created an
        # order (and that it belongs to this cafe) from cafe_payment_orders — without a
        # record here, verification always fails. No hold_* fields: a top-up holds no slot.
        db_main.cafe_payment_orders.insert_one({
            "order_id": order["order_id"],
            "cafe_id": str(cafe_id),
            "used_platform_fallback": used_platform_fallback,
            "purpose": "wallet_topup",
            "hold_token": None,
        })

    order["used_platform_fallback"] = used_platform_fallback
    return {"status": "success", **order}, 200


def confirm_wallet_topup_handler(user_email: str, cafe_id: str, order_id: str):
    """
    Verifies the Cashfree order server-side (via the exact same verify_cashfree_payment
    bookings use) and, only on real confirmed payment, atomically credits the wallet and
    appends a ledger row in one transaction. Idempotent: calling this twice for an
    already-completed order returns the current balance instead of double-crediting.
    """
    _ensure_wallet_indexes()
    if not isinstance(order_id, str) or not order_id:
        return {"status": "error", "message": "Missing order id."}, 400

    order_record = db_main.wallet_topup_orders.find_one({
        "order_id": order_id, "cafe_id": str(cafe_id), "user_email": user_email,
    })
    if not order_record:
        return {"status": "error", "message": "No matching top-up order found."}, 404

    if order_record.get("status") == "completed":
        balance_doc = db_main.wallet_balances.find_one({"user_email": user_email, "cafe_id": str(cafe_id)})
        return {"status": "success", "balance": int(balance_doc["balance"]) if balance_doc else 0}, 200

    from .payments import verify_cashfree_payment
    amount_inr = order_record["amount_inr"]
    if not verify_cashfree_payment(order_id, amount_inr * 100, cafe_id=str(cafe_id)):
        return {"status": "error", "message": "Payment verification failed. Please complete payment before confirming."}, 402

    client = get_client()
    if client is None:
        return {"status": "error", "message": "Database connection unavailable."}, 500

    now = datetime.now(IST)
    try:
        with client.start_session() as session:
            with session.start_transaction():
                updated = db_main.wallet_balances.find_one_and_update(
                    {"user_email": user_email, "cafe_id": str(cafe_id)},
                    {
                        "$inc": {"balance": amount_inr},
                        "$set": {"updated_at": now},
                        "$setOnInsert": {"created_at": now},
                    },
                    upsert=True,
                    return_document=ReturnDocument.AFTER,
                    session=session,
                )
                db_main.wallet_transactions.insert_one({
                    "user_email": user_email,
                    "cafe_id": str(cafe_id),
                    "type": "topup",
                    "amount": amount_inr,
                    "balance_after": updated["balance"],
                    "order_id": order_id,
                    "booking_ids": None,
                    "settlement": "platform_pending_payout" if order_record.get("used_platform_fallback") else "direct_to_cafe",
                    "created_at": now,
                }, session=session)
                db_main.wallet_topup_orders.update_one(
                    {"_id": order_record["_id"]},
                    {"$set": {"status": "completed", "completed_at": now}},
                    session=session,
                )
    except Exception as e:
        return {"status": "error", "message": f"Failed to credit wallet: {e}"}, 500

    return {"status": "success", "balance": int(updated["balance"])}, 200


def get_my_wallets_handler(user_email: str):
    """For the mobile app's Profile → 'My Wallets' list: every cafe where this customer
    currently holds a positive BC Coin balance, with the cafe's name/thumbnail for
    display. Zero-balance wallets (e.g. fully spent) are omitted — nothing useful to show."""
    _ensure_wallet_indexes()
    balances = list(
        db_main.wallet_balances.find({"user_email": user_email, "balance": {"$gt": 0}}).sort("balance", -1)
    )
    if not balances:
        return {"status": "success", "wallets": []}, 200

    cafe_ids = [ObjectId(b["cafe_id"]) for b in balances if ObjectId.is_valid(b["cafe_id"])]
    cafes_by_id = {
        str(c["_id"]): c
        for c in db_main.cafes.find({"_id": {"$in": cafe_ids}}, {"name": 1, "images": 1, "photos": 1})
    }

    wallets = []
    for b in balances:
        cafe = cafes_by_id.get(b["cafe_id"])
        images = (cafe.get("images") or cafe.get("photos") or []) if cafe else []
        wallets.append({
            "cafeId": b["cafe_id"],
            "cafeName": cafe.get("name") if cafe else "Unknown cafe",
            "cafeImage": images[0] if images else None,
            "balance": int(b["balance"]),
        })
    return {"status": "success", "wallets": wallets}, 200


def get_my_wallet_transactions_handler(user_email: str, kind: str = "all"):
    """Recent BC Coin activity across EVERY cafe for the mobile Wallet page's 'Recent
    Transactions' list. kind: 'all' | 'topup' (Added) | 'spend' (Spent)."""
    _ensure_wallet_indexes()
    query = {"user_email": user_email}
    if kind in ("topup", "spend"):
        query["type"] = kind
    txns = list(db_main.wallet_transactions.find(query).sort("created_at", -1).limit(100))

    cafe_ids = list({ObjectId(t["cafe_id"]) for t in txns if ObjectId.is_valid(t["cafe_id"])})
    names = {str(c["_id"]): c.get("name") for c in db_main.cafes.find({"_id": {"$in": cafe_ids}}, {"name": 1})}

    out = []
    for t in txns:
        item = _serialize_txn(t)
        item["cafeId"] = t["cafe_id"]
        item["cafeName"] = names.get(t["cafe_id"]) or "Unknown cafe"
        out.append(item)
    return {"status": "success", "transactions": out}, 200


def reconcile_pending_topups_handler(user_email: str, cafe_id: str):
    """Credits any of this customer's recent still-pending top-ups at this cafe that
    Cashfree actually shows as PAID — covers the app being killed/backgrounded after
    paying but before the confirm call landed. Safe to call repeatedly: an unpaid order
    just fails verification without side effects (verify only claims an order on
    success), and confirm_wallet_topup_handler is idempotent."""
    _ensure_wallet_indexes()
    since = datetime.now(IST) - timedelta(hours=24)
    pending = list(
        db_main.wallet_topup_orders.find(
            {"user_email": user_email, "cafe_id": str(cafe_id), "status": "pending", "created_at": {"$gte": since}}
        ).sort("created_at", -1).limit(5)
    )
    credited = 0
    for o in pending:
        resp, code = confirm_wallet_topup_handler(user_email, cafe_id, o["order_id"])
        if code == 200 and resp.get("status") == "success":
            credited += 1
    balance_resp, _ = get_wallet_balance_handler(user_email, cafe_id)
    return {"status": "success", "credited": credited, "balance": balance_resp["balance"]}, 200


def get_cafe_wallet_summary_handler(cafe_id: str):
    """For the cafe-owner dashboard: total outstanding wallet liability (money already
    collected but not yet redeemed — a real accounting obligation), how many customers
    hold a balance, this month's top-up volume, and the largest individual balances."""
    _ensure_wallet_indexes()
    cafe_id = str(cafe_id)

    agg = list(db_main.wallet_balances.aggregate([
        {"$match": {"cafe_id": cafe_id}},
        {"$group": {"_id": None, "total": {"$sum": "$balance"}, "count": {"$sum": 1}}},
    ]))
    total_liability = int(agg[0]["total"]) if agg else 0
    customer_count = int(agg[0]["count"]) if agg else 0

    now = datetime.now(IST)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    topup_agg = list(db_main.wallet_transactions.aggregate([
        {"$match": {"cafe_id": cafe_id, "type": "topup", "created_at": {"$gte": month_start}}},
        {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
    ]))
    topup_this_month = int(topup_agg[0]["total"]) if topup_agg else 0

    top_balances = db_main.wallet_balances.find({"cafe_id": cafe_id}).sort("balance", -1).limit(50)
    balances = [{"userEmail": b["user_email"], "balance": int(b["balance"])} for b in top_balances]

    return {
        "status": "success",
        "totalLiability": total_liability,
        "customerCount": customer_count,
        "topupThisMonth": topup_this_month,
        "balances": balances,
    }, 200
