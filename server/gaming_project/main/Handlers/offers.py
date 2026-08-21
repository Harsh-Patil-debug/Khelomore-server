# offers.py
# Handlers for managing cafe offers/promotions in BookMyConsole Gaming Hub

import math
from datetime import datetime, timezone, date
import traceback
from bson import ObjectId
from .db_connection import get_db


def _safe_oid(id_str):
    try:
        return ObjectId(id_str)
    except Exception:
        return id_str


def _map_offer(doc):
    return {
        "id": str(doc["_id"]),
        "cafe_id": doc.get("cafe_id", ""),
        "name": doc.get("name", ""),
        # "type" is the marketing category (Happy Hour, Weekend, ...) used for display
        # grouping/coloring in the admin UI — separate from "pricing_mode", which is the
        # actual pricing MECHANISM. Kept apart deliberately so adding pricing_mode never
        # touches the meaning of existing offers' type values.
        "type": doc.get("type", "custom"),
        "pricing_mode": doc.get("pricing_mode", "percentage"),
        "discount_pct": int(doc.get("discount_pct", 0) or 0),
        # Only meaningful when pricing_mode == "bulk" — book at least min_hours slots in
        # one booking and pay bundle_price flat for those, with any additional slots
        # beyond min_hours charged at the cafe's normal per-hour rate. 0 otherwise.
        "min_hours": int(doc.get("min_hours", 0) or 0),
        "bundle_price": int(doc.get("bundle_price", 0) or 0),
        # Empty string = auto-applied (no customer action needed, already factored into
        # the price shown everywhere). Non-empty = a coupon: shown in a checkout coupon
        # list and only ever takes effect once the customer taps Apply and it's verified
        # server-side, never applied automatically.
        "code": doc.get("code", ""),
        "start_date": doc.get("start_date", ""),
        "end_date": doc.get("end_date", ""),
        "is_active": doc.get("is_active", True),
        "is_deleted": doc.get("is_deleted", False),
        "created_at": doc.get("created_at", ""),
    }


def get_offers_handler(cafe_id=None):
    """Returns all offers for a cafe (admin view — includes inactive)."""
    db = get_db()
    if db is None:
        return {"status": "error", "message": "DB not connected."}, 500
    try:
        query = {}
        if cafe_id:
            query["cafe_id"] = cafe_id
        docs = list(db.offers.find(query).sort("created_at", -1))
        return {"status": "success", "offers": [_map_offer(d) for d in docs]}, 200
    except Exception as e:
        print(f"[Offers] get_offers_handler error: {e}")
        return {"status": "error", "message": str(e)}, 500


def get_active_offers_handler(cafe_id=None):
    """
    Public endpoint — returns currently active offers for a cafe.
    Active = is_active=True AND today is within [start_date, end_date].
    """
    db = get_db()
    if db is None:
        return {"status": "error", "message": "DB not connected."}, 500
    try:
        from .bookings_handler import IST
        now_ist = datetime.now(IST)
        today_str = now_ist.strftime("%Y-%m-%d")   # e.g. "2026-07-02"
        query = {
            "is_active": True,
            "is_deleted": {"$ne": True},
            "start_date": {"$lte": today_str},
            "end_date":   {"$gte": today_str},
        }
        if cafe_id:
            query["cafe_id"] = cafe_id
        docs = list(db.offers.find(query))
        return {"status": "success", "offers": [_map_offer(d) for d in docs]}, 200
    except Exception as e:
        print(f"[Offers] get_active_offers_handler error: {e}")
        return {"status": "error", "message": str(e)}, 500


def _price_for_offer(offer, hourly_price, num_slots):
    """
    Returns the total price num_slots would cost under this one offer, or None if the
    offer doesn't apply (e.g. a bulk offer whose min_hours threshold isn't met, or a
    percentage offer with no real discount). Never returns more than the caller would
    pay at the normal rate — callers additionally cap against normal_total themselves.
    """
    mode = offer.get("pricing_mode", "percentage")
    if mode == "bulk":
        min_hours = int(offer.get("min_hours", 0) or 0)
        bundle_price = int(offer.get("bundle_price", 0) or 0)
        if min_hours <= 0 or bundle_price <= 0 or num_slots < min_hours:
            return None
        extra_slots = num_slots - min_hours
        return int(bundle_price + extra_slots * hourly_price)

    pct = int(offer.get("discount_pct", 0) or 0)
    if pct <= 0:
        return None
    # math.floor(x + 0.5) matches JS's Math.round (round-half-up) exactly, unlike
    # Python's built-in round() (banker's rounding) — the mobile client computes its
    # displayed/charged price with Math.round, so this must agree with it on every
    # value, including exact .5 cases, or verify_cashfree_payment's amount check
    # silently diverges by ₹1 and every booking under this offer fails verification.
    per_hour = math.floor(hourly_price * (1 - pct / 100) + 0.5)
    return int(per_hour * num_slots)


