# rigs.py
# Handlers for managing PC/Console hardware rigs in KheloMore Gaming Hub

from bson import ObjectId
from .db_connection import get_db

# Default rigs template to seed for each cafe
SEED_RIG_TEMPLATES = [
    {"type": "PC", "name": "PC #01", "spec": "RTX 4090 · 360Hz"},
    {"type": "PC", "name": "PC #02", "spec": "RTX 4090 · 360Hz"},
    {"type": "PC", "name": "PC #03", "spec": "RTX 4090 · 360Hz"},
    {"type": "PC", "name": "PC #04", "spec": "RTX 4080 · 240Hz"},
    {"type": "PC", "name": "PC #05", "spec": "RTX 4080 · 240Hz"},
    {"type": "PC", "name": "PC #06", "spec": "RTX 4080 · 240Hz"},
    {"type": "PC", "name": "PC #07", "spec": "RTX 4070 · 240Hz"},
    {"type": "PS5", "name": "PS5 #01", "spec": "DualSense Edge · 4K"},
    {"type": "PS5", "name": "PS5 #02", "spec": "DualSense Edge · 4K"},
]

def map_rig_doc(doc):
    """Maps a MongoDB rig document to the format expected by the frontend."""
    raw_name = doc.get("name", "")
    number_fallback = raw_name.split("#")[-1].strip() if "#" in raw_name else raw_name
    raw_type = doc.get("type", "PC").lower()

    return {
        "id": str(doc["_id"]),
        "cafeId": doc.get("cafe_id"),
        "type": raw_type,
        "name": raw_name,
        "number": doc.get("number") or number_fallback,
        "spec": doc.get("spec") or doc.get("specs") or "",
        "specs": doc.get("spec") or doc.get("specs") or "",
        "status": doc.get("status", "available"),
        "zone": doc.get("zone", "Standard"),
        "hourly_price": doc.get("hourly_price", 100),
    }

def get_rigs_handler(cafe_id=None):
    """Retrieves all rigs or filters by cafe_id. Seeds the database if empty."""
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection is not established."}

    try:
        # Auto-seed per cafe if cafe_id is provided and that cafe has 0 rigs
        if cafe_id and db_main.rigs.count_documents({"cafe_id": cafe_id}) == 0:
            rigs_to_insert = []
            for template in SEED_RIG_TEMPLATES:
                name = template["name"]
                number = name.split("#")[-1].strip() if "#" in name else name
                spec = template["spec"]
                
                # Determine zone and price based on type/spec
                if template["type"] == "PS5":
                    zone = "Console Lounge"
                    price = 160
                elif "RTX 4090" in spec:
                    zone = "VIP Elite Zone"
                    price = 200
                else:
                    zone = "Regular Zone"
                    price = 150
                
                rigs_to_insert.append({
                    "cafe_id": cafe_id,
                    "type": template["type"],
                    "name": name,
                    "number": number,
                    "spec": spec,
                    "status": "available",
                    "zone": zone,
                    "hourly_price": price
                })
            if rigs_to_insert:
                db_main.rigs.insert_many(rigs_to_insert)
                print(f"[KheloMore] Auto-seeded {len(rigs_to_insert)} rigs for cafe: {cafe_id}")
        
        # General fallback: if collection is completely empty, seed for all cafes
        elif db_main.rigs.count_documents({}) == 0:
            cafes = list(db_main.cafes.find({}))
            seeded_count = 0
            if cafes:
                rigs_to_insert = []
                for cafe in cafes:
                    cafe_id_str = str(cafe["_id"])
                    for template in SEED_RIG_TEMPLATES:
                        name = template["name"]
                        number = name.split("#")[-1].strip() if "#" in name else name
                        spec = template["spec"]
                        
                        if template["type"] == "PS5":
                            zone = "Console Lounge"
                            price = 160
                        elif "RTX 4090" in spec:
                            zone = "VIP Elite Zone"
                            price = 200
                        else:
                            zone = "Regular Zone"
                            price = 150

                        rigs_to_insert.append({
                            "cafe_id": cafe_id_str,
                            "type": template["type"],
                            "name": name,
                            "number": number,
                            "spec": spec,
                            "status": "available",
                            "zone": zone,
                            "hourly_price": price
                        })
                if rigs_to_insert:
                    db_main.rigs.insert_many(rigs_to_insert)
                    seeded_count = len(rigs_to_insert)
            print(f"[KheloMore] Auto-seeded {seeded_count} global hardware rigs in MongoDB.")

        # Build query
        query = {}
        if cafe_id:
            query["cafe_id"] = cafe_id

        docs = list(db_main.rigs.find(query))
        mapped = [map_rig_doc(d) for d in docs]
        return {"status": "success", "rigs": mapped}
    except Exception as e:
        return {"status": "error", "message": f"Failed to retrieve rigs: {e}"}

