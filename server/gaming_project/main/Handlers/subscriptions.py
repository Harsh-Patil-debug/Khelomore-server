# subscriptions.py
# Handlers for the cafe-owner platform subscription: a flat ₹1599/month fee, paid manually
# each cycle via a Razorpay order (not an auto-recurring mandate) — see the "Monthly Pay
# Now order" decision. A brand-new cafe gets a SUBSCRIPTION_TRIAL_DAYS free trial before
# its first charge (existing cafes, already on the old ₹1500 rate, are never retroactively
# changed — see _ensure_defaults). A cafe more than SUBSCRIPTION_GRACE_DAYS past its due
# date is hidden from the public cafe listing (gaming-cafe-connect + mobile) until paid;
# the owner's own cafe-command-center dashboard stays fully usable throughout, so they can
# always pay.

import calendar
from datetime import datetime, timedelta, timezone
from bson import ObjectId
from .db_connection import get_db
from . import payments

IST = timezone(timedelta(hours=5, minutes=30))

SUBSCRIPTION_MONTHLY_AMOUNT = 1599
SUBSCRIPTION_GRACE_DAYS = 7
SUBSCRIPTION_TRIAL_DAYS = 15


def _safe_oid(id_str):
    try:
        return ObjectId(id_str)
    except Exception:
        return None


