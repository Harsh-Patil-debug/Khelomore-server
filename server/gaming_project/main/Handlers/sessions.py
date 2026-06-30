# sessions.py
# Handlers for managing real-time game sessions on the Live Floor

import os
import random
from datetime import datetime, timezone, timedelta
from bson import ObjectId
from .db_connection import get_db

IST = timezone(timedelta(hours=5, minutes=30))

def parse_slot_times(date_str: str, slots: list) -> tuple:
    """
    Parses start and end times for a list of slots relative to a date string.
    Returns (earliest_start, latest_end) as localized timezone-aware datetimes.
    """
    start_times = []
    end_times = []
    for slot in slots:
        parts = slot.split("-")
        if len(parts) == 2:
            try:
                st = datetime.strptime(f"{date_str} {parts[0].strip()}", "%Y-%m-%d %I:%M %p").replace(tzinfo=IST)
                et = datetime.strptime(f"{date_str} {parts[1].strip()}", "%Y-%m-%d %I:%M %p").replace(tzinfo=IST)
                if et <= st:
                    et += timedelta(days=1)
                start_times.append(st)
                end_times.append(et)
            except Exception:
                pass
    if not start_times:
        # Fallback to default full-day window if parsing fails
        default_start = datetime.strptime(f"{date_str} 10:00 AM", "%Y-%m-%d %I:%M %p").replace(tzinfo=IST)
        default_end = datetime.strptime(f"{date_str} 10:00 PM", "%Y-%m-%d %I:%M %p").replace(tzinfo=IST) + timedelta(hours=2)
        return default_start, default_end
    return min(start_times), max(end_times)

def list_sessions_handler(cafe_id: str):
    """
    Returns all active and reserved sessions for a given cafe on the current date,
    automatically expiring past/unstarted bookings.
    """
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection is not established."}

    try:
        now = datetime.now(IST)
        today_str = now.strftime("%Y-%m-%d")

        # 1. Housekeeping: Auto-expire past/completed bookings for today
        bookings = list(db_main.bookings.find({
            "cafe_id": cafe_id,
            "date": today_str,
            "status": {"$in": ["Active", "Upcoming"]}
        }))

        for b in bookings:
            slots = b.get("slots", [])
            _, latest_end = parse_slot_times(today_str, slots)
            if now > latest_end:
                # Slot has ended - automatically mark completed
                db_main.bookings.update_one(
                    {"_id": b["_id"]},
                    {"$set": {"status": "Completed"}}
                )
                # Free corresponding rig status in DB
                rig_name = b.get("rig", "").replace("•", "·").split("·")[0].strip()
                db_main.rigs.update_one(
                    {"cafe_id": cafe_id, "name": rig_name},
                    {"$set": {"status": "available"}}
                )

        # 2. Re-fetch current bookings & rigs (including future bookings)
        bookings = list(db_main.bookings.find({
            "cafe_id": cafe_id,
            "status": {"$in": ["Active", "Upcoming"]}
        }).sort([("date", 1), ("slots", 1)]))
        
        rigs = list(db_main.rigs.find({"cafe_id": cafe_id}))
        rig_map = {r.get("name", ""): r for r in rigs}  # name -> rig doc

        mapped_sessions = []
        rigs_with_bookings = set()

        # One session entry per booking
        for b in bookings:
            b_rig_name = b.get("rig", "").replace("•", "·").split("·")[0].strip()
            matched_rig = rig_map.get(b_rig_name)
            if not matched_rig:
                continue

            rig_id_str = str(matched_rig["_id"])
            rigs_with_bookings.add(b_rig_name)
            b_status = b.get("status")
            slots = b.get("slots", [])
            earliest_start, latest_end = parse_slot_times(b.get("date"), slots)

            # Sync rig status to most urgent booking (Active > reserved)
            expected_rig_status = "occupied" if b_status == "Active" else "reserved"
            current_rig_status = matched_rig.get("status", "available")
            if current_rig_status not in ["occupied"] and expected_rig_status == "occupied":
                db_main.rigs.update_one({"_id": matched_rig["_id"]}, {"$set": {"status": "occupied"}})
                matched_rig["status"] = "occupied"
            elif current_rig_status == "available":
                db_main.rigs.update_one({"_id": matched_rig["_id"]}, {"$set": {"status": "reserved"}})
                matched_rig["status"] = "reserved"

            # Use admin-set actual_end_at if available, else fall back to slot end
            actual_end_at_raw = b.get("actual_end_at")
            if actual_end_at_raw and b_status == "Active":
                try:
                    actual_end_dt = datetime.fromisoformat(actual_end_at_raw)
                    if actual_end_dt.tzinfo is None:
                        actual_end_dt = actual_end_dt.replace(tzinfo=IST)
                    synced_end = actual_end_dt
                except Exception:
                    synced_end = latest_end
            else:
                synced_end = latest_end

            # Retrieve customer name (first name + last name mapping)
            user_name_raw = b.get("user_name") or b.get("userName")
            email_raw = b.get("user_email", "").strip().lower()
            
            if email_raw == "harshdpatil2007@gmail.com":
                cust_name = "Harsh Patil"
            elif email_raw == "shrutidpatil0309@gmail.com":
                cust_name = "Shruti Patil"
            elif email_raw == "co2023.harsh.patil@ves.ac.in":
                cust_name = "Harsh Patil"
            elif email_raw == "vmingale2007@gmail.com":
                cust_name = "Vedant Ingale"
            elif email_raw == "pmingale5284@gmail.com":
                cust_name = "Poonam Mingale"
            else:
                cust_name = user_name_raw or "Guest Player"
                if cust_name.isupper() and len(cust_name.split()) == 1:
                    cust_name = cust_name.capitalize()

            mapped_sessions.append({
                "id": str(b["_id"]),
                "system_id": rig_id_str,
                "rig_name": b_rig_name,
                "date": b.get("date"),
                "customer_name": cust_name,
                "start_at": b.get("started_at") or earliest_start.isoformat(),
                "scheduled_end_at": synced_end.isoformat(),
                "time_label": f"{b.get('date', today_str)} · {earliest_start.strftime('%I:%M %p')} - {latest_end.strftime('%I:%M %p')}",
                "status": "active" if b_status == "Active" else "reserved"
            })

        # Free rigs that have no more bookings
        for r in rigs:
            if r.get("name") not in rigs_with_bookings and r.get("status") not in ["available", "maintenance"]:
                db_main.rigs.update_one({"_id": r["_id"]}, {"$set": {"status": "available"}})

        return {
            "status": "success",
            "sessions": mapped_sessions
        }
    except Exception as e:
        return {"status": "error", "message": f"Failed to list floor sessions: {e}"}