def create_rig_handler(data):
    """Creates a new hardware rig in MongoDB."""
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection is not established."}

    try:
        rig_type_raw = str(data.get("type", "PC")).upper()
        if rig_type_raw == "RACING_SIM":
            rig_type = "Racing Sim"
        elif rig_type_raw == "VR":
            rig_type = "VR Station"
        else:
            rig_type = rig_type_raw

        name_input = data.get("name")
        if not name_input:
            return {"status": "error", "message": "Rig 'name' is a required field."}

        number = data.get("number") or (name_input.split("#")[-1].strip() if "#" in name_input else "")
        base_name = name_input.split("#")[0].strip()
        if number:
            name = f"{base_name} #{number}"
        else:
            name = name_input

        spec = data.get("spec") or data.get("specs") or ""
        status = data.get("status", "available")
        zone = data.get("zone", "Standard")
        hourly_price = int(data.get("hourly_price") or data.get("hourlyPrice") or 100)
        cafe_id = data.get("cafeId") or data.get("cafe_id")

        rig_doc = {
            "cafe_id": cafe_id,
            "type": rig_type,
            "name": name,
            "number": number,
            "spec": spec,
            "status": status,
            "zone": zone,
            "hourly_price": hourly_price
        }

        result = db_main.rigs.insert_one(rig_doc)
        rig_doc["_id"] = result.inserted_id

        print(f"[KheloMore] Created rig: {rig_doc['name']} for cafe: {rig_doc['cafe_id']}")
        return {"status": "success", "rig": map_rig_doc(rig_doc)}
    except Exception as e:
        return {"status": "error", "message": f"Failed to create rig: {e}"}

def get_rig_detail_handler(rig_id):
    """Retrieves a single hardware rig by its ID."""
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection is not established."}

    try:
        if not ObjectId.is_valid(rig_id):
            return {"status": "error", "message": "Invalid rig ID format."}

        doc = db_main.rigs.find_one({"_id": ObjectId(rig_id)})
        if not doc:
            return {"status": "error", "message": "Rig not found."}

        return {"status": "success", "rig": map_rig_doc(doc)}
    except Exception as e:
        return {"status": "error", "message": f"Failed to retrieve rig detail: {e}"}

def update_rig_handler(rig_id, data):
    """Updates an existing hardware rig's fields in MongoDB."""
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection is not established."}

    try:
        if not ObjectId.is_valid(rig_id):
            return {"status": "error", "message": "Invalid rig ID format."}

        existing_doc = db_main.rigs.find_one({"_id": ObjectId(rig_id)})
        if not existing_doc:
            return {"status": "error", "message": "Rig not found."}

        # Fields allowed to update
        update_fields = {}
        if "type" in data:
            rt = str(data["type"]).upper()
            if rt == "RACING_SIM":
                update_fields["type"] = "Racing Sim"
            elif rt == "VR":
                update_fields["type"] = "VR Station"
            else:
                update_fields["type"] = rt

        name_input = data.get("name")
        number_input = data.get("number")
        
        if name_input is not None or number_input is not None:
            current_name = name_input if name_input is not None else existing_doc.get("name", "")
            current_number = number_input if number_input is not None else existing_doc.get("number", "")
            
            base_name = current_name.split("#")[0].strip()
            if current_number:
                update_fields["name"] = f"{base_name} #{current_number}"
                update_fields["number"] = current_number
            else:
                update_fields["name"] = current_name
                update_fields["number"] = ""
        if "spec" in data or "specs" in data:
            update_fields["spec"] = data.get("spec") or data.get("specs")
        if "status" in data:
            update_fields["status"] = data["status"]
        if "zone" in data:
            update_fields["zone"] = data["zone"]
        if "hourly_price" in data or "hourlyPrice" in data:
            try:
                update_fields["hourly_price"] = int(data.get("hourly_price") or data.get("hourlyPrice"))
            except (ValueError, TypeError):
                pass
        if "cafeId" in data or "cafe_id" in data:
            update_fields["cafe_id"] = data.get("cafeId") or data.get("cafe_id")

        if not update_fields:
            return {"status": "error", "message": "No valid fields to update were provided."}

        result = db_main.rigs.update_one(
            {"_id": ObjectId(rig_id)},
            {"$set": update_fields}
        )

        if result.matched_count == 0:
            return {"status": "error", "message": "Rig not found."}

        updated_doc = db_main.rigs.find_one({"_id": ObjectId(rig_id)})
        print(f"[KheloMore] Updated rig: {rig_id}")
        return {"status": "success", "rig": map_rig_doc(updated_doc)}
    except Exception as e:
        return {"status": "error", "message": f"Failed to update rig: {e}"}