def _to_ist_iso(dt) -> str | None:
    """
    Mongo/pymongo always returns naive datetimes on read (it stores everything as UTC
    internally, but drops the tzinfo when handing it back to Python) — calling
    .isoformat() directly on that produces a string with NO timezone suffix
    (e.g. "2026-07-17T18:55:53"), which a browser's `new Date(...)` then parses as LOCAL
    time instead of UTC. For someone in IST that silently shifts the displayed time
    backward by 5:30 — enough to land on the wrong calendar date entirely for anything
    that happened in the last ~5.5 hours of a day. Every datetime read from Mongo must be
    explicitly re-tagged as UTC and converted to IST before serializing.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST).isoformat()


def _add_one_month(dt: datetime) -> datetime:
    """
    Adds exactly one CALENDAR month, not a flat 30 days. A flat 30-day increment drifts
    against real months (31/30/28/29 days) — Jan 31 + 30 days lands on Mar 2, not the
    "one month later" a cafe owner would expect, and over a year that drift adds up to
    roughly a 13th billing cycle squeezed into 12 real months (365 days / 30 ≈ 12.2
    cycles), effectively overcharging every subscriber a little more each year.

    Clamps the day to the target month's last valid day — Jan 31 -> Feb 28 (or 29 in a
    leap year), not an invalid "Feb 31" or an overflow into March — the same convention
    real billing systems (Stripe, etc.) use for month-end anchor dates.
    """
    month = dt.month + 1
    year = dt.year + (month - 1) // 12
    month = ((month - 1) % 12) + 1
    last_day_of_target_month = calendar.monthrange(year, month)[1]
    day = min(dt.day, last_day_of_target_month)
    return dt.replace(year=year, month=month, day=day)


def _compute_status(due_date: datetime, grace_until: datetime, now: datetime, trial_end: datetime | None = None) -> str:
    # A cafe still inside its free trial window reports "trial", not "active" — distinct
    # labels even though due_date == trial_end for a brand-new cafe (see _ensure_defaults),
    # so the frontend can show trial-specific copy ("free trial ends in X days") instead of
    # implying a real subscription is already running.
    if trial_end is not None and now <= trial_end:
        return "trial"
    if now <= due_date:
        return "active"
    if now <= grace_until:
        return "overdue"
    return "suspended"


def _ensure_defaults(db, cafe_doc: dict) -> dict:
    """
    Lazily backfills subscription fields on a cafe that predates this feature, and
    refreshes the cached status label — nothing runs on a schedule to do this, so it's
    recomputed whenever a cafe's subscription is actually looked at (matches this
    codebase's existing lazy-init conventions, e.g. rig/tournament auto-seeding).

    A cafe that has NEVER had subscription fields gets a SUBSCRIPTION_TRIAL_DAYS free
    trial starting now (not backdated to its creation date) — its due date is the trial's
    end, so nothing is charged until the trial actually runs out. The trial itself gets
    NO grace period: unlike a normal paid cycle, an unpaid trial goes straight to
    "suspended" the moment it ends, with no further overdue buffer (see grace_until
    below) — once the owner pays even once, every cycle after that gets the normal
    SUBSCRIPTION_GRACE_DAYS grace period via _extend_subscription. A cafe that already
    had subscription fields before this trial feature shipped is untouched here — it
    keeps its existing amount/cadence (see subscription_amount below) and never
    retroactively gets a trial_end, so _compute_status can never accidentally show it as
    "trial".
    """
    now = datetime.now(IST)
    update = {}

    due_date = cafe_doc.get("subscription_due_date")
    trial_end = cafe_doc.get("subscription_trial_end")
    if due_date is None:
        trial_end = now + timedelta(days=SUBSCRIPTION_TRIAL_DAYS)
        due_date = trial_end
        update["subscription_due_date"] = due_date
        update["subscription_trial_end"] = trial_end
    elif due_date.tzinfo is None:
        due_date = due_date.replace(tzinfo=timezone.utc).astimezone(IST)
    # Mongo/pymongo always returns naive UTC datetimes on read, regardless of what was
    # written — cafe_doc must hold the normalized, tz-aware value from here on, even when
    # no DB write was needed this call, or _to_view's later arithmetic breaks.
    cafe_doc["subscription_due_date"] = due_date

    if trial_end is not None and trial_end.tzinfo is None:
        trial_end = trial_end.replace(tzinfo=timezone.utc).astimezone(IST)
    cafe_doc["subscription_trial_end"] = trial_end

    is_fresh_trial = "subscription_due_date" in update
    grace_until = cafe_doc.get("subscription_grace_until")
    if grace_until is None or is_fresh_trial:
        # Zero grace days for a brand-new trial (see docstring); a cafe that predates
        # this feature and is only missing subscription_grace_until for some other
        # reason (not a fresh trial) keeps the normal grace period, matching this
        # backfill's original behavior.
        grace_days = 0 if is_fresh_trial else SUBSCRIPTION_GRACE_DAYS
        grace_until = due_date + timedelta(days=grace_days)
        update["subscription_grace_until"] = grace_until
    elif grace_until.tzinfo is None:
        grace_until = grace_until.replace(tzinfo=timezone.utc).astimezone(IST)
    cafe_doc["subscription_grace_until"] = grace_until

    if cafe_doc.get("subscription_amount") is None:
        update["subscription_amount"] = SUBSCRIPTION_MONTHLY_AMOUNT

    status = _compute_status(due_date, grace_until, now, trial_end)
    if cafe_doc.get("subscription_status") != status:
        update["subscription_status"] = status

    if update:
        db.cafes.update_one({"_id": cafe_doc["_id"]}, {"$set": update})
        cafe_doc.update(update)

    return cafe_doc


def _to_view(cafe_doc: dict) -> dict:
    now = datetime.now(IST)
    due_date = cafe_doc["subscription_due_date"]
    grace_until = cafe_doc["subscription_grace_until"]
    days_remaining = int((due_date - now).total_seconds() // 86400)
    return {
        "amount": cafe_doc.get("subscription_amount", SUBSCRIPTION_MONTHLY_AMOUNT),
        "status": cafe_doc.get("subscription_status", "active"),
        "due_date": _to_ist_iso(due_date),
        "grace_until": _to_ist_iso(grace_until),
        "trial_end": _to_ist_iso(cafe_doc.get("subscription_trial_end")),
        "trial_welcome_shown": bool(cafe_doc.get("subscription_trial_welcome_shown")),
        "days_remaining": days_remaining,
    }


def get_cafe_subscription_handler(cafe_id: str):
    db = get_db()
    if db is None:
        return {"status": "error", "message": "MongoDB connection is not established."}, 500

    oid = _safe_oid(cafe_id)
    if oid is None:
        return {"status": "error", "message": "Invalid cafe ID."}, 400

    cafe = db.cafes.find_one({"_id": oid})
    if not cafe:
        return {"status": "error", "message": "Cafe not found."}, 404

    cafe = _ensure_defaults(db, cafe)
    view = _to_view(cafe)

    payments_cursor = db.subscription_payments.find({"cafe_id": cafe_id}).sort("paid_at", -1)
    history = [{
        "id": str(p["_id"]),
        "amount": p.get("amount"),
        "method": p.get("method"),
        "paid_at": _to_ist_iso(p.get("paid_at")),
        "period_end": _to_ist_iso(p.get("period_end")),
    } for p in payments_cursor]

    return {"status": "success", "subscription": view, "history": history}, 200


def mark_trial_welcome_shown_handler(cafe_id: str):
    """
    One-shot flag so the "welcome to your 15-day free trial" popup in
    cafe-command-center only ever fires once, the first time a brand-new owner logs in
    — not on every subsequent page load/login for the rest of the trial. Idempotent:
    calling it again once already set is a harmless no-op.
    """
    db = get_db()
    if db is None:
        return {"status": "error", "message": "MongoDB connection is not established."}, 500

    oid = _safe_oid(cafe_id)
    if oid is None:
        return {"status": "error", "message": "Invalid cafe ID."}, 400

    result = db.cafes.update_one({"_id": oid}, {"$set": {"subscription_trial_welcome_shown": True}})
    if result.matched_count == 0:
        return {"status": "error", "message": "Cafe not found."}, 404

    return {"status": "success"}, 200


def create_subscription_order_handler(cafe_id: str):
    db = get_db()
    if db is None:
        return {"status": "error", "message": "MongoDB connection is not established."}, 500

    oid = _safe_oid(cafe_id)
    if oid is None:
        return {"status": "error", "message": "Invalid cafe ID."}, 400

    cafe = db.cafes.find_one({"_id": oid})
    if not cafe:
        return {"status": "error", "message": "Cafe not found."}, 404

    cafe = _ensure_defaults(db, cafe)
    order = payments.create_razorpay_order_handler(cafe.get("subscription_amount", SUBSCRIPTION_MONTHLY_AMOUNT))
    from django.conf import settings
    return {
        "status": "success",
        "order": order,
        "key_id": getattr(settings, "RAZORPAY_KEY_ID", ""),
    }, 200


def _extend_subscription(db, cafe: dict, method: str, marked_by: str = "", razorpay_order_id: str = "", razorpay_payment_id: str = ""):
    now = datetime.now(IST)
    current_due = cafe.get("subscription_due_date") or now
    if current_due.tzinfo is None:
        current_due = current_due.replace(tzinfo=timezone.utc).astimezone(IST)

    # Extend from whichever is later: the current due date (on-time/early payment keeps
    # the existing cadence) or today (a late payment doesn't get to "stack up" free days).
    period_start = max(current_due, now)
    new_due_date = _add_one_month(period_start)
    new_grace_until = new_due_date + timedelta(days=SUBSCRIPTION_GRACE_DAYS)
    amount = cafe.get("subscription_amount", SUBSCRIPTION_MONTHLY_AMOUNT)

    db.cafes.update_one(
        {"_id": cafe["_id"]},
        {
            "$set": {
                "subscription_due_date": new_due_date,
                "subscription_grace_until": new_grace_until,
                "subscription_status": "active",
                "subscription_amount": amount,
            },
            # Any real payment (whether it converts an active trial early or renews a
            # normal cycle) permanently retires the trial marker — without this, a cafe
            # that paid during its trial would still show "trial" status until the old
            # trial_end date passed, even though it's now a genuine paid subscriber.
            "$unset": {"subscription_trial_end": ""},
        },
    )

    db.subscription_payments.insert_one({
        "cafe_id": str(cafe["_id"]),
        "amount": amount,
        "method": method,
        "razorpay_order_id": razorpay_order_id,
        "razorpay_payment_id": razorpay_payment_id,
        "marked_by": marked_by,
        "paid_at": now,
        "period_start": period_start,
        "period_end": new_due_date,
    })

    cafe["subscription_due_date"] = new_due_date
    cafe["subscription_grace_until"] = new_grace_until
    cafe["subscription_status"] = "active"
    cafe["subscription_amount"] = amount
    cafe.pop("subscription_trial_end", None)
    return cafe


def verify_subscription_payment_handler(cafe_id: str, razorpay_order_id: str, razorpay_payment_id: str, razorpay_signature: str):
    db = get_db()
    if db is None:
        return {"status": "error", "message": "MongoDB connection is not established."}, 500

    oid = _safe_oid(cafe_id)
    if oid is None:
        return {"status": "error", "message": "Invalid cafe ID."}, 400

    cafe = db.cafes.find_one({"_id": oid})
    if not cafe:
        return {"status": "error", "message": "Cafe not found."}, 404

    cafe = _ensure_defaults(db, cafe)
    amount = cafe.get("subscription_amount", SUBSCRIPTION_MONTHLY_AMOUNT)

    verified = payments.verify_razorpay_payment(
        razorpay_order_id, razorpay_payment_id, razorpay_signature, int(amount) * 100
    )
    if not verified:
        return {"status": "error", "message": "Payment verification failed."}, 402

    cafe = _extend_subscription(
        db, cafe, method="razorpay",
        razorpay_order_id=razorpay_order_id, razorpay_payment_id=razorpay_payment_id,
    )
    return {"status": "success", "subscription": _to_view(cafe)}, 200


def mark_subscription_paid_manually_handler(cafe_id: str, admin_email: str):
    """Super-admin-only override for a payment collected outside the app (cash/UPI-direct
    to the platform, a goodwill free month, etc) — same trust-based pattern already used
    for cash bookings, just for subscriptions."""
    db = get_db()
    if db is None:
        return {"status": "error", "message": "MongoDB connection is not established."}, 500

    oid = _safe_oid(cafe_id)
    if oid is None:
        return {"status": "error", "message": "Invalid cafe ID."}, 400

    cafe = db.cafes.find_one({"_id": oid})
    if not cafe:
        return {"status": "error", "message": "Cafe not found."}, 404

    cafe = _ensure_defaults(db, cafe)
    cafe = _extend_subscription(db, cafe, method="manual", marked_by=admin_email)
    return {"status": "success", "subscription": _to_view(cafe)}, 200


def get_all_subscriptions_handler():
    """Super-admin-only: every non-deleted cafe's subscription status, for the platform's
    Subscriptions overview page."""
    db = get_db()
    if db is None:
        return {"status": "error", "message": "MongoDB connection is not established."}, 500

    docs = list(db.cafes.find({"is_deleted": {"$ne": True}}))
    rows = []
    for cafe in docs:
        cafe = _ensure_defaults(db, cafe)
        view = _to_view(cafe)
        rows.append({
            "cafe_id": str(cafe["_id"]),
            "cafe_name": cafe.get("name", ""),
            "city": cafe.get("city", ""),
            "owner_email": cafe.get("owner_email", ""),
            **view,
        })
    rows.sort(key=lambda r: r["due_date"])
    return {"status": "success", "subscriptions": rows}, 200
