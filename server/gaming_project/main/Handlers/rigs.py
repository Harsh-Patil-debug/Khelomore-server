# rigs.py
# Handlers for managing PC/Console hardware rigs in BookMyConsole Gaming Hub

import secrets
from datetime import datetime, timezone, timedelta
from bson import ObjectId
from .db_connection import get_db
from . import input_validation

IST = timezone(timedelta(hours=5, minutes=30))

# A rig's agent is considered online if it polled within this window — matches the
# agent's own ~4s poll interval with generous slack for a missed beat or two before
# flipping the admin UI's indicator, rather than flickering offline on every tiny delay.
AGENT_ONLINE_WINDOW_SECONDS = 15

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

def map_rig_doc(doc, cafe_prices=None):
    """Maps a MongoDB rig document to the format expected by the frontend."""
    raw_name = doc.get("name", "")
    number_fallback = raw_name.split("#")[-1].strip() if "#" in raw_name else raw_name
    raw_type = doc.get("type", "PC").lower()

    # Dynamic lookup of cafe price to ensure it is applied to every system
    cafe_id_str = str(doc.get("cafe_id"))
    cafe_price = None
    if cafe_prices and cafe_id_str in cafe_prices:
        cafe_price = cafe_prices[cafe_id_str]
    else:
        db_main = get_db()
        if db_main is not None and doc.get("cafe_id"):
            try:
                cafe = db_main.cafes.find_one({"_id": ObjectId(doc.get("cafe_id"))}, {"price_per_hour": 1})
                if cafe:
                    cafe_price = cafe.get("price_per_hour")
            except Exception:
                pass

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
        "hourly_price": doc.get("hourly_price") if doc.get("hourly_price") is not None else (cafe_price if cafe_price is not None else 150),
        # Free-text games the admin says are installed on this rig (e.g. "GTA 5", "BGMI")
        # — matched case-insensitively against the customer's selected game in the
        # booking flow (bookings_handler.create_booking_handler). No games catalog exists
        # anywhere in this codebase by design; this is deliberately just per-rig tags.
        "installed_games": doc.get("installed_games") or [],
        # Hardware-agent connectivity — never the raw agent_token itself (that's a
        # secret, only ever returned once by generate_agent_token_handler at pairing
        # time). hasAgent just means "has this rig ever been paired", independent of
        # whether it's currently online.
        "hasAgent": bool(doc.get("agent_token")),
        "agentConnected": _is_agent_online(doc.get("agent_last_seen")),
        "agentLastSeen": doc.get("agent_last_seen"),
    }


def _is_agent_online(last_seen_raw) -> bool:
    # Stored (and read back) as an ISO string, never a raw datetime object — pymongo
    # silently strips tzinfo on read (returns naive UTC) unless the client is configured
    # tz_aware, which this codebase's db_connection.py isn't; every other
    # stored-timestamp-compared-to-now field in this codebase (actual_end_at, etc.)
    # already works around this the same way, by storing .isoformat() strings and
    # re-parsing with fromisoformat() instead of trusting a raw datetime round-trip.
    if not last_seen_raw:
        return False
    try:
        last_seen = datetime.fromisoformat(last_seen_raw)
        if last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=IST)
    except (TypeError, ValueError):
        return False
    return (datetime.now(IST) - last_seen) <= timedelta(seconds=AGENT_ONLINE_WINDOW_SECONDS)