def delete_rig_handler(rig_id):
    """Deletes a hardware rig from MongoDB."""
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection is not established."}

    try:
        if not ObjectId.is_valid(rig_id):
            return {"status": "error", "message": "Invalid rig ID format."}

        result = db_main.rigs.delete_one({"_id": ObjectId(rig_id)})
        if result.deleted_count == 0:
            return {"status": "error", "message": "Rig not found."}

        print(f"[KheloMore] Deleted rig: {rig_id}")
        return {"status": "success", "message": "Rig successfully deleted."}
    except Exception as e:
        return {"status": "error", "message": f"Failed to delete rig: {e}"}

def reserve_rig_slots_handler(rig_id, data):
    """Creates an admin booking/reservation for a specific rig and list of slots."""
    import random
    from .bookings_handler import calculate_booking_status_and_time, IST
    
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection is not established."}

    try:
        if not ObjectId.is_valid(rig_id):
            return {"status": "error", "message": "Invalid rig ID format."}

        rig = db_main.rigs.find_one({"_id": ObjectId(rig_id)})
        if not rig:
            return {"status": "error", "message": "Rig not found."}

        cafe_id = rig.get("cafe_id")
        cafe = db_main.cafes.find_one({"_id": ObjectId(cafe_id)}) if ObjectId.is_valid(cafe_id) else None
        cafe_name = cafe.get("name", "Unknown Cafe") if cafe else "Unknown Cafe"

        date = data.get("date")
        slots = data.get("slots") # list of slot strings, e.g. ["10:00 AM - 11:00 AM"]
        admin_email = data.get("admin_email") or "admin@khelomore.com"

        if not date or not slots:
            return {"status": "error", "message": "Parameters 'date' and 'slots' are required."}

        # Format rig display name
        rig_spec = rig.get("spec", "")
        rig_display_name = f"{rig.get('name')} · {rig_spec}" if rig_spec else rig.get("name", "")

        # Check for overlap/duplicate reservation on the same station and slot
        existing_bookings = list(db_main.bookings.find({
            "cafe_id": cafe_id,
            "date": date,
            "status": {"$in": ["Upcoming", "Active"]}
        }))

        rig_short_name = rig.get("name").replace("•", "·").split("·")[0].strip()

        # Check if any slot has an overlap booking on this rig
        for slot in slots:
            for eb in existing_bookings:
                eb_rig = eb.get("rig", "").replace("•", "·").split("·")[0].strip()
                if eb_rig == rig_short_name:
                    eb_slots = eb.get("slots", [])
                    if slot in eb_slots:
                        return {
                            "status": "error", 
                            "message": f"Conflict: Slot '{slot}' is already booked on this station."
                        }

        # Create booking document
        code = str(random.randint(100000, 999999))
        status, remaining_time = calculate_booking_status_and_time(date, slots)

        booking_doc = {
            "user_email": admin_email,
            "user_name": "ADMIN RESERVED",
            "cafe_id": cafe_id,
            "cafe_name": cafe_name,
            "zone": rig.get("zone", "Standard"),
            "date": date,
            "slots": slots,
            "slot": ", ".join(slots),
            "price": 0, # Admin reservations are free
            "code": code,
            "rig": rig_display_name,
            "status": status,
            "createdAt": datetime.now(IST)
        }
        if status == "Active" and remaining_time > 0:
            booking_doc["remainingTimeSeconds"] = remaining_time

        db_main.bookings.insert_one(booking_doc)

        # Set rig status to reserved in the rigs collection
        db_main.rigs.update_one(
            {"_id": ObjectId(rig_id)},
            {"$set": {"status": "reserved"}}
        )

        return {
            "status": "success",
            "message": "Slots reserved successfully.",
            "booking": {
                "id": str(booking_doc.get("_id") or ""),
                "rig": rig_display_name,
                "slots": slots,
                "date": date,
                "status": status
            }
        }
    except Exception as e:
        return {"status": "error", "message": f"Failed to reserve slots: {e}"}
