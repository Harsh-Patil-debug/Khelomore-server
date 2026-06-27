import random
from datetime import datetime, timezone, timedelta
from bson.objectid import ObjectId
from .db_connection import db_main

IST = timezone(timedelta(hours=5, minutes=30))

def calculate_booking_status_and_time(date_str: str, slots: list) -> tuple:
    """
    Given a booking date string and a list of slots, calculates:
    (status, remainingTimeSeconds)
    relative to the current time in IST.
    """
    now = datetime.now(IST)
    today_str = now.strftime("%Y-%m-%d")

    # 1. If date is in the past, it's completed
    if date_str < today_str:
        return "Completed", 0

    # 2. If date is in the future, it's upcoming
    if date_str > today_str:
        return "Upcoming", 0

    # 3. Date is today! Parse slot times
    try:
        start_times = []
        end_times = []
        for slot in slots:
            parts = slot.split("-")
            if len(parts) == 2:
                st = datetime.strptime(f"{date_str} {parts[0].strip()}", "%Y-%m-%d %I:%M %p").replace(tzinfo=IST)
                et = datetime.strptime(f"{date_str} {parts[1].strip()}", "%Y-%m-%d %I:%M %p").replace(tzinfo=IST)
                start_times.append(st)
                end_times.append(et)
        
        if not start_times:
            return "Upcoming", 0

        earliest_start = min(start_times)
        latest_end = max(end_times)

        if now < earliest_start:
            return "Upcoming", 0
        elif earliest_start <= now <= latest_end:
            remaining_seconds = int((latest_end - now).total_seconds())
            return "Active", remaining_seconds
        else:
            return "Completed", 0
    except Exception as e:
        print(f"Error parsing slot times: {str(e)}")
        return "Upcoming", 0

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

def create_booking_handler(user_email: str, cafe_id: str, cafe_name: str, zone: str, date: str, slots: list, price: int):
    """
    Validates slot availability and saves the booking record.
    """
    try:
        if not slots:
            return {"status": "error", "message": "No slots selected"}, 400

        # 1. Validate if any of the slots are already booked
        existing_bookings = db_main.bookings.find({
            "cafe_id": cafe_id,
            "zone": zone,
            "date": date
        })
        
        already_booked = set()
        for b in existing_bookings:
            slots_list = b.get("slots", [])
            if isinstance(slots_list, list):
                already_booked.update(slots_list)
            elif isinstance(slots_list, str):
                already_booked.update([s.strip() for s in slots_list.split(",") if s.strip()])
        
        overlapping = [slot for slot in slots if slot in already_booked]
        if overlapping:
            return {
                "status": "error",
                "message": f"Conflict detected: Slots {overlapping} are already booked."
            }, 400

        # 2. Determine rig name
        rig_num = random.randint(1, 20)
        if zone == "Console Lounge":
            rig = f"PS5 Pro #{rig_num}"
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
            "cafe_id": cafe_id,
            "cafe_name": cafe_name,
            "zone": zone,
            "date": date,
            "slots": slots,
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

def get_user_bookings_handler(user_email: str):
    """
    Fetches all bookings for the authenticated user email.
    """
    try:
        bookings = db_main.bookings.find({"user_email": user_email}).sort("createdAt", -1)
        
        bookings_list = []
        for b in bookings:
            b_id = str(b["_id"])
            del b["_id"]
            
            if "createdAt" in b:
                b["createdAt"] = b["createdAt"].isoformat()
            
            slots = b.get("slots", [])
            slot_str = ", ".join(slots)
            
            # Recalculate status dynamically based on current time
            status, remaining_time = calculate_booking_status_and_time(b.get("date"), slots)
            
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
                "userEmail": b.get("user_email")
            }
            if status == "Active":
                item["remainingTimeSeconds"] = remaining_time
                
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