def compute_best_price(cafe_id, hourly_price, num_slots, coupon_code=None, offer_id=None):
    """
    THE server-side source of truth for what a booking of num_slots hours at this cafe
    actually costs right now — used identically by the checkout offer-preview endpoint
    and by create_booking_handler's final payment verification, so a customer can never
    be shown/charged one price and have the booking verified against a different one.
    Never trust a client-supplied price or discount for this — every active offer is
    looked up fresh here, same as _resolve_hourly_price does for the base rate.

    Returns (total_price: int, applied_offer_id: str | None, error: str | None).

    NOTHING auto-applies. A customer must explicitly tap Apply on one specific offer in
    the checkout list — identified by offer_id (works for any offer, coded or not) or by
    coupon_code (for a coded offer, matched case-insensitively) — and that selection is
    what gets priced and, later, verified against the actual payment. Omitting both means
    the customer didn't apply anything, and the plain normal_total is what's charged; it
    is never silently swapped for a "best" discount the customer didn't choose.
    """
    normal_total = int(round(hourly_price)) * int(num_slots)
    db = get_db()
    if db is None or not cafe_id:
        return normal_total, None, None

    if not coupon_code and not offer_id:
        return normal_total, None, None

    try:
        from .bookings_handler import IST
        today_str = datetime.now(IST).strftime("%Y-%m-%d")
        active_offers = list(db.offers.find({
            "cafe_id": cafe_id,
            "is_active": True,
            "is_deleted": {"$ne": True},
            "start_date": {"$lte": today_str},
            "end_date": {"$gte": today_str},
        }))

        if offer_id:
            match = next((o for o in active_offers if str(o["_id"]) == str(offer_id)), None)
            if not match:
                return None, None, "This offer is no longer available."
        else:
            normalized = str(coupon_code).strip().upper()
            match = next(
                (o for o in active_offers if (o.get("code") or "").strip().upper() == normalized),
                None,
            )
            if not match:
                return None, None, "Invalid or expired coupon code."

        price = _price_for_offer(match, hourly_price, num_slots)
        if price is None:
            return None, None, "This offer isn't valid for this booking (check the minimum hours required)."
        return min(price, normal_total), str(match["_id"]), None
    except Exception as e:
        print(f"[Offers] compute_best_price error: {e}")
        return normal_total, None, None


