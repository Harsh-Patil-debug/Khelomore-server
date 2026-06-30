import random
from datetime import datetime, timezone, timedelta
from bson.objectid import ObjectId
from .db_connection import db_main

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
        default_start = datetime.strptime(f"{date_str} 10:00 AM", "%Y-%m-%d %I:%M %p").replace(tzinfo=IST)
        default_end = datetime.strptime(f"{date_str} 10:00 PM", "%Y-%m-%d %I:%M %p").replace(tzinfo=IST) + timedelta(hours=2)
        return default_start, default_end
    return min(start_times), max(end_times)

def calculate_booking_status_and_time(date_str: str, slots: list, db_status: str = "Upcoming", actual_end_at=None) -> tuple:
    """
    Given a booking date string and a list of slots, calculates:
    (status, remainingTimeSeconds)
    relative to the current time in IST, respecting the database status.
    """
    now = datetime.now(IST)
    today_str = now.strftime("%Y-%m-%d")

    # 1. If date is in the past, it's completed
    if date_str < today_str:
        return "Completed", 0

    # 2. Parse slot times to find latest end time
    try:
        earliest_start, latest_end = parse_slot_times(date_str, slots)

        # 3. If slot end time has passed, it's Completed/Expired
        if now > latest_end:
            return "Completed", 0

        # 4. If booking has been started manually by admin (status is "Active")
        if db_status == "Active":
            # Use actual_end_at from DB if set (synced timer), else fall back to slot end
            end_time = actual_end_at if actual_end_at else latest_end
            remaining_seconds = int((end_time - now).total_seconds())
            return "Active", max(0, remaining_seconds)

        # 5. Otherwise, even if the slot started, it stays Upcoming/Reserved until manual activation
        return "Upcoming", 0

    except Exception as e:
        print(f"Error parsing slot times: {str(e)}")
        return db_status, 0

def get_booked_slots_handler(cafe_id: str, zone: str, date: str):
    """
    Returns a list of all booked slot strings for a given cafe, zone, and date.
    """
    try:
        bookings = db_main.bookings.find({
            "cafe_id": cafe_id,
            "zone": zone,
            "date": date
        })
        
        booked_slots = []
        for b in bookings:
            slots_list = b.get("slots", [])
            if isinstance(slots_list, list):
                booked_slots.extend(slots_list)
            elif isinstance(slots_list, str):
                booked_slots.extend([s.strip() for s in slots_list.split(",") if s.strip()])
                
        return {
            "status": "success",
            "booked_slots": list(set(booked_slots))
        }, 200
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to retrieve booked slots: {str(e)}"
        }, 500