def get_rigs_handler(cafe_id=None):
    """Retrieves all rigs or filters by cafe_id. Seeds the database if empty."""
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection is not established."}

    try:
        # Auto-seed per cafe if cafe_id is provided and that cafe has 0 rigs
        if cafe_id and db_main.rigs.count_documents({"cafe_id": cafe_id}) == 0:
            cafe_doc = db_main.cafes.find_one({"_id": ObjectId(cafe_id)})
            cafe_price = cafe_doc.get("price_per_hour") if cafe_doc else None
            
            rigs_to_insert = []
            for template in SEED_RIG_TEMPLATES:
                name = template["name"]
                number = name.split("#")[-1].strip() if "#" in name else name
                spec = template["spec"]
                
                # Determine zone based on type/spec
                if template["type"] == "PS5":
                    zone = "Console Lounge"
                    price = cafe_price if cafe_price is not None else 160
                elif "RTX 4090" in spec:
                    zone = "VIP Elite Zone"
                    price = cafe_price if cafe_price is not None else 200
                else:
                    zone = "Regular Zone"
                    price = cafe_price if cafe_price is not None else 150
                
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
                print(f"[BookMyConsole] Auto-seeded {len(rigs_to_insert)} rigs for cafe: {cafe_id}")
        
        # General fallback: if collection is completely empty, seed for all cafes
        elif db_main.rigs.count_documents({}) == 0:
            cafes = list(db_main.cafes.find({"is_deleted": {"$ne": True}}))
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
            print(f"[BookMyConsole] Auto-seeded {seeded_count} global hardware rigs in MongoDB.")

        # Build query
        query = {}
        if cafe_id:
            query["cafe_id"] = cafe_id

        docs = list(db_main.rigs.find(query))
        
        # Calculate dynamic status based on active/upcoming bookings
        if docs:
            from datetime import datetime
            from .bookings_handler import parse_slot_times, IST, calculate_booking_status_and_time
            now = datetime.now(IST)
            today_str = now.strftime("%Y-%m-%d")
            
            cafe_ids = list(set(d.get("cafe_id") for d in docs if d.get("cafe_id")))
            bookings = list(db_main.bookings.find({
                "cafe_id": {"$in": cafe_ids},
                "status": {"$in": ["Active", "Upcoming"]}
            }))
            
            bookings_by_rig = {}
            for b in bookings:
                # First run auto-expiration check on bookings in the database
                b_status = b.get("status")
                slots = b.get("slots", [])
                date_str = b.get("date")
                
                should_expire = False
                if date_str:
                    try:
                        computed_status, _ = calculate_booking_status_and_time(date_str, slots or [], b_status)
                        if computed_status == "Completed":
                            should_expire = True
                    except Exception:
                        pass
                
                if should_expire:
                    db_main.bookings.update_one({"_id": b["_id"]}, {"$set": {"status": "Completed"}})
                    b["status"] = "Completed"
                    # Exclude from active listings
                    continue

                rig_name = b.get("rig", "").replace("•", "·").split("·")[0].strip()
                bookings_by_rig.setdefault(rig_name, []).append(b)
                
            for d in docs:
                if d.get("status") == "maintenance":
                    continue
                    
                rig_name = d.get("name", "").replace("•", "·").split("·")[0].strip()
                rig_bookings = bookings_by_rig.get(rig_name, [])
                
                new_status = "available"
                for b in rig_bookings:
                    b_status = b.get("status")
                    slots = b.get("slots", [])
                    try:
                        earliest_start, latest_end = parse_slot_times(b.get("date"), slots)
                        is_active_time = (b.get("date") == today_str and earliest_start <= now <= latest_end)
                        
                        if b_status == "Active":
                            new_status = "occupied"
                            break
                        elif b_status == "Upcoming" and is_active_time:
                            new_status = "reserved"
                    except Exception:
                        pass
                        
                if d.get("status") != new_status:
                    db_main.rigs.update_one({"_id": d["_id"]}, {"$set": {"status": new_status}})
                    d["status"] = new_status

        # Batch fetch cafe prices to avoid N+1 queries
        cafe_ids_to_query = list(set(ObjectId(d.get("cafe_id")) for d in docs if d.get("cafe_id") and ObjectId.is_valid(d.get("cafe_id"))))
        cafes_docs = list(db_main.cafes.find({"_id": {"$in": cafe_ids_to_query}}, {"_id": 1, "price_per_hour": 1}))
        cafe_prices = {str(c["_id"]): c.get("price_per_hour") for c in cafes_docs}

        mapped = [map_rig_doc(d, cafe_prices=cafe_prices) for d in docs]
        return {"status": "success", "rigs": mapped}
    except Exception as e:
        return {"status": "error", "message": f"Failed to retrieve rigs: {e}"}

