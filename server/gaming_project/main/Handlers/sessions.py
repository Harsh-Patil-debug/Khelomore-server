# sessions.py
# Handlers for managing real-time game sessions on the Live Floor

import os
import random
from datetime import datetime, timezone, timedelta
from bson import ObjectId
from .db_connection import get_db
from . import input_validation

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
            b_status = b.get("status")
            slots = b.get("slots", [])
            _, latest_end = parse_slot_times(today_str, slots)
            
            should_expire = False
            if b_status == "Active":
                actual_end_raw = b.get("actual_end_at")
                if actual_end_raw:
                    try:
                        actual_end_dt = datetime.fromisoformat(actual_end_raw)
                        if actual_end_dt.tzinfo is None:
                            actual_end_dt = actual_end_dt.replace(tzinfo=IST)
                        should_expire = (now > actual_end_dt)
                    except Exception:
                        should_expire = (now > latest_end)
                else:
                    should_expire = (now > latest_end)
            else:
                should_expire = (now > latest_end)

            if should_expire:
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

        # Reset non-maintenance rigs status to "available" initially so they are clean
        db_main.rigs.update_many(
            {"cafe_id": cafe_id, "status": {"$ne": "maintenance"}},
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

            # Sync rig status to most urgent booking ONLY if the slot is currently active/running!
            is_active_time = (b.get("date") == today_str and earliest_start <= now <= latest_end)
            
            if b_status == "Active":
                # Active session always marks rig as occupied
                if matched_rig.get("status") != "maintenance":
                    db_main.rigs.update_one({"_id": matched_rig["_id"]}, {"$set": {"status": "occupied"}})
                    matched_rig["status"] = "occupied"
            elif b_status == "Upcoming" and is_active_time:
                # Upcoming booking only marks rig as reserved if we are currently in the slot time
                if matched_rig.get("status") not in ["occupied", "maintenance"]:
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
                "slots": slots,
                "customer_name": cust_name,
                "start_at": b.get("started_at") or earliest_start.isoformat(),
                "scheduled_end_at": synced_end.isoformat(),
                "time_label": f"{b.get('date', today_str)} · {b.get('slot') or ', '.join(slots)}",
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
                # Sum individual slot durations to support non-contiguous slots correctly
                scheduled_duration = timedelta()
                for slot in slots:
                    parts = slot.split("-")
                    if len(parts) == 2:
                        try:
                            st = datetime.strptime(f"{booking_date} {parts[0].strip()}", "%Y-%m-%d %I:%M %p").replace(tzinfo=IST)
                            et = datetime.strptime(f"{booking_date} {parts[1].strip()}", "%Y-%m-%d %I:%M %p").replace(tzinfo=IST)
                            if et <= st:
                                et += timedelta(days=1)
                            scheduled_duration += (et - st)
                        except Exception:
                            scheduled_duration += timedelta(hours=1)
                    else:
                        scheduled_duration += timedelta(hours=1)

                if scheduled_duration.total_seconds() == 0:
                    scheduled_duration = timedelta(hours=1)

                actual_end_at = now + scheduled_duration
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
            hours, hours_error = input_validation.parse_bounded_number(
                data.get("duration_hours") or data.get("hours") or 1.0, "Duration", min_val=0.5, max_val=24, is_float=True
            )
            if hours_error or hours is None:
                return {"status": "error", "message": hours_error or "Duration is required."}

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
    Ends a session early, setting status to Completed and setting actual_end_at to now.
    Keeps all slots and original price/amount intact for history/payment reporting.
    """
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection is not established."}

    try:
        now = datetime.now(IST)
        booking = db_main.bookings.find_one({"_id": ObjectId(booking_id)})
        if not booking:
            return {"status": "error", "message": "Booking not found."}

        # Keep original price, amount, slots, and slot intact, only set status and actual_end_at
        db_main.bookings.update_one(
            {"_id": ObjectId(booking_id)},
            {
                "$set": {
                    "status": "Completed",
                    "actual_end_at": now.isoformat()
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
            "message": "Session ended successfully. Booking marked as Completed."
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

        # Also extend actual_end_at if set
        actual_end_at_raw = booking.get("actual_end_at")
        if actual_end_at_raw:
            try:
                actual_end_dt = datetime.fromisoformat(actual_end_at_raw)
                if actual_end_dt.tzinfo is None:
                    actual_end_dt = actual_end_dt.replace(tzinfo=IST)
                new_actual_end = actual_end_dt + timedelta(minutes=minutes)
            except Exception:
                new_actual_end = new_end
        else:
            new_actual_end = new_end

        # Save extended slots, slot string, and actual_end_at
        db_main.bookings.update_one(
            {"_id": ObjectId(booking_id)},
            {"$set": {
                "slots": slots,
                "slot": ", ".join(slots),
                "actual_end_at": new_actual_end.isoformat()
            }}
        )

        return {
            "status": "success",
            "message": f"Extended session by {minutes} minutes."
        }
    except Exception as e:
        return {"status": "error", "message": f"Failed to extend session: {e}"}