def create_booking_handler(user_email: str, cafe_id: str, cafe_name: str, zone: str, date: str, slots: list, price: int, rig: str = None, user_name: str = None):
    """
    Validates slot availability and saves the booking record.
    """
    try:
        if not user_name:
            user_doc = db_main.users.find_one({"email": user_email})
            if user_doc:
                user_name = user_doc.get("full_name") or user_doc.get("name")
            if not user_name:
                user_name = user_email.split("@")[0].upper()
        if user_email == "harshdpatil2007@gmail.com" and (not user_name or user_name.upper() == "HARSHDPATIL2007"):
            user_name = "Harsh Patil"
        if not slots:
            return {"status": "error", "message": "No slots selected"}, 400

        # 1. Validate availability at the specific machine level
        existing_bookings = list(db_main.bookings.find({
            "cafe_id": cafe_id,
            "date": date,
            "status": {"$in": ["Upcoming", "Active"]}
        }))
        
        if rig:
            clean_req_rig = rig.replace("•", "·").replace("  ", " ").split("·")[0].strip()
            
            for b in existing_bookings:
                b_rig = b.get("rig", "").replace("•", "·").replace("  ", " ").split("·")[0].strip()
                if b_rig == clean_req_rig:
                    b_slots = b.get("slots", [])
                    overlapping = [s for s in slots if s in b_slots]
                    if overlapping:
                        return {
                            "status": "error",
                            "message": f"Conflict detected: Station '{clean_req_rig}' is already booked for slots {overlapping}."
                        }, 400
        else:
            # Fallback to zone-wide capacity validation if no specific rig is selected
            rigs = list(db_main.rigs.find({"cafe_id": cafe_id}))
            if zone == "Console Lounge":
                matching_rigs = [r for r in rigs if r.get("type", "").upper() in ["PS5", "XBOX"]]
            else:
                matching_rigs = [r for r in rigs if r.get("type", "").upper() == "PC"]
            
            matching_rig_names = {r.get("name") for r in matching_rigs}
            
            for slot in slots:
                bookings_for_slot = 0
                for b in existing_bookings:
                    b_rig = b.get("rig", "").replace("•", "·").replace("  ", " ").split("·")[0].strip()
                    if b_rig in matching_rig_names and slot in b.get("slots", []):
                        bookings_for_slot += 1
                if bookings_for_slot >= len(matching_rigs) and len(matching_rigs) > 0:
                    return {
                        "status": "error",
                        "message": f"Conflict detected: All stations in {zone} are fully booked for slot '{slot}'."
                    }, 400

        # 2. Determine rig name: Use client's selected rig if provided
        if rig:
            # Clean and normalize bullet symbol to center dot
            rig = rig.replace("•", "·").replace("  ", " ").strip()
            rig_name = rig.split("·")[0].strip()
            db_rig = db_main.rigs.find_one({"cafe_id": cafe_id, "name": rig_name})
            if db_rig and db_rig.get("status") == "maintenance":
                return {
                    "status": "error",
                    "message": f"Rig '{rig_name}' is currently under maintenance and cannot be booked."
                }, 400
        else:
            # Rig auto-assignment
            rigs = list(db_main.rigs.find({"cafe_id": cafe_id}))
            
            # Filter rigs by type based on zone, ignoring any under maintenance
            if zone == "Console Lounge":
                matching_rigs = [r for r in rigs if r.get("type", "").upper() in ["PS5", "XBOX"] and r.get("status") != "maintenance"]
            else:
                matching_rigs = [r for r in rigs if r.get("type", "").upper() == "PC" and r.get("status") != "maintenance"]

            if matching_rigs:
                booked_rigs = set()
                existing_bookings = db_main.bookings.find({
                    "cafe_id": cafe_id,
                    "date": date,
                    "status": {"$in": ["Upcoming", "Active"]}
                })
                for eb in existing_bookings:
                    eb_slots = eb.get("slots", [])
                    has_overlap = any(s in eb_slots for s in slots)
                    if has_overlap:
                        eb_rig = eb.get("rig", "").split("·")[0].strip()
                        booked_rigs.add(eb_rig)
                
                # Find an available rig
                available_rigs = [r for r in matching_rigs if r.get("name") not in booked_rigs]
                if available_rigs:
                    assigned_rig = random.choice(available_rigs)
                else:
                    assigned_rig = random.choice(matching_rigs)
                    
                assigned_rig_name = assigned_rig.get("name")
                rig_spec = assigned_rig.get("spec", "")
                rig = f"{assigned_rig_name} · {rig_spec}" if rig_spec else assigned_rig_name
            else:
                # Fallback to random if no rigs in database
                rig_num = random.randint(1, 7)
                if zone == "Console Lounge":
                    rig = f"PS5 #{str(rig_num).zfill(2)}"
                else:
                    rig_spec = "RTX 4090" if zone == "VIP Elite Zone" else "RTX 4070"
                    rig = f"PC #{str(rig_num).zfill(2)} · {rig_spec}"

        # 3. Generate booking code
        code = str(random.randint(100000, 999999))

        # 4. Determine status based on slot times
        status, remaining_time = calculate_booking_status_and_time(date, slots)

        # 5. Insert booking document
        booking_doc = {
            "user_email": user_email,
            "user_name": user_name,
            "cafe_id": cafe_id,
            "cafe_name": cafe_name,
            "zone": zone,
            "date": date,
            "slots": slots,
            "slot": ", ".join(slots),
            "price": price,
            "code": code,
            "rig": rig,
            "status": status,
            "createdAt": datetime.now(IST)
        }
        if status == "Active" and remaining_time > 0:
            booking_doc["remainingTimeSeconds"] = remaining_time

        result = db_main.bookings.insert_one(booking_doc)
        booking_doc["id"] = str(result.inserted_id)
        
        if "_id" in booking_doc:
            del booking_doc["_id"]
        if "createdAt" in booking_doc:
            booking_doc["createdAt"] = booking_doc["createdAt"].isoformat()

        return {
            "status": "success",
            "message": "Booking secured successfully",
            "booking": booking_doc
        }, 201

    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to create booking: {str(e)}"
        }, 500

