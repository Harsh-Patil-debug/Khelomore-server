# bookings.py
# Handlers for managing PC/Console station bookings in KheloMore Gaming Hub

import os
from datetime import datetime, timezone
from bson import ObjectId
from .db_connection import get_db

# Initial realistic bookings to seed when collection is empty
# Initial realistic bookings to seed when collection is empty
SEED_BOOKINGS = [
    {
        "cafe_id": "gear-up-nerul",
        "cafe_name": "Gear Up Gaming Nerul",
        "zone": "VIP Elite Zone",
        "date": "2026-06-25", # Today (default fallback)
        "slot": "06:00 PM - 08:00 PM",
        "price": 440,
        "code": "294810",
        "status": "Active",
        "rig": "PC #01 · RTX 4090 · 360Hz",
        "remaining_time_seconds": 4500,
        "user_name": "SHADOW_WRATH",
        "user_email": "shadow@khelomore.com"
    },
    {
        "cafe_id": "red-zone-nerul",
        "cafe_name": "Red Zone Gaming Cafe",
        "zone": "Regular Zone",
        "date": "2026-06-26", # Tomorrow
        "slot": "04:00 PM - 06:00 PM",
        "price": 360,
        "code": "834190",
        "status": "Upcoming",
        "rig": "PC #12 · RTX 4070 · 240Hz",
        "user_name": "SHADOW_WRATH",
        "user_email": "shadow@khelomore.com"
    },
    {
        "cafe_id": "vortex-nerul",
        "cafe_name": "Vortex Lounge Nerul",
        "zone": "Console Lounge",
        "date": "2026-06-20", # Past
        "slot": "02:00 PM - 04:00 PM",
        "price": 320,
        "code": "192843",
        "status": "Completed",
        "rig": "PS5 Pro #03 · DualSense Edge · 4K",
        "user_name": "SHADOW_WRATH",
        "user_email": "shadow@khelomore.com"
    }
]

def map_booking_doc(doc):
    """Maps a MongoDB booking document to the format expected by the frontend."""
    return {
        "id": str(doc["_id"]),
        "cafeId": doc.get("cafe_id", ""),
        "cafeName": doc.get("cafe_name", ""),
        "zone": doc.get("zone", ""),
        "date": doc.get("date", ""),
        "slot": doc.get("slot", ""),
        "price": int(doc.get("price", 0)) if doc.get("price") is not None else 0,
        "code": doc.get("code", ""),
        "status": doc.get("status", "Upcoming"),
        "rig": doc.get("rig", ""),
        "remainingTimeSeconds": int(doc.get("remaining_time_seconds")) if doc.get("remaining_time_seconds") is not None else None,
        "userName": doc.get("user_name", "GUEST_PLAYER"),
        "userEmail": doc.get("user_email", "")
    }

def get_bookings_handler(cafe_id=None):
    """Retrieves café bookings from the database. Seeds default list if empty. Optionally filters by cafe_id."""
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection is not established."}

    try:
        if db_main.bookings.count_documents({}) == 0:
            # Dynamically adjust seed booking date to match today's date
            today_str = datetime.now(timezone.utc).date().isoformat()
            
            # Use tomorrow's date for upcoming seed
            import datetime as dt
            tomorrow_str = (datetime.now(timezone.utc) + dt.timedelta(days=1)).date().isoformat()
            
            # Resolve dynamic ObjectIds for seed cafes to prevent mismatches
            gear_up = db_main.cafes.find_one({"name": "Gear Up Gaming Nerul"})
            red_zone = db_main.cafes.find_one({"name": "Red Zone Gaming Cafe"})
            vortex = db_main.cafes.find_one({"name": "Vortex Lounge Nerul"})
            
            gear_up_id = str(gear_up["_id"]) if gear_up else "6a3a339c659278e9486cc269"
            red_zone_id = str(red_zone["_id"]) if red_zone else "6a3a339c659278e9486cc268"
            vortex_id = str(vortex["_id"]) if vortex else "6a3a339c659278e9486cc26a"

            seeded = [dict(b) for b in SEED_BOOKINGS]
            seeded[0]["cafe_id"] = gear_up_id
            seeded[1]["cafe_id"] = red_zone_id
            seeded[2]["cafe_id"] = vortex_id
            
            seeded[0]["date"] = today_str
            seeded[1]["date"] = tomorrow_str
            
            db_main.bookings.insert_many(seeded)
            print(f"[KheloMore] Seeded {len(seeded)} initial game bookings in MongoDB.")

        # Build query — optionally filter by cafe_id
        query = {}
        if cafe_id:
            query["cafe_id"] = cafe_id

        docs = list(db_main.bookings.find(query))
        mapped = [map_booking_doc(d) for d in docs]

        return {"status": "success", "bookings": mapped}
    except Exception as e:
        return {"status": "error", "message": f"Failed to retrieve bookings: {e}"}