def create_rig_handler(data):
    """Creates a new hardware rig in MongoDB."""
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection is not established."}

    try:
        # .strip() matters now that the admin panel lets an owner type a brand-new
        # category freely — stray whitespace would otherwise silently create a
        # near-duplicate category (e.g. "PS5" vs "PS5 ") that looks identical in the UI.
        rig_type_raw = str(data.get("type", "PC")).strip().upper()
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
        hourly_price, price_error = input_validation.parse_bounded_number(
            data.get("hourly_price") or data.get("hourlyPrice") or 100, "Hourly Price", min_val=0, max_val=100000
        )
        if price_error:
            return {"status": "error", "message": price_error}
        cafe_id = data.get("cafeId") or data.get("cafe_id")

        installed_games, games_error = input_validation.parse_string_list(
            data.get("installed_games") or data.get("installedGames"),
            "Installed games", max_items=20, max_item_len=40,
        )
        if games_error:
            return {"status": "error", "message": games_error}

        rig_doc = {
            "cafe_id": cafe_id,
            "type": rig_type,
            "name": name,
            "number": number,
            "spec": spec,
            "specs": spec,
            "status": status,
            "zone": zone,
            "hourly_price": hourly_price,
            "installed_games": installed_games,
        }

        result = db_main.rigs.insert_one(rig_doc)
        rig_doc["_id"] = result.inserted_id

        print(f"[BookMyConsole] Created rig: {rig_doc['name']} for cafe: {rig_doc['cafe_id']}")
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
            rt = str(data["type"]).strip().upper()
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
            val = data.get("spec") or data.get("specs")
            update_fields["spec"] = val
            update_fields["specs"] = val
        if "status" in data:
            update_fields["status"] = data["status"]
        if "zone" in data:
            update_fields["zone"] = data["zone"]
        if "hourly_price" in data or "hourlyPrice" in data:
            price, price_error = input_validation.parse_bounded_number(
                data.get("hourly_price") or data.get("hourlyPrice"), "Hourly Price", min_val=0, max_val=100000
            )
            if price_error:
                return {"status": "error", "message": price_error}
            update_fields["hourly_price"] = price
        if "installed_games" in data or "installedGames" in data:
            installed_games, games_error = input_validation.parse_string_list(
                data.get("installed_games") if "installed_games" in data else data.get("installedGames"),
                "Installed games", max_items=20, max_item_len=40,
            )
            if games_error:
                return {"status": "error", "message": games_error}
            update_fields["installed_games"] = installed_games
        # SECURITY: cafe_id is deliberately NOT updatable here. The caller is authorized
        # against the rig's CURRENT cafe_id only (RigDetailView._authenticate_for_rig
        # checks ownership of the rig as it exists now, before this update runs) — if this
        # field were writable, a cafe owner could reassign their own rig into a DIFFERENT
        # cafe's tenant scope they were never authorized against, polluting that cafe's
        # rig list, Live Floor, and booking-capacity math. A rig's cafe is fixed at
        # creation (rigs.create_rig_handler), matching how offers/cafes already refuse to
        # let ownership be reassigned via their own update handlers.
        if "cafeId" in data or "cafe_id" in data:
            return {"status": "error", "message": "A rig's cafe cannot be changed after creation."}

        if not update_fields:
            return {"status": "error", "message": "No valid fields to update were provided."}

        result = db_main.rigs.update_one(
            {"_id": ObjectId(rig_id)},
            {"$set": update_fields}
        )

        if result.matched_count == 0:
            return {"status": "error", "message": "Rig not found."}

        updated_doc = db_main.rigs.find_one({"_id": ObjectId(rig_id)})
        print(f"[BookMyConsole] Updated rig: {rig_id}")
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

        print(f"[BookMyConsole] Deleted rig: {rig_id}")
        return {"status": "success", "message": "Rig successfully deleted."}
    except Exception as e:
        return {"status": "error", "message": f"Failed to delete rig: {e}"}

def reserve_rig_slots_handler(rig_id, data):
    """Creates an admin booking/reservation for a specific rig and list of slots."""
    import random
    from datetime import datetime
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
        admin_email = data.get("admin_email") or "admin@bookmyconsole.com"

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


# ── Hardware agent: pairing, polling, commands ───────────────────────────────────
# A small background app ("the agent") installed on each physical PC connects OUT to
# these endpoints (pull model — works regardless of NAT/where the admin panel is opened
# from, no port-forwarding needed at the cafe). The agent authenticates with a per-rig
# secret token (X-Agent-Token header), completely separate from the normal cafe-owner
# JWT scheme used everywhere else in this file — the agent is a machine, not a logged-in
# user. See the feature plan for the full design (why polling + a local client-side
# timer gives precise auto-close timing without needing sub-second round trips).

def generate_agent_token_handler(rig_id):
    """Admin action: (re)pairs a rig with a fresh agent token, shown exactly once in
    this response — never re-readable afterward, same as any other API-key-style secret.
    Overwrites any previous token outright, so re-pairing immediately invalidates a
    lost/leaked old one; the agent config on the PC just needs updating with the new
    value if it was already set up."""
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection is not established."}
    try:
        if not ObjectId.is_valid(rig_id):
            return {"status": "error", "message": "Invalid rig ID format."}
        rig = db_main.rigs.find_one({"_id": ObjectId(rig_id)})
        if not rig:
            return {"status": "error", "message": "Rig not found."}

        token = secrets.token_urlsafe(32)
        db_main.rigs.update_one({"_id": ObjectId(rig_id)}, {"$set": {"agent_token": token, "agent_last_seen": None}})
        return {"status": "success", "agent_token": token, "rig_id": rig_id}
    except Exception as e:
        return {"status": "error", "message": f"Failed to generate agent token: {e}"}