def start_session_handler(booking_id: str = None, data: dict = None):
    """
    Starts a session manually.
    If booking_id is provided: Activates an online booking.
    Otherwise: Creates a new walk-in session.
    """
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection is not established."}

    try:
        now = datetime.now(IST)
        today_str = now.strftime("%Y-%m-%d")

        if booking_id:
            # 1. Activation of existing reservation
            booking = db_main.bookings.find_one({"_id": ObjectId(booking_id)})
            if not booking:
                return {"status": "error", "message": "Booking not found."}

            # Calculate actual end time from slot duration
            slots = booking.get("slots", [])
            booking_date = booking.get("date", today_str)
            try:
                _, latest_end = parse_slot_times(booking_date, slots)
                # actual_end_at = now + (slot duration), capped to slot end
                slot_duration = latest_end - min(now, latest_end)
                actual_end_at = now + slot_duration
            except Exception:
                actual_end_at = now + timedelta(hours=1)

            db_main.bookings.update_one(
                {"_id": ObjectId(booking_id)},
                {"$set": {
                    "status": "Active",
                    "started_at": now.isoformat(),
                    "actual_end_at": actual_end_at.isoformat()
                }}
            )
            
            rig_name = booking.get("rig", "").replace("•", "·").split("·")[0].strip()
            db_main.rigs.update_one(
                {"cafe_id": booking["cafe_id"], "name": rig_name},
                {"$set": {"status": "occupied"}}
            )
            return {"status": "success", "message": "Session started successfully.", "actual_end_at": actual_end_at.isoformat()}
            
        elif data:
            # 2. Starting a manual walk-in session from scratch
            system_id = data.get("system_id") or data.get("systemId")
            customer_name = data.get("customer_name") or data.get("customerName") or "Walk-in Customer"
            hours = float(data.get("duration_hours") or data.get("hours") or 1.0)
            
            rig = db_main.rigs.find_one({"_id": ObjectId(system_id)})
            if not rig:
                return {"status": "error", "message": "Hardware station not found."}
                
            cafe = db_main.cafes.find_one({"_id": ObjectId(rig["cafe_id"])})
            cafe_name = cafe.get("name", "Unknown Cafe") if cafe else "Unknown Cafe"
            
            end_time = now + timedelta(hours=hours)
            slot_str = f"{now.strftime('%I:%M %p')} - {end_time.strftime('%I:%M %p')}"
            price = int(rig.get("hourly_price", 80) * hours)
            code = str(random.randint(100000, 999999))
            
            booking_doc = {
                "user_email": "walkin@khelomore.com",
                "user_name": customer_name,
                "cafe_id": rig["cafe_id"],
                "cafe_name": cafe_name,
                "zone": rig.get("zone", "Standard"),
                "date": today_str,
                "slots": [slot_str],
                "price": price,
                "code": code,
                "rig": rig["name"],
                "status": "Active",
                "createdAt": now
            }
            
            res = db_main.bookings.insert_one(booking_doc)
            db_main.rigs.update_one({"_id": ObjectId(system_id)}, {"$set": {"status": "occupied"}})
            
            return {
                "status": "success",
                "message": "Walk-in session started.",
                "booking_id": str(res.inserted_id)
            }
            
        return {"status": "error", "message": "Missing booking_id or walk-in data."}
    except Exception as e:
        return {"status": "error", "message": f"Failed to start session: {e}"}

