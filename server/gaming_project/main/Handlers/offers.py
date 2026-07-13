# offers.py
# Handlers for managing cafe offers/promotions in KheloMore Gaming Hub

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
        "type": doc.get("type", "custom"),
        "discount_pct": int(doc.get("discount_pct", 0)),
        "start_date": doc.get("start_date", ""),
        "end_date": doc.get("end_date", ""),
        "is_active": doc.get("is_active", True),
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


def create_offer_handler(data):
    """Creates a new offer in MongoDB."""
    db = get_db()
    if db is None:
        return {"status": "error", "message": "DB not connected."}, 500
    try:
        cafe_id = data.get("cafe_id") or data.get("cafeId")
        name = data.get("name", "").strip()
        discount_pct = int(data.get("discount_pct", 0) or data.get("discountPct", 0))
        start_date = data.get("start_date") or data.get("startDate", "")
        end_date = data.get("end_date") or data.get("endDate", "")
        offer_type = data.get("type", "custom")
        is_active = str(data.get("is_active", "true")).lower() != "false"

        if not name:
            return {"status": "error", "message": "Offer name is required."}, 400
        if not cafe_id:
            return {"status": "error", "message": "cafe_id is required."}, 400
        if discount_pct <= 0 or discount_pct > 90:
            return {"status": "error", "message": "Discount must be between 1 and 90."}, 400
        if not start_date or not end_date:
            return {"status": "error", "message": "start_date and end_date are required."}, 400

        from .bookings_handler import IST
        doc = {
            "cafe_id": cafe_id,
            "name": name,
            "type": offer_type,
            "discount_pct": discount_pct,
            "start_date": start_date,
            "end_date": end_date,
            "is_active": is_active,
            "created_at": datetime.now(IST).isoformat(),
        }
        result = db.offers.insert_one(doc)
        doc["_id"] = result.inserted_id
        print(f"[Offers] Created offer '{name}' ({discount_pct}%) for cafe {cafe_id}")
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
        if "name" in data:
            update["name"] = data["name"]
        if "discount_pct" in data:
            update["discount_pct"] = int(data["discount_pct"])
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