def _authenticate_agent(db_main, rig_id, token):
    """Returns (rig_doc, error_dict). error_dict is None on success."""
    if not token:
        return None, {"status": "error", "message": "Missing agent token."}
    if not ObjectId.is_valid(rig_id):
        return None, {"status": "error", "message": "Invalid rig ID format."}
    rig = db_main.rigs.find_one({"_id": ObjectId(rig_id)})
    if not rig:
        return None, {"status": "error", "message": "Rig not found."}
    if not rig.get("agent_token") or not secrets.compare_digest(rig["agent_token"], token):
        return None, {"status": "error", "message": "Invalid agent token."}
    return rig, None


def _resolve_active_session_for_rig(db_main, rig_id):
    """The rig's current Active session, if any — {"ends_at": iso, "game": str, "process_name": str|None}
    or None. New-shape (group) bookings only: a rig's real per-rig status/end-time lives
    nested under rig_sessions[rig_id], not at the booking's top level (same place every
    other per-rig feature this session reads it from — list_sessions_handler,
    get_user_bookings_handler, the push-notification/auto-complete schedulers). Old-shape
    (legacy slots) bookings are out of scope here, same as those other features."""
    booking = db_main.bookings.find_one({"rig_ids": rig_id, f"rig_sessions.{rig_id}.status": "Active"})
    if not booking:
        return None
    rs = (booking.get("rig_sessions") or {}).get(rig_id) or {}
    ends_at = rs.get("actual_end_at")
    if not ends_at:
        return None
    game = (booking.get("game") or "").strip()
    process_name = None
    if game:
        game_doc = db_main.game_images.find_one({"name_normalized": game.lower()}, {"process_name": 1})
        process_name = (game_doc or {}).get("process_name") or None
    return {"ends_at": ends_at, "game": game, "process_name": process_name}


def agent_poll_handler(rig_id, token):
    """Called every ~4s by the agent on the PC. Updates agent_last_seen (drives the
    admin UI's online/offline indicator), and returns both the rig's current active
    session (so the agent can maintain its own precise local close-the-game timer — see
    the feature plan) and the oldest still-pending one-off command (e.g. an admin's
    manual Shutdown click) for this rig, if any."""
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection is not established."}
    try:
        rig, error = _authenticate_agent(db_main, rig_id, token)
        if error:
            return error

        db_main.rigs.update_one({"_id": ObjectId(rig_id)}, {"$set": {"agent_last_seen": datetime.now(IST).isoformat()}})

        active_session = _resolve_active_session_for_rig(db_main, rig_id)
        command_doc = db_main.rig_commands.find_one(
            {"rig_id": rig_id, "status": "pending"}, sort=[("created_at", 1)]
        )
        command = None
        if command_doc:
            command = {"id": str(command_doc["_id"]), "type": command_doc.get("type")}

        return {"status": "success", "active_session": active_session, "command": command}
    except Exception as e:
        return {"status": "error", "message": f"Agent poll failed: {e}"}


def agent_ack_handler(rig_id, token, command_id, success):
    """The agent confirms it executed (or failed to execute) a command, so it's never
    re-delivered on the next poll."""
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection is not established."}
    try:
        rig, error = _authenticate_agent(db_main, rig_id, token)
        if error:
            return error
        if not ObjectId.is_valid(command_id):
            return {"status": "error", "message": "Invalid command id."}
        result = db_main.rig_commands.update_one(
            {"_id": ObjectId(command_id), "rig_id": rig_id},
            {"$set": {"status": "delivered" if success else "failed", "acked_at": datetime.now(IST)}},
        )
        if result.matched_count == 0:
            return {"status": "error", "message": "Command not found for this rig."}
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": f"Agent ack failed: {e}"}


def enqueue_rig_command_handler(rig_id, command_type):
    """Admin action (e.g. the Live Floor/Systems 'Shutdown' button) — queues a one-off
    command the agent picks up on its next poll. command_type is kept generic (just
    'shutdown' today) so a future command type needs no new plumbing here."""
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection is not established."}
    try:
        if not ObjectId.is_valid(rig_id):
            return {"status": "error", "message": "Invalid rig ID format."}
        rig = db_main.rigs.find_one({"_id": ObjectId(rig_id)})
        if not rig:
            return {"status": "error", "message": "Rig not found."}
        if command_type not in ("shutdown",):
            return {"status": "error", "message": f"Unsupported command type: {command_type}"}
        if not rig.get("agent_token"):
            return {"status": "error", "message": "This station has no agent paired yet — set one up first."}

        doc = {
            "rig_id": rig_id,
            "cafe_id": rig.get("cafe_id"),
            "type": command_type,
            "status": "pending",
            "created_at": datetime.now(IST),
        }
        result = db_main.rig_commands.insert_one(doc)
        return {"status": "success", "command_id": str(result.inserted_id)}
    except Exception as e:
        return {"status": "error", "message": f"Failed to queue command: {e}"}