def create_offer_handler(data):
    """Creates a new offer in MongoDB."""
    db = get_db()
    if db is None:
        return {"status": "error", "message": "DB not connected."}, 500
    try:
        cafe_id = data.get("cafe_id") or data.get("cafeId")
        name = data.get("name", "").strip()
        start_date = data.get("start_date") or data.get("startDate", "")
        end_date = data.get("end_date") or data.get("endDate", "")
        offer_type = data.get("type", "custom")
        pricing_mode = data.get("pricing_mode", "percentage")
        is_active = str(data.get("is_active", "true")).lower() != "false"
        code = str(data.get("code", "") or "").strip().upper()

        if not name:
            return {"status": "error", "message": "Offer name is required."}, 400
        if len(name) > 100:
            return {"status": "error", "message": "Offer name must be at most 100 characters."}, 400
        if not cafe_id:
            return {"status": "error", "message": "cafe_id is required."}, 400
        if pricing_mode not in ("percentage", "bulk"):
            return {"status": "error", "message": "Invalid pricing mode."}, 400
        if not start_date or not end_date:
            return {"status": "error", "message": "start_date and end_date are required."}, 400
        # start_date/end_date are ISO "YYYY-MM-DD" strings, so a plain string comparison is
        # correct for ordering — no need to parse them into real dates for this check.
        if end_date < start_date:
            return {"status": "error", "message": "End date must be on or after the start date."}, 400
        if code:
            if not (3 <= len(code) <= 20) or not code.replace("_", "").isalnum():
                return {"status": "error", "message": "Coupon code must be 3-20 alphanumeric characters."}, 400
            # Scoped to this cafe only — the same code text on a DIFFERENT cafe is fine,
            # compute_best_price always looks a code up within one cafe_id.
            if db.offers.find_one({"cafe_id": cafe_id, "code": code, "is_deleted": {"$ne": True}}):
                return {"status": "error", "message": f"Coupon code '{code}' is already in use for this cafe."}, 400

        discount_pct = 0
        min_hours = 0
        bundle_price = 0
        if pricing_mode == "bulk":
            min_hours = int(data.get("min_hours", 0) or 0)
            bundle_price = int(data.get("bundle_price", 0) or 0)
            if min_hours < 1 or min_hours > 24:
                return {"status": "error", "message": "Minimum hours must be between 1 and 24."}, 400
            if bundle_price <= 0:
                return {"status": "error", "message": "Bundle price must be greater than 0."}, 400
            # Sanity check against the cafe's real rate — catches an admin typo that
            # would otherwise silently overcharge (or give away hours for free) rather
            # than genuinely discount anything.
            cafe = db.cafes.find_one({"_id": ObjectId(cafe_id)}) if ObjectId.is_valid(str(cafe_id)) else None
            cafe_hourly = (cafe or {}).get("price_per_hour") or 150
            if bundle_price >= min_hours * cafe_hourly:
                return {
                    "status": "error",
                    "message": f"Bundle price must be less than {min_hours} hours at the normal rate (₹{min_hours * cafe_hourly}).",
                }, 400
        else:
            discount_pct = int(data.get("discount_pct", 0) or data.get("discountPct", 0))
            if discount_pct <= 0 or discount_pct > 90:
                return {"status": "error", "message": "Discount must be between 1 and 90."}, 400

        from .bookings_handler import IST
        doc = {
            "cafe_id": cafe_id,
            "name": name,
            "type": offer_type,
            "pricing_mode": pricing_mode,
            "discount_pct": discount_pct,
            "min_hours": min_hours,
            "bundle_price": bundle_price,
            "code": code,
            "start_date": start_date,
            "end_date": end_date,
            "is_active": is_active,
            "is_deleted": False,
            "created_at": datetime.now(IST).isoformat(),
        }
        result = db.offers.insert_one(doc)
        doc["_id"] = result.inserted_id
        print(f"[Offers] Created {pricing_mode} offer '{name}' for cafe {cafe_id}")
        return {"status": "success", "offer": _map_offer(doc)}, 201
    except Exception as e:
        print(f"[Offers] create_offer_handler error: {e}")
        print(traceback.format_exc())
        return {"status": "error", "message": str(e)}, 500


def update_offer_handler(offer_id, data):
    """Toggles is_active or updates fields of an offer."""
    db = get_db()
    if db is None:
        return {"status": "error", "message": "DB not connected."}, 500
    try:
        oid = _safe_oid(offer_id)
        update = {}
        if "is_active" in data:
            val = data["is_active"]
            update["is_active"] = (val is True or str(val).lower() == "true")
        if "is_deleted" in data:
            val = data["is_deleted"]
            update["is_deleted"] = (val is True or str(val).lower() == "true")
        if "name" in data:
            update["name"] = data["name"]
        if "discount_pct" in data:
            update["discount_pct"] = int(data["discount_pct"])
        if "min_hours" in data:
            update["min_hours"] = int(data["min_hours"])
        if "bundle_price" in data:
            update["bundle_price"] = int(data["bundle_price"])
        if not update:
            return {"status": "error", "message": "Nothing to update."}, 400
        db.offers.update_one({"_id": oid}, {"$set": update})
        doc = db.offers.find_one({"_id": oid})
        if not doc:
            return {"status": "error", "message": "Offer not found."}, 404
        return {"status": "success", "offer": _map_offer(doc)}, 200
    except Exception as e:
        return {"status": "error", "message": str(e)}, 500


def delete_offer_handler(offer_id):
    """Deletes an offer by ID."""
    db = get_db()
    if db is None:
        return {"status": "error", "message": "DB not connected."}, 500
    try:
        oid = _safe_oid(offer_id)
        result = db.offers.delete_one({"_id": oid})
        if result.deleted_count == 0:
            return {"status": "error", "message": "Offer not found."}, 404
        return {"status": "success", "message": "Offer deleted."}, 200
    except Exception as e:
        return {"status": "error", "message": str(e)}, 500