def end_session_handler(booking_id: str):
    """
    Ends a session early, releasing any future slots from the booking document
    while keeping the payment/price intact for the admin.
    """
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection is not established."}

    try:
        now = datetime.now(IST)
        booking = db_main.bookings.find_one({"_id": ObjectId(booking_id)})
        if not booking:
            return {"status": "error", "message": "Booking not found."}

        slots = booking.get("slots", [])
        date_str = booking.get("date")
        
        # Identify unused future slots
        used_slots = []
        for slot in slots:
            parts = slot.split("-")
            if len(parts) == 2:
                try:
                    slot_start = datetime.strptime(f"{date_str} {parts[0].strip()}", "%Y-%m-%d %I:%M %p").replace(tzinfo=IST)
                    # If this slot starts in the future (more than 5 mins from now), it is released
                    if now < slot_start - timedelta(minutes=5):
                        continue
                except Exception:
                    pass
            used_slots.append(slot)

        # Enforce keeping at least the first slot so booking document remains valid
        if not used_slots and slots:
            used_slots = [slots[0]]

        # Update the booking slots (releasing the unused future ones) and status to Completed
        db_main.bookings.update_one(
            {"_id": ObjectId(booking_id)},
            {
                "$set": {
                    "slots": used_slots,
                    "status": "Completed"
                }
            }
        )

        # Free the rig
        rig_name = booking.get("rig", "").split("·")[0].strip()
        db_main.rigs.update_one(
            {"cafe_id": booking["cafe_id"], "name": rig_name},
            {"$set": {"status": "available"}}
        )

        return {
            "status": "success",
            "message": f"Session ended. Released {len(slots) - len(used_slots)} future slots."
        }
    except Exception as e:
        return {"status": "error", "message": f"Failed to end session: {e}"}

def extend_session_handler(booking_id: str, minutes: int):
    """
    Extends an active session by the specified number of minutes.
    """
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection is not established."}

    try:
        booking = db_main.bookings.find_one({"_id": ObjectId(booking_id)})
        if not booking:
            return {"status": "error", "message": "Booking not found."}

        slots = booking.get("slots", [])
        if not slots:
            return {"status": "error", "message": "Cannot extend a booking with no slots."}

        # Parse the latest slot end time and add extension minutes
        earliest_start, latest_end = parse_slot_times(booking.get("date"), slots)
        new_end = latest_end + timedelta(minutes=minutes)
        
        # Modify the last slot in the array or replace it to show new extension
        last_slot = slots[-1]
        parts = last_slot.split("-")
        if len(parts) == 2:
            new_last_slot = f"{parts[0].strip()} - {new_end.strftime('%I:%M %p')}"
            slots[-1] = new_last_slot

        # Save extended slot list
        db_main.bookings.update_one(
            {"_id": ObjectId(booking_id)},
            {"$set": {"slots": slots}}
        )

        return {
            "status": "success",
            "message": f"Extended session by {minutes} minutes."
        }
    except Exception as e:
        return {"status": "error", "message": f"Failed to extend session: {e}"}