def create_booking_handler(data):
    """Creates a new Café booking in MongoDB."""
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection is not established."}

    try:
        cafe_id = data.get("cafeId")
        cafe_name = data.get("cafeName")
        zone = data.get("zone")
        slot = data.get("slot")
        date = data.get("date")
        code = data.get("code")
        price = data.get("price")
        rig = data.get("rig")
        remaining_time_seconds = data.get("remainingTimeSeconds")
        user_name = data.get("userName") or "GUEST_PLAYER"
        user_email = data.get("userEmail") or ""

        if not cafe_id or not slot or not date or not rig:
            return {
                "status": "error",
                "message": "Cafe ID, Slot, Date, and Rig are required fields."
            }

        # Dynamically evaluate status based on target date
        today_str = datetime.now(timezone.utc).date().isoformat()
        if date == today_str:
            status = "Active"
            if remaining_time_seconds is None:
                remaining_time_seconds = 7200 # default to 2 hours
        else:
            status = "Upcoming"

        booking_doc = {
            "cafe_id": cafe_id,
            "cafe_name": cafe_name,
            "zone": zone,
            "slot": slot,
            "date": date,
            "code": code,
            "price": int(price) if price is not None else 0,
            "rig": rig,
            "status": status,
            "remaining_time_seconds": int(remaining_time_seconds) if remaining_time_seconds is not None else None,
            "user_name": user_name,
            "user_email": user_email
        }

        result = db_main.bookings.insert_one(booking_doc)
        booking_doc["_id"] = result.inserted_id

        print(f"[KheloMore] Created booking: {booking_doc['code']} for user: {user_name} at cafe: {booking_doc['cafe_name']}")
        return {"status": "success", "booking": map_booking_doc(booking_doc)}
    except Exception as e:
        return {"status": "error", "message": f"Failed to create booking: {e}"}


def delete_booking_handler(booking_id):
    """Deletes a booking by ID to immediately release slots."""
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection is not established."}
    try:
        res = db_main.bookings.delete_one({"_id": ObjectId(booking_id)})
        if res.deleted_count == 0:
            return {"status": "error", "message": "Booking not found."}
        return {"status": "success", "message": "Booking deleted successfully."}
    except Exception as e:
        return {"status": "error", "message": f"Failed to delete booking: {e}"}


def update_booking_handler(booking_id, data):
    """Updates status or other attributes of an existing booking."""
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection is not established."}
    try:
        update_fields = {}
        if "status" in data:
            update_fields["status"] = data["status"]
        if "slot" in data:
            update_fields["slot"] = data["slot"]
        if "date" in data:
            update_fields["date"] = data["date"]
        if "rig" in data:
            update_fields["rig"] = data["rig"]

        if not update_fields:
            return {"status": "error", "message": "No valid fields to update."}

        res = db_main.bookings.update_one({"_id": ObjectId(booking_id)}, {"$set": update_fields})
        if res.matched_count == 0:
            return {"status": "error", "message": "Booking not found."}

        updated = db_main.bookings.find_one({"_id": ObjectId(booking_id)})
        return {"status": "success", "booking": map_booking_doc(updated)}
    except Exception as e:
        return {"status": "error", "message": f"Failed to update booking: {e}"}