def get_user_bookings_handler(user_email: str, cafe_id: str = None):
    """
    Fetches all bookings. If cafe_id is provided, fetches all bookings for that cafe.
    Otherwise, if the user is a Cafe Owner/Admin, fetches all bookings for their cafes.
    Otherwise, fetches all bookings made by this user email.
    """
    try:
        if cafe_id:
            bookings = db_main.bookings.find({"cafe_id": cafe_id}).sort("createdAt", -1)
        else:
            # Check if user is a super admin
            user_doc = db_main.users.find_one({"email": user_email})
            is_super_admin = False
            if user_doc:
                is_super_admin = user_doc.get("is_super_admin") or user_doc.get("role") == "super_admin"
                
            if is_super_admin:
                # Super Admin sees all bookings in the system
                bookings = db_main.bookings.find({}).sort("createdAt", -1)
            else:
                # Check if this user is a cafe owner (has registered cafes)
                owned_cafes = list(db_main.cafes.find({"owner_email": user_email}))
                if owned_cafes:
                    # It's an admin! Get all bookings for their cafes OR bookings they personally made
                    cafe_ids = [str(c["_id"]) for c in owned_cafes]
                    bookings = db_main.bookings.find({
                        "$or": [
                            {"user_email": user_email},
                            {"cafe_id": {"$in": cafe_ids}}
                        ]
                    }).sort("createdAt", -1)
                else:
                    # It's a regular user! Get bookings they made
                    bookings = db_main.bookings.find({"user_email": user_email}).sort("createdAt", -1)
        
        bookings_list = []
        for b in bookings:
            b_id = str(b["_id"])
            
            # Auto-expire if end time has passed in the database view pass
            slots = b.get("slots", [])
            date_str = b.get("date")
            now = datetime.now(IST)
            if slots and date_str:
                _, latest_end = parse_slot_times(date_str, slots)
                if now > latest_end and b.get("status") in ["Upcoming", "Active"]:
                    db_main.bookings.update_one({"_id": b["_id"]}, {"$set": {"status": "Completed"}})
                    b["status"] = "Completed"
                    # Free rig status
                    rig_name = b.get("rig", "").replace("•", "·").split("·")[0].strip()
                    db_main.rigs.update_one(
                        {"cafe_id": b.get("cafe_id"), "name": rig_name},
                        {"$set": {"status": "available"}}
                    )

            del b["_id"]
            
            if "createdAt" in b:
                if isinstance(b["createdAt"], datetime):
                    b["createdAt"] = b["createdAt"].isoformat()
                else:
                    b["createdAt"] = str(b["createdAt"])
            
            slot_str = ", ".join(slots)
            
            # Recalculate status dynamically based on current time and DB state
            actual_end_at_raw = b.get("actual_end_at")
            actual_end_at_dt = None
            if actual_end_at_raw:
                try:
                    if isinstance(actual_end_at_raw, str):
                        actual_end_at_dt = datetime.fromisoformat(actual_end_at_raw)
                        if actual_end_at_dt.tzinfo is None:
                            actual_end_at_dt = actual_end_at_dt.replace(tzinfo=IST)
                    else:
                        actual_end_at_dt = actual_end_at_raw
                        if getattr(actual_end_at_dt, 'tzinfo', None) is None:
                            actual_end_at_dt = actual_end_at_dt.replace(tzinfo=IST)
                except Exception:
                    actual_end_at_dt = None

            status, remaining_time = calculate_booking_status_and_time(
                b.get("date"), slots, db_status=b.get("status", "Upcoming"), actual_end_at=actual_end_at_dt
            )
            
            item = {
                "id": b_id,
                "cafeId": b.get("cafe_id"),
                "cafeName": b.get("cafe_name"),
                "zone": b.get("zone"),
                "date": b.get("date"),
                "slot": slot_str,
                "price": b.get("price"),
                "code": b.get("code"),
                "status": status,
                "rig": b.get("rig"),
                "userEmail": b.get("user_email"),
                "userName": b.get("user_name"),
            }
            if status == "Active":
                item["remainingTimeSeconds"] = remaining_time
                if actual_end_at_raw:
                    item["actualEndAt"] = actual_end_at_raw
                if b.get("started_at"):
                    item["startedAt"] = b.get("started_at")
                
            bookings_list.append(item)
            
        return {
            "status": "success",
            "bookings": bookings_list
        }, 200
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to retrieve user bookings: {str(e)}"
        }, 500
